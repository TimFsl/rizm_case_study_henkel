"""Electrode-boiler investment screening, incremental to the optimized plant.

Business Case 2 does not redo Business Case 1. The counterfactual is the
optimized existing system: base demand, redispatch-only, the baseline annual
CHP total, the 50%/h CHP ramp, a 64 MW import cap, and a 10 MW export cap.
The only added asset is the electrode boiler. CAPEX stays outside the hourly
dispatch.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from src.baseline import dispatch_baseline
from src.costs import (
    commodity_electricity_prices,
    network_cost_breakdown,
    read_network_tariff,
    scenario_blended_fuel_price,
)
from src.investment import (
    capital_recovery_factor,
    carbon_prices_for_years,
    equivalent_annual_value,
    internal_rate_of_return,
    net_present_value,
    payback_label,
    payback_years,
    select_capacity_by_npv,
)
from src.market_prices import parse_smard_day_ahead, smard_csv_for_year
from src.optimization import (
    FULL_FLEX,
    REDISPATCH_ONLY,
    _chp_ramp_mw_per_hour,
    build_dispatch_model,
    finalize_dispatch,
)
from src.profiles import build_demand_profile_for_year

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw"
BINDING_TOLERANCE_MW = 1e-3
PRODUCTION_LABEL = "site-level screening value normalized by Henkel production"

BC1_OUTPUTS = (
    "data/processed/electricity_day_ahead_prices.csv",
    "data/processed/demand_profile_base.csv",
    "data/processed/demand_profile_flat.csv",
    "data/processed/demand_profile_variable.csv",
    "outputs/tables/optimization_summary.csv",
    "outputs/tables/optimization_network_tariff_summary.csv",
    "outputs/tables/dispatch_final_sensitivity.csv",
    "outputs/tables/dispatch_export_sensitivity.csv",
)


class ElectricBoilerError(ValueError):
    """Raised when the e-boiler screening case is inconsistent."""


def eboiler_electricity_break_even_price(
    assumptions: Any,
    blended_fuel_price_eur_mwh: float,
    eboiler_efficiency: float,
) -> float:
    """Electricity price at which e-boiler steam matches gas-boiler steam.

    This ignores CHP, the annual CHP constraint, peak charges, and grid caps.
    It is a QA reference for the hourly dispatch, not the investment result.
    """
    boiler_efficiency = float(assumptions.value("boiler", "thermal_efficiency"))
    eta = float(eboiler_efficiency)
    if boiler_efficiency <= 0.0 or eta <= 0.0:
        raise ElectricBoilerError("Boiler and e-boiler efficiencies must be positive")
    return float(blended_fuel_price_eur_mwh) / boiler_efficiency * eta


class ScreeningLibrary:
    """Cached demands, prices, and linear models for the e-boiler screen."""

    def __init__(self, assumptions: Any):
        self.assumptions = assumptions
        self.eta = float(assumptions.value("electric_boiler", "electrical_to_heat_efficiency"))
        self.import_cap = float(assumptions.value("grid", "import_capacity_mw"))
        self.export_cap = float(assumptions.value("grid", "export_capacity_mw"))
        self.ramp_fraction = float(
            assumptions.value("chp", "chp_ramp_fraction_of_heat_capacity_per_hour")
        )
        self.chp_max = float(assumptions.value("chp", "max_heat_output_mw"))
        self.ramp_mw = _chp_ramp_mw_per_hour(self.ramp_fraction, self.chp_max)
        self.fossil_share = float(assumptions.value("fuel_mix", "fossil_gas_share"))
        self.biomethane_share = float(assumptions.value("fuel_mix", "biomethane_share"))
        self.emission_factor = float(
            assumptions.value("emissions", "fossil_gas_emission_factor_tco2_per_mwh_fuel")
        )
        self.high_tariff = read_network_tariff(assumptions, "hv_high_utilization")
        self.low_tariff = read_network_tariff(assumptions, "hv_low_utilization")
        self._demand: dict[int, pd.DataFrame] = {}
        self._prices: dict[tuple[int, float], pd.DataFrame] = {}
        self._chp_target: dict[int, float] = {}
        self._models: dict[tuple, Any] = {}
        self._metrics: dict[tuple, dict[str, Any]] = {}
        self.solves = 0

    def demand(self, market_year: int) -> pd.DataFrame:
        year = int(market_year)
        if year not in self._demand:
            self._demand[year] = build_demand_profile_for_year(
                self.assumptions, "base", year
            )
        return self._demand[year]

    def prices(self, market_year: int, multiplier: float = 1.0) -> pd.DataFrame:
        year = int(market_year)
        factor = round(float(multiplier), 6)
        key = (year, factor)
        if key not in self._prices:
            parsed = parse_smard_day_ahead(smard_csv_for_year(RAW_DIR, year))
            demand_index = self.demand(year).index
            if len(parsed) != len(demand_index) or parsed.index.difference(demand_index).size:
                raise ElectricBoilerError(
                    f"{year} day-ahead timestamps do not match the demand-profile index"
                )
            frame = pd.DataFrame(
                {
                    "day_ahead_price_eur_mwh": parsed["day_ahead_price_eur_mwh"].to_numpy(
                        dtype=float
                    )
                    * factor
                },
                index=demand_index,
            )
            frame.index.name = "timestamp"
            self._prices[key] = commodity_electricity_prices(frame, self.assumptions)
        return self._prices[key]

    def chp_target_mwh(self, market_year: int) -> float:
        year = int(market_year)
        if year not in self._chp_target:
            dispatch = dispatch_baseline(self.demand(year), self.assumptions)
            self._chp_target[year] = float(dispatch["chp_electricity_mw"].sum())
        return self._chp_target[year]

    def _model(
        self,
        market_year: int,
        capacity_mw: float,
        mode: str,
        multiplier: float,
        tariff_name: str,
        zero_demand_charge: bool,
    ):
        key = (
            int(market_year),
            round(float(capacity_mw), 6),
            mode,
            round(float(multiplier), 6),
            tariff_name,
            bool(zero_demand_charge),
        )
        if key not in self._models:
            tariff = self.high_tariff if tariff_name == self.high_tariff.name else self.low_tariff
            demand = self.demand(market_year)
            prices = self.prices(market_year, multiplier)
            demand_charge = 0.0 if zero_demand_charge else tariff.demand_charge_eur_per_kw_a
            self._models[key] = build_dispatch_model(
                steam=demand["steam_demand_mw"],
                electricity=demand["electricity_demand_mw"],
                import_price=prices["electricity_import_price_eur_mwh"],
                export_price=prices["electricity_export_price_eur_mwh"],
                fuel_price=1.0,
                assumptions=self.assumptions,
                mode=mode,
                baseline_chp_electricity_mwh=(
                    self.chp_target_mwh(market_year) if mode == REDISPATCH_ONLY else None
                ),
                network_energy_charge_eur_per_mwh=tariff.energy_charge_eur_per_mwh,
                network_demand_charge_eur_per_kw_a=demand_charge,
                chp_ramp_mw_per_hour=self.ramp_mw,
                export_capacity_mw=self.export_cap,
                eboiler_capacity_mw=float(capacity_mw),
                eboiler_efficiency=self.eta,
            )
        return self._models[key]

    def _solve_tariff(
        self,
        market_year: int,
        capacity_mw: float,
        mode: str,
        multiplier: float,
        fuel_price: float,
        tariff,
        zero_demand_charge: bool,
    ) -> dict[str, Any]:
        model = self._model(
            market_year,
            capacity_mw,
            mode,
            multiplier,
            tariff.name,
            zero_demand_charge,
        )
        result = finalize_dispatch(
            model,
            demand=self.demand(market_year),
            prices=self.prices(market_year, multiplier),
            assumptions=self.assumptions,
            mode=mode,
            baseline_chp_electricity_mwh=(
                self.chp_target_mwh(market_year) if mode == REDISPATCH_ONLY else None
            ),
            network_tariff=tariff,
            chp_ramp_fraction_per_hour=self.ramp_fraction,
            ramp_mw=self.ramp_mw,
            export_capacity_mw=self.export_cap,
            eboiler_capacity_mw=float(capacity_mw),
            eboiler_efficiency=self.eta,
            fuel_price=float(fuel_price),
        )
        self.solves += 1
        frame = result.frame
        network = network_cost_breakdown(frame["grid_import_mw"], tariff)
        variable = float(frame["total_variable_energy_cost_eur"].sum())
        total_fuel = float(frame["total_fuel_mwh"].sum())
        eboiler_steam = (
            float(frame["eboiler_steam_mw"].sum()) if "eboiler_steam_mw" in frame.columns else 0.0
        )
        eboiler_electricity = (
            float(frame["eboiler_electricity_mw"].sum())
            if "eboiler_electricity_mw" in frame.columns
            else 0.0
        )
        return {
            "screening_cost_eur": variable
            + result.network_energy_cost_eur
            + result.network_demand_charge_eur,
            "variable_cost_eur": variable,
            "network_energy_eur": result.network_energy_cost_eur,
            "network_demand_eur": result.network_demand_charge_eur,
            "consistent": bool(network["consistent"]),
            "utilization_hours": network["utilization_hours"],
            "solved_tariff": tariff.name,
            "eboiler_steam_mwh": eboiler_steam,
            "eboiler_electricity_mwh": eboiler_electricity,
            "chp_steam_mwh": float(frame["chp_steam_mw"].sum()),
            "boiler_steam_mwh": float(frame["boiler_steam_mw"].sum()),
            "chp_electricity_mwh": float(frame["chp_electricity_mw"].sum()),
            "grid_import_mwh": float(frame["grid_import_mw"].sum()),
            "grid_export_mwh": float(frame["grid_export_mw"].sum()),
            "peak_import_mw": float(frame["grid_import_mw"].max()),
            "peak_export_mw": float(frame["grid_export_mw"].max()),
            "hours_import_binding": int(
                (frame["grid_import_mw"] >= self.import_cap - BINDING_TOLERANCE_MW).sum()
            ),
            "hours_export_binding": int(
                (frame["grid_export_mw"] >= self.export_cap - BINDING_TOLERANCE_MW).sum()
            ),
            "total_fuel_mwh": total_fuel,
            "fossil_fuel_mwh": total_fuel * self.fossil_share,
            "biomethane_mwh": total_fuel * self.biomethane_share,
            "frame": frame,
            **operating_price_stats(frame, float(capacity_mw)),
        }

    def regime_case(
        self,
        market_year: int,
        capacity_mw: float,
        mode: str,
        multiplier: float,
        fuel_price: float,
        *,
        zero_demand_charge: bool = False,
        keep_frame: bool = False,
    ) -> dict[str, Any]:
        """Solve the high-utilization tariff, then the low tariff only if needed.

        The two Düsseldorf regimes are mutually exclusive at the 2,500 h
        threshold, so a consistent high-utilization result is the only
        consistent regime. Both are solved when the high result is inconsistent.
        """
        key = (
            int(market_year),
            round(float(capacity_mw), 6),
            mode,
            round(float(multiplier), 6),
            round(float(fuel_price), 6),
            bool(zero_demand_charge),
        )
        cached = self._metrics.get(key)
        if cached is not None and not keep_frame:
            return cached
        high = self._solve_tariff(
            market_year,
            capacity_mw,
            mode,
            multiplier,
            fuel_price,
            self.high_tariff,
            zero_demand_charge,
        )
        low = None
        if high["consistent"]:
            chosen = high
            regime = self.high_tariff.name
        else:
            low = self._solve_tariff(
                market_year,
                capacity_mw,
                mode,
                multiplier,
                fuel_price,
                self.low_tariff,
                zero_demand_charge,
            )
            if low["consistent"]:
                chosen = low
                regime = self.low_tariff.name
            else:
                chosen = high
                regime = "inconsistent"
        record = {key_name: value for key_name, value in chosen.items() if key_name != "frame"}
        record["tariff_regime"] = regime
        record["high_consistent"] = bool(high["consistent"])
        record["low_consistent"] = None if low is None else bool(low["consistent"])
        record["ambiguous"] = regime == "inconsistent"
        self._metrics[key] = record
        if keep_frame:
            return {**record, "frame": chosen["frame"]}
        return record


def configured_capacities_mw(assumptions: Any) -> list[float]:
    values = assumptions.value("electric_boiler", "screened_capacities_mwth")
    capacities = [float(value) for value in values]
    if 0.0 not in capacities:
        raise ElectricBoilerError("The screened capacities must include 0 MW as the counterfactual")
    return capacities


def configured_market_years(assumptions: Any) -> list[int]:
    return [int(year) for year in assumptions.value("electric_boiler", "market_price_years")]


def carbon_path(assumptions: Any, name: str, years: list[int]) -> dict[int, float]:
    paths = assumptions.section("electric_boiler")["carbon_price_paths"]
    if name not in paths:
        known = ", ".join(str(item) for item in paths)
        raise ElectricBoilerError(f"Unknown carbon path {name!r}. Known paths: {known}")
    return carbon_prices_for_years(paths[name], years)


def primary_fossil_pre_carbon(assumptions: Any) -> float:
    commodity = float(assumptions.value("market", "natural_gas_commodity_eur_per_mwh"))
    adder = float(assumptions.value("market", "gas_variable_adder_eur_per_mwh"))
    documented = float(assumptions.value("electric_boiler", "fossil_pre_carbon_eur_per_mwh"))
    actual = commodity + adder
    if abs(actual - documented) > 1e-6:
        raise ElectricBoilerError(
            "The documented pre-carbon fossil-gas price does not match "
            f"commodity plus adder: documented {documented:g}, actual {actual:g}"
        )
    return actual


def primary_biomethane_price(assumptions: Any) -> float:
    commodity = float(assumptions.value("market", "natural_gas_commodity_eur_per_mwh"))
    adder = float(assumptions.value("market", "gas_variable_adder_eur_per_mwh"))
    premium = float(assumptions.value("market", "biomethane_premium_eur_per_mwh"))
    documented = float(assumptions.value("electric_boiler", "biomethane_eur_per_mwh"))
    actual = commodity + adder + premium
    if abs(actual - documented) > 1e-6:
        raise ElectricBoilerError(
            "The documented biomethane price does not match commodity plus adder "
            f"plus premium: documented {documented:g}, actual {actual:g}"
        )
    return actual


def _fuel_price(
    assumptions: Any,
    carbon_price: float,
    fossil_pre_carbon: float | None,
    biomethane_price: float | None,
) -> float:
    return scenario_blended_fuel_price(
        assumptions,
        carbon_price,
        fossil_pre_carbon_eur_per_mwh=fossil_pre_carbon,
        biomethane_eur_per_mwh=biomethane_price,
    )


def operating_price_stats(frame: pd.DataFrame, capacity_mw: float) -> dict[str, float]:
    """How the electrode boiler uses cheap hours. Zeros when it does not run."""
    zeros = {
        "operating_hours": 0.0,
        "hours_above_25": 0.0,
        "hours_above_50": 0.0,
        "hours_above_90": 0.0,
        "operating_price_sum": 0.0,
        "operating_price_median": 0.0,
        "elec_price_product": 0.0,
        "elec_negative_mwh": 0.0,
        "elec_below_25_mwh": 0.0,
        "elec_below_50_mwh": 0.0,
    }
    if capacity_mw <= 0.0 or "eboiler_steam_mw" not in frame.columns:
        return zeros
    steam = frame["eboiler_steam_mw"].astype(float)
    electricity = frame["eboiler_electricity_mw"].astype(float)
    price = frame["day_ahead_price_eur_mwh"].astype(float)
    operating = steam > 1e-3
    if not bool(operating.any()):
        return zeros
    zeros["operating_hours"] = float(operating.sum())
    zeros["hours_above_25"] = float((steam > 0.25 * capacity_mw).sum())
    zeros["hours_above_50"] = float((steam > 0.50 * capacity_mw).sum())
    zeros["hours_above_90"] = float((steam > 0.90 * capacity_mw).sum())
    operating_prices = price[operating]
    zeros["operating_price_sum"] = float(operating_prices.sum())
    zeros["operating_price_median"] = float(operating_prices.median())
    zeros["elec_price_product"] = float((price * electricity).sum())
    zeros["elec_negative_mwh"] = float(electricity[price < 0.0].sum())
    zeros["elec_below_25_mwh"] = float(electricity[price < 25.0].sum())
    zeros["elec_below_50_mwh"] = float(electricity[price < 50.0].sum())
    return zeros


def _median_operating_price(rows: list[dict[str, Any]]) -> float:
    medians = [
        float(row["operating_price_median"])
        for row in rows
        if float(row.get("operating_hours", 0.0)) > 0.0
    ]
    if not medians:
        return 0.0
    medians.sort()
    mid = len(medians) // 2
    if len(medians) % 2:
        return medians[mid]
    return 0.5 * (medians[mid - 1] + medians[mid])


def ensemble_benefit(
    library: ScreeningLibrary,
    investment_year: int,
    capacity_mw: float,
    carbon_price: float,
    *,
    mode: str = REDISPATCH_ONLY,
    multiplier: float = 1.0,
    fossil_pre_carbon: float | None = None,
    biomethane_price: float | None = None,
    zero_demand_charge: bool = False,
    market_years: list[int] | None = None,
    keep_frame_for: int | None = None,
) -> dict[str, Any]:
    """Mean operating benefit across the observed market-year shapes."""
    years = market_years or configured_market_years(library.assumptions)
    fuel = _fuel_price(
        library.assumptions, carbon_price, fossil_pre_carbon, biomethane_price
    )
    details = []
    for market_year in years:
        without = library.regime_case(
            market_year,
            0.0,
            mode,
            multiplier,
            fuel,
            zero_demand_charge=zero_demand_charge,
        )
        with_boiler = library.regime_case(
            market_year,
            capacity_mw,
            mode,
            multiplier,
            fuel,
            zero_demand_charge=zero_demand_charge,
            keep_frame=keep_frame_for == market_year,
        )
        benefit = without["screening_cost_eur"] - with_boiler["screening_cost_eur"]
        details.append(
            {
                "investment_year": int(investment_year),
                "price_shape_year": int(market_year),
                "gross_operating_benefit_eur": benefit,
                "with_boiler": with_boiler,
                "without_boiler": without,
            }
        )
    return {
        "fuel_price_eur_mwh": fuel,
        "gross_operating_benefit_eur": sum(row["gross_operating_benefit_eur"] for row in details)
        / len(details),
        "details": details,
    }


def _mean(rows: list[dict[str, Any]], key: str) -> float:
    return sum(float(row[key]) for row in rows) / len(rows)


def investment_cashflows(
    capex_eur: float,
    annual_net_eur: list[float],
    discount_rate: float,
) -> dict[str, Any]:
    vector = [-float(capex_eur), *[float(value) for value in annual_net_eur]]
    years = len(annual_net_eur)
    npv = net_present_value(discount_rate, vector)
    irr = internal_rate_of_return(vector)
    simple = payback_years(vector, 0.0)
    discounted = payback_years(vector, discount_rate)
    eav = equivalent_annual_value(npv, discount_rate, years)
    return {
        "cashflows": vector,
        "npv_eur": npv,
        "irr": irr,
        "simple_payback_years": simple,
        "discounted_payback_years": discounted,
        "equivalent_annual_value_eur": eav,
        "capital_recovery_factor": capital_recovery_factor(discount_rate, years),
    }


def _regime_label(regimes: list[str]) -> str:
    unique = sorted(set(regimes))
    if len(unique) == 1:
        return unique[0]
    return "mixed"


def build_primary_tables(
    library: ScreeningLibrary,
    mode: str = REDISPATCH_ONLY,
) -> dict[str, Any]:
    """Size the e-boiler against the same optimization mode without a boiler."""
    assumptions = library.assumptions
    capacities = configured_capacities_mw(assumptions)
    market_years = configured_market_years(assumptions)
    first_year = int(assumptions.value("electric_boiler", "first_operating_year"))
    lifetime = int(assumptions.value("electric_boiler", "lifetime_years"))
    operating_years = list(range(first_year, first_year + lifetime))
    path_name = str(assumptions.value("electric_boiler", "primary_carbon_path"))
    carbon = carbon_path(assumptions, path_name, operating_years)
    discount_rate = float(assumptions.value("electric_boiler", "real_discount_rate"))
    capex_rate = float(assumptions.value("electric_boiler", "capex_eur_per_mwth"))
    om_fraction = float(assumptions.value("electric_boiler", "fixed_om_fraction_of_capex"))
    production = float(assumptions.value("site", "annual_henkel_production_tonnes"))
    if assumptions.value("electric_boiler", "external_grid_expansion") is not False:
        raise ElectricBoilerError("The primary case does not expand the external grid connection")

    print(
        f"{mode} screen: {len(capacities)} sizes, {len(operating_years)} years, "
        f"{len(market_years)} market shapes",
        flush=True,
    )
    by_capacity: dict[float, list[dict[str, Any]]] = {}
    market_rows: list[dict[str, Any]] = []
    for capacity in capacities:
        year_rows = []
        for year in operating_years:
            ensemble = ensemble_benefit(
                library,
                year,
                capacity,
                carbon[year],
                mode=mode,
                market_years=market_years,
            )
            for detail in ensemble["details"]:
                with_boiler = detail["with_boiler"]
                with_boiler.pop("frame", None)
                without = detail["without_boiler"]
                market_rows.append(
                    {
                        "eboiler_capacity_mwth": capacity,
                        "investment_year": year,
                        "price_shape_year": detail["price_shape_year"],
                        "eua_price_eur_per_tco2": carbon[year],
                        "gross_operating_benefit_eur": detail["gross_operating_benefit_eur"],
                        "eboiler_steam_gwh": with_boiler["eboiler_steam_mwh"] / 1000.0,
                        "eboiler_electricity_gwh": with_boiler["eboiler_electricity_mwh"] / 1000.0,
                        "grid_import_gwh": with_boiler["grid_import_mwh"] / 1000.0,
                        "grid_export_gwh": with_boiler["grid_export_mwh"] / 1000.0,
                        "total_fuel_gwh": with_boiler["total_fuel_mwh"] / 1000.0,
                        "peak_grid_import_mw": with_boiler["peak_import_mw"],
                        "tariff_regime": with_boiler["tariff_regime"],
                        "counterfactual_tariff_regime": without["tariff_regime"],
                        "counterfactual_screening_cost_eur": without["screening_cost_eur"],
                        "eboiler_screening_cost_eur": with_boiler["screening_cost_eur"],
                    }
                )
            physical = [detail["with_boiler"] for detail in ensemble["details"]]
            counterfactual = [detail["without_boiler"] for detail in ensemble["details"]]
            year_rows.append(
                {
                    "year": year,
                    "carbon": carbon[year],
                    "gross": ensemble["gross_operating_benefit_eur"],
                    "physical": physical,
                    "counterfactual": counterfactual,
                }
            )
            print(
                f"  {capacity:g} MW, {year}: benefit "
                f"{ensemble['gross_operating_benefit_eur'] / 1e6:.3f} M EUR "
                f"({library.solves} solves)",
                flush=True,
            )
        by_capacity[capacity] = year_rows

    economics = {
        capacity: _economics_for_capacity(
            capacity,
            rows,
            capex_rate=capex_rate,
            om_fraction=om_fraction,
            discount_rate=discount_rate,
            production_tonnes=production,
            emission_factor=library.emission_factor,
            eta=library.eta,
        )
        for capacity, rows in by_capacity.items()
    }
    k_star = select_capacity_by_npv(
        {capacity: row["npv_eur"] for capacity, row in economics.items()}
    )
    week_frame = _capture_week(
        library,
        k_star,
        first_year,
        carbon[first_year],
        market_years,
        mode=mode,
    )
    return {
        "capacities": capacities,
        "operating_years": operating_years,
        "carbon": carbon,
        "by_capacity": by_capacity,
        "economics": economics,
        "k_star": k_star,
        "market_rows": market_rows,
        "week_frame": week_frame,
        "discount_rate": discount_rate,
        "capex_rate": capex_rate,
        "om_fraction": om_fraction,
        "production_tonnes": production,
        "path_name": path_name,
        "lifetime": lifetime,
        "first_year": first_year,
        "mode": mode,
    }


def _capture_week(
    library,
    capacity,
    year,
    carbon_price,
    market_years,
    mode: str = REDISPATCH_ONLY,
):
    if capacity <= 0.0:
        return None
    ensemble = ensemble_benefit(
        library,
        year,
        capacity,
        carbon_price,
        mode=mode,
        market_years=market_years,
        keep_frame_for=2025,
    )
    for detail in ensemble["details"]:
        frame = detail["with_boiler"].pop("frame", None)
        if detail["price_shape_year"] == 2025 and frame is not None:
            return frame
    return None


def _economics_for_capacity(
    capacity: float,
    year_rows: list[dict[str, Any]],
    *,
    capex_rate: float,
    om_fraction: float,
    discount_rate: float,
    production_tonnes: float,
    emission_factor: float,
    eta: float,
) -> dict[str, Any]:
    capex = float(capacity) * float(capex_rate)
    fixed_opex = om_fraction * capex
    annual_gross = [float(row["gross"]) for row in year_rows]
    annual_net = [gross - fixed_opex for gross in annual_gross]
    metrics = investment_cashflows(capex, annual_net, discount_rate)
    physical_rows = [item for row in year_rows for item in row["physical"]]
    counterfactual_rows = [item for row in year_rows for item in row["counterfactual"]]
    steam_mwh = _mean(physical_rows, "eboiler_steam_mwh")
    fossil_with = _mean(physical_rows, "fossil_fuel_mwh")
    fossil_without = _mean(counterfactual_rows, "fossil_fuel_mwh")
    avoided = (fossil_without - fossil_with) * emission_factor
    full_load = None if capacity <= 0.0 else steam_mwh / capacity
    return {
        "eboiler_capacity_mwth": capacity,
        "eboiler_electrical_capacity_equivalent_mw": 0.0 if capacity <= 0 else capacity / eta,
        "capex_eur": capex,
        "fixed_opex_eur": fixed_opex,
        "year1_gross_eur": annual_gross[0],
        "average_gross_eur": sum(annual_gross) / len(annual_gross),
        "average_net_eur": sum(annual_net) / len(annual_net),
        "annual_gross": annual_gross,
        "annual_net": annual_net,
        "npv_eur": metrics["npv_eur"],
        "irr": metrics["irr"],
        "simple_payback_years": payback_label(
            metrics["simple_payback_years"], capex_eur=capex
        ),
        "discounted_payback_years": payback_label(
            metrics["discounted_payback_years"], capex_eur=capex
        ),
        "equivalent_annual_value_eur": metrics["equivalent_annual_value_eur"],
        "eav_eur_per_t": metrics["equivalent_annual_value_eur"] / production_tonnes,
        "year1_gross_eur_per_t": annual_gross[0] / production_tonnes,
        "average_gross_eur_per_t": (sum(annual_gross) / len(annual_gross)) / production_tonnes,
        "annual_eboiler_steam_mwh": steam_mwh,
        "annual_eboiler_electricity_mwh": _mean(physical_rows, "eboiler_electricity_mwh"),
        "eboiler_full_load_hours": full_load,
        "annual_chp_steam_mwh": _mean(physical_rows, "chp_steam_mwh"),
        "annual_boiler_steam_mwh": _mean(physical_rows, "boiler_steam_mwh"),
        "annual_chp_electricity_mwh": _mean(physical_rows, "chp_electricity_mwh"),
        "annual_grid_import_mwh": _mean(physical_rows, "grid_import_mwh"),
        "annual_grid_export_mwh": _mean(physical_rows, "grid_export_mwh"),
        "peak_grid_import_mw": _mean(physical_rows, "peak_import_mw"),
        "peak_grid_export_mw": _mean(physical_rows, "peak_export_mw"),
        "hours_grid_import_capacity_binding": _mean(physical_rows, "hours_import_binding"),
        "hours_export_capacity_binding": _mean(physical_rows, "hours_export_binding"),
        "annual_total_fuel_mwh": _mean(physical_rows, "total_fuel_mwh"),
        "annual_fossil_fuel_mwh": fossil_with,
        "annual_biomethane_mwh": _mean(physical_rows, "biomethane_mwh"),
        "avoided_fossil_combustion_tco2": avoided,
        "network_energy_eur": _mean(physical_rows, "network_energy_eur"),
        "network_demand_eur": _mean(physical_rows, "network_demand_eur"),
        "counterfactual_network_energy_eur": _mean(counterfactual_rows, "network_energy_eur"),
        "counterfactual_network_demand_eur": _mean(counterfactual_rows, "network_demand_eur"),
        "counterfactual_boiler_steam_mwh": _mean(counterfactual_rows, "boiler_steam_mwh"),
        "counterfactual_chp_steam_mwh": _mean(counterfactual_rows, "chp_steam_mwh"),
        "counterfactual_chp_electricity_mwh": _mean(counterfactual_rows, "chp_electricity_mwh"),
        "counterfactual_grid_import_mwh": _mean(counterfactual_rows, "grid_import_mwh"),
        "counterfactual_grid_export_mwh": _mean(counterfactual_rows, "grid_export_mwh"),
        "counterfactual_fossil_fuel_mwh": fossil_without,
        "operating_hours": _mean(physical_rows, "operating_hours"),
        "hours_above_25": _mean(physical_rows, "hours_above_25"),
        "hours_above_50": _mean(physical_rows, "hours_above_50"),
        "hours_above_90": _mean(physical_rows, "hours_above_90"),
        "operating_price_sum": _mean(physical_rows, "operating_price_sum"),
        "operating_price_median": _median_operating_price(physical_rows),
        "elec_price_product": _mean(physical_rows, "elec_price_product"),
        "elec_negative_mwh": _mean(physical_rows, "elec_negative_mwh"),
        "elec_below_25_mwh": _mean(physical_rows, "elec_below_25_mwh"),
        "elec_below_50_mwh": _mean(physical_rows, "elec_below_50_mwh"),
        "tariff_regime": _regime_label([row["tariff_regime"] for row in physical_rows]),
        "counterfactual_tariff_regime": _regime_label(
            [row["tariff_regime"] for row in counterfactual_rows]
        ),
        "eur_per_t_basis": PRODUCTION_LABEL,
        "year_rows": year_rows,
    }


def sizing_frame(economics: dict[float, dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for capacity in sorted(economics):
        row = economics[capacity]
        rows.append(
            {
                "eboiler_capacity_mwth": capacity,
                "eboiler_electrical_capacity_equivalent_mw": row[
                    "eboiler_electrical_capacity_equivalent_mw"
                ],
                "capex_m_eur": row["capex_eur"] / 1e6,
                "fixed_opex_m_eur_a": row["fixed_opex_eur"] / 1e6,
                "year1_gross_operating_benefit_m_eur": row["year1_gross_eur"] / 1e6,
                "average_gross_operating_benefit_m_eur_a": row["average_gross_eur"] / 1e6,
                "average_net_cashflow_m_eur_a": row["average_net_eur"] / 1e6,
                "year1_gross_operating_benefit_eur_per_t": row["year1_gross_eur_per_t"],
                "average_gross_operating_benefit_eur_per_t": row["average_gross_eur_per_t"],
                "npv_m_eur": row["npv_eur"] / 1e6,
                "irr_percent": "undefined" if row["irr"] is None else 100.0 * row["irr"],
                "simple_payback_years": row["simple_payback_years"],
                "discounted_payback_years": row["discounted_payback_years"],
                "equivalent_annual_value_m_eur": row["equivalent_annual_value_eur"] / 1e6,
                "eav_eur_per_t": row["eav_eur_per_t"],
                "eur_per_t_basis": row["eur_per_t_basis"],
                "annual_eboiler_steam_gwh": row["annual_eboiler_steam_mwh"] / 1000.0,
                "annual_eboiler_electricity_gwh": row["annual_eboiler_electricity_mwh"] / 1000.0,
                "eboiler_full_load_hours": row["eboiler_full_load_hours"],
                "annual_chp_steam_gwh": row["annual_chp_steam_mwh"] / 1000.0,
                "annual_boiler_steam_gwh": row["annual_boiler_steam_mwh"] / 1000.0,
                "annual_chp_electricity_gwh": row["annual_chp_electricity_mwh"] / 1000.0,
                "annual_grid_import_gwh": row["annual_grid_import_mwh"] / 1000.0,
                "annual_grid_export_gwh": row["annual_grid_export_mwh"] / 1000.0,
                "peak_grid_import_mw": row["peak_grid_import_mw"],
                "peak_grid_export_mw": row["peak_grid_export_mw"],
                "hours_grid_import_capacity_binding": row["hours_grid_import_capacity_binding"],
                "hours_export_capacity_binding": row["hours_export_capacity_binding"],
                "annual_total_fuel_gwh": row["annual_total_fuel_mwh"] / 1000.0,
                "annual_fossil_fuel_gwh": row["annual_fossil_fuel_mwh"] / 1000.0,
                "annual_biomethane_gwh": row["annual_biomethane_mwh"] / 1000.0,
                "avoided_fossil_combustion_tco2": row["avoided_fossil_combustion_tco2"],
                "tariff_regime": row["tariff_regime"],
                "counterfactual_tariff_regime": row["counterfactual_tariff_regime"],
            }
        )
    return pd.DataFrame(rows)


def full_flex_sizing_frame(economics: dict[float, dict[str, Any]]) -> pd.DataFrame:
    """Investment table for the full-flex electrode-boiler screen."""
    rows = []
    for capacity in sorted(economics):
        row = economics[capacity]
        network = row["network_energy_eur"] + row["network_demand_eur"]
        rows.append(
            {
                "capacity_mwth": capacity,
                "capex_m_eur": row["capex_eur"] / 1e6,
                "fixed_opex_m_eur_a": row["fixed_opex_eur"] / 1e6,
                "average_gross_operating_benefit_m_eur_a": row["average_gross_eur"] / 1e6,
                "average_net_cashflow_m_eur_a": row["average_net_eur"] / 1e6,
                "npv_m_eur": row["npv_eur"] / 1e6,
                "irr_percent": "undefined" if row["irr"] is None else 100.0 * row["irr"],
                "simple_payback_years": row["simple_payback_years"],
                "discounted_payback_years": row["discounted_payback_years"],
                "eav_m_eur": row["equivalent_annual_value_eur"] / 1e6,
                "eav_eur_per_t": row["eav_eur_per_t"],
                "eboiler_steam_gwh": row["annual_eboiler_steam_mwh"] / 1000.0,
                "eboiler_electricity_gwh": row["annual_eboiler_electricity_mwh"] / 1000.0,
                "full_load_hours": row["eboiler_full_load_hours"],
                "chp_electricity_gwh": row["annual_chp_electricity_mwh"] / 1000.0,
                "chp_steam_gwh": row["annual_chp_steam_mwh"] / 1000.0,
                "gas_boiler_steam_gwh": row["annual_boiler_steam_mwh"] / 1000.0,
                "grid_import_gwh": row["annual_grid_import_mwh"] / 1000.0,
                "grid_export_gwh": row["annual_grid_export_mwh"] / 1000.0,
                "peak_import_mw": row["peak_grid_import_mw"],
                "peak_export_mw": row["peak_grid_export_mw"],
                "hours_import_cap_binding": row["hours_grid_import_capacity_binding"],
                "hours_export_cap_binding": row["hours_export_capacity_binding"],
                "network_energy_charge_m_eur": row["network_energy_eur"] / 1e6,
                "network_demand_charge_m_eur": row["network_demand_eur"] / 1e6,
                "network_cost_m_eur": network / 1e6,
                "tariff_regime": row["tariff_regime"],
                "counterfactual_tariff_regime": row["counterfactual_tariff_regime"],
                "total_fuel_gwh": row["annual_total_fuel_mwh"] / 1000.0,
                "fossil_fuel_gwh": row["annual_fossil_fuel_mwh"] / 1000.0,
                "biomethane_gwh": row["annual_biomethane_mwh"] / 1000.0,
                "avoided_direct_fossil_co2_t": row["avoided_fossil_combustion_tco2"],
            }
        )
    return pd.DataFrame(rows)


def framework_comparison_frame(
    redispatch: pd.DataFrame,
    full_flex: pd.DataFrame,
) -> pd.DataFrame:
    """Place the constrained CHP case beside the full-flex investment case."""
    left = redispatch.set_index("eboiler_capacity_mwth")
    right = full_flex.set_index("capacity_mwth")
    rows = []
    for capacity in right.index:
        constrained = left.loc[capacity]
        flexible = right.loc[capacity]
        rows.append(
            {
                "capacity_mwth": float(capacity),
                "redispatch_only_npv_m_eur": constrained["npv_m_eur"],
                "full_flex_npv_m_eur": flexible["npv_m_eur"],
                "redispatch_only_eav_eur_per_t": constrained["eav_eur_per_t"],
                "full_flex_eav_eur_per_t": flexible["eav_eur_per_t"],
                "redispatch_only_annual_gross_benefit_m_eur": constrained[
                    "average_gross_operating_benefit_m_eur_a"
                ],
                "full_flex_annual_gross_benefit_m_eur": flexible[
                    "average_gross_operating_benefit_m_eur_a"
                ],
                "redispatch_only_eboiler_steam_gwh": constrained["annual_eboiler_steam_gwh"],
                "full_flex_eboiler_steam_gwh": flexible["eboiler_steam_gwh"],
                "redispatch_only_full_load_hours": constrained["eboiler_full_load_hours"],
                "full_flex_full_load_hours": flexible["full_load_hours"],
            }
        )
    return pd.DataFrame(rows)


def cashflow_frame(primary: dict[str, Any]) -> pd.DataFrame:
    rows = []
    rate = primary["discount_rate"]
    for capacity, economics in primary["economics"].items():
        capex = economics["capex_eur"]
        cumulative = -capex
        cumulative_discounted = -capex
        rows.append(
            {
                "year": 0,
                "capacity_mwth": capacity,
                "eua_price_eur_per_tco2": None,
                "mean_gross_operating_benefit_eur": 0.0,
                "fixed_opex_eur": 0.0,
                "net_cashflow_eur": -capex,
                "discount_factor": 1.0,
                "discounted_cashflow_eur": -capex,
                "cumulative_cashflow_eur": cumulative,
                "cumulative_discounted_cashflow_eur": cumulative_discounted,
            }
        )
        for index, year_row in enumerate(economics["year_rows"], start=1):
            gross = float(year_row["gross"])
            fixed = economics["fixed_opex_eur"]
            net = gross - fixed
            factor = 1.0 / (1.0 + rate) ** index
            discounted = net * factor
            cumulative += net
            cumulative_discounted += discounted
            rows.append(
                {
                    "year": int(year_row["year"]),
                    "capacity_mwth": capacity,
                    "eua_price_eur_per_tco2": year_row["carbon"],
                    "mean_gross_operating_benefit_eur": gross,
                    "fixed_opex_eur": fixed,
                    "net_cashflow_eur": net,
                    "discount_factor": factor,
                    "discounted_cashflow_eur": discounted,
                    "cumulative_cashflow_eur": cumulative,
                    "cumulative_discounted_cashflow_eur": cumulative_discounted,
                }
            )
    return pd.DataFrame(rows)


def _benefit_path_for_capacity(
    library: ScreeningLibrary,
    capacity: float,
    years: list[int],
    carbon: dict[int, float],
    **kwargs,
) -> list[float]:
    benefits = []
    for year in years:
        ensemble = ensemble_benefit(
            library,
            year,
            capacity,
            carbon[year],
            **kwargs,
        )
        for detail in ensemble["details"]:
            detail["with_boiler"].pop("frame", None)
            detail["without_boiler"].pop("frame", None)
        benefits.append(ensemble["gross_operating_benefit_eur"])
    return benefits


def run_selected_sensitivities(library: ScreeningLibrary, primary: dict[str, Any]) -> pd.DataFrame:
    """One-way sensitivities at K*. CAPEX, discount rate, and life do not re-solve."""
    assumptions = library.assumptions
    capacity = float(primary["k_star"])
    production = primary["production_tonnes"]
    base_rate = primary["discount_rate"]
    base_life = primary["lifetime"]
    base_capex_rate = primary["capex_rate"]
    om_fraction = primary["om_fraction"]
    first_year = primary["first_year"]
    base_gross = primary["economics"][capacity]["annual_gross"]
    mode = str(primary.get("mode", REDISPATCH_ONLY))
    rows = []

    def add_row(name: str, case: str, gross: list[float], capex_rate: float, rate: float):
        capex = capacity * capex_rate
        fixed = om_fraction * capex
        net = [value - fixed for value in gross]
        metrics = investment_cashflows(capex, net, rate)
        rows.append(
            {
                "sensitivity_name": name,
                "case": case,
                "npv_m_eur": metrics["npv_eur"] / 1e6,
                "eav_eur_per_t": metrics["equivalent_annual_value_eur"] / production,
                "irr_percent": "undefined" if metrics["irr"] is None else 100.0 * metrics["irr"],
                "simple_payback_years": payback_label(
                    metrics["simple_payback_years"], capex_eur=capex
                ),
                "discounted_payback_years": payback_label(
                    metrics["discounted_payback_years"], capex_eur=capex
                ),
            }
        )

    for capex_rate in assumptions.parameter("electric_boiler", "capex_eur_per_mwth")["sensitivity"]:
        add_row("capex_eur_per_mwth", f"{float(capex_rate):.0f}", base_gross, float(capex_rate), base_rate)

    for rate in assumptions.parameter("electric_boiler", "real_discount_rate")["sensitivity"]:
        add_row("real_discount_rate", f"{float(rate):.2f}", base_gross, base_capex_rate, float(rate))

    life_values = [
        int(value)
        for value in assumptions.parameter("electric_boiler", "lifetime_years")["sensitivity"]
    ]
    longest = max(life_values)
    life_years = list(range(first_year, first_year + longest))
    life_carbon = carbon_path(assumptions, primary["path_name"], life_years)
    if longest > len(base_gross):
        extra_years = life_years[len(base_gross) :]
        extra = _benefit_path_for_capacity(
            library, capacity, extra_years, life_carbon, mode=mode
        )
        full_gross = list(base_gross) + extra
    else:
        full_gross = list(base_gross)
    for life in life_values:
        add_row("lifetime_years", str(life), full_gross[:life], base_capex_rate, base_rate)

    horizon = list(range(first_year, first_year + base_life))
    for path_name in ("low", "base", "high"):
        if path_name == primary["path_name"]:
            gross = base_gross
        else:
            carbon = carbon_path(assumptions, path_name, horizon)
            print(f"Sensitivity EUA path {path_name}", flush=True)
            gross = _benefit_path_for_capacity(
                library, capacity, horizon, carbon, mode=mode
            )
        add_row("eua_path", path_name, gross, base_capex_rate, base_rate)

    documented_bio = primary_biomethane_price(assumptions)
    for price in assumptions.parameter("electric_boiler", "biomethane_eur_per_mwh")["sensitivity"]:
        price = float(price)
        if abs(price - documented_bio) < 1e-6:
            gross = base_gross
        else:
            carbon = carbon_path(assumptions, primary["path_name"], horizon)
            print(f"Sensitivity biomethane {price:g}", flush=True)
            gross = _benefit_path_for_capacity(
                library, capacity, horizon, carbon, mode=mode, biomethane_price=price
            )
        add_row("biomethane_eur_per_mwh", f"{price:.0f}", gross, base_capex_rate, base_rate)

    documented_fossil = primary_fossil_pre_carbon(assumptions)
    for price in assumptions.parameter("electric_boiler", "fossil_pre_carbon_eur_per_mwh")[
        "sensitivity"
    ]:
        price = float(price)
        if abs(price - documented_fossil) < 1e-6:
            gross = base_gross
        else:
            carbon = carbon_path(assumptions, primary["path_name"], horizon)
            print(f"Sensitivity fossil pre-carbon {price:g}", flush=True)
            gross = _benefit_path_for_capacity(
                library, capacity, horizon, carbon, mode=mode, fossil_pre_carbon=price
            )
        add_row("fossil_pre_carbon_eur_per_mwh", f"{price:.0f}", gross, base_capex_rate, base_rate)

    for multiplier in assumptions.parameter("electric_boiler", "electricity_price_multiplier")[
        "sensitivity"
    ]:
        multiplier = float(multiplier)
        if abs(multiplier - 1.0) < 1e-9:
            gross = base_gross
        else:
            carbon = carbon_path(assumptions, primary["path_name"], horizon)
            print(f"Sensitivity electricity multiplier {multiplier:g}", flush=True)
            gross = _benefit_path_for_capacity(
                library, capacity, horizon, carbon, mode=mode, multiplier=multiplier
            )
        add_row(
            "electricity_price_multiplier",
            f"{multiplier:.1f}",
            gross,
            base_capex_rate,
            base_rate,
        )

    carbon = carbon_path(assumptions, primary["path_name"], horizon)
    print("Sensitivity network demand charge set to zero", flush=True)
    gross = _benefit_path_for_capacity(
        library, capacity, horizon, carbon, mode=mode, zero_demand_charge=True
    )
    add_row("network_demand_charge", "included", base_gross, base_capex_rate, base_rate)
    add_row("network_demand_charge", "zero", gross, base_capex_rate, base_rate)

    if mode == REDISPATCH_ONLY:
        print("Secondary full-flex case", flush=True)
        flex_gross = _benefit_path_for_capacity(
            library, capacity, horizon, carbon, mode=FULL_FLEX
        )
        add_row("structural_flexibility", "redispatch_only", base_gross, base_capex_rate, base_rate)
        add_row(
            "structural_flexibility",
            "full_flex_secondary_upside",
            flex_gross,
            base_capex_rate,
            base_rate,
        )
    return pd.DataFrame(rows)


def extend_carbon_years_note() -> str:
    return (
        "The investment model does not claim to predict individual future hourly "
        "electricity prices. Three observed market years are reused as an empirical "
        "hourly market ensemble, while structural long-term uncertainty is treated "
        "through explicit fuel, carbon, and cost sensitivities."
    )


def structural_flexibility_snapshot(
    library: ScreeningLibrary,
    primary: dict[str, Any],
) -> dict[str, float | str]:
    """Cached full-flex upside at K*, after the primary redispatch sizing."""
    capacity = float(primary["k_star"])
    if capacity <= 0.0:
        return {"capacity_mwth": capacity, "note": "K* is zero, so no upside case is sized"}
    flex_rows = []
    base_rows = []
    for year in primary["operating_years"]:
        flex = ensemble_benefit(
            library,
            year,
            capacity,
            primary["carbon"][year],
            mode=FULL_FLEX,
        )
        base = ensemble_benefit(
            library,
            year,
            capacity,
            primary["carbon"][year],
            mode=REDISPATCH_ONLY,
        )
        flex_rows.append(flex)
        base_rows.append(base)
    flex_physical = [detail["with_boiler"] for row in flex_rows for detail in row["details"]]
    base_physical = [detail["with_boiler"] for row in base_rows for detail in row["details"]]
    flex_gross = [row["gross_operating_benefit_eur"] for row in flex_rows]
    capex = capacity * primary["capex_rate"]
    fixed = primary["om_fraction"] * capex
    flex_net = [value - fixed for value in flex_gross]
    flex_metrics = investment_cashflows(capex, flex_net, primary["discount_rate"])
    base_npv = primary["economics"][capacity]["npv_eur"]
    return {
        "capacity_mwth": capacity,
        "label": "secondary structural-flexibility upside case",
        "average_gross_benefit_redispatch_m_eur": primary["economics"][capacity]["average_gross_eur"]
        / 1e6,
        "average_gross_benefit_full_flex_m_eur": (sum(flex_gross) / len(flex_gross)) / 1e6,
        "npv_redispatch_m_eur": base_npv / 1e6,
        "npv_full_flex_m_eur": flex_metrics["npv_eur"] / 1e6,
        "npv_difference_m_eur": (flex_metrics["npv_eur"] - base_npv) / 1e6,
        "annual_chp_electricity_redispatch_gwh": _mean(base_physical, "chp_electricity_mwh") / 1000.0,
        "annual_chp_electricity_full_flex_gwh": _mean(flex_physical, "chp_electricity_mwh") / 1000.0,
        "annual_grid_import_full_flex_gwh": _mean(flex_physical, "grid_import_mwh") / 1000.0,
        "annual_grid_export_full_flex_gwh": _mean(flex_physical, "grid_export_mwh") / 1000.0,
        "annual_eboiler_steam_full_flex_gwh": _mean(flex_physical, "eboiler_steam_mwh") / 1000.0,
    }
