#!/usr/bin/env python3
"""Forward fuel-cost screen for the electrode boiler.

Hourly dispatch is solved only at 2026, 2030, and 2040. Years between those
anchors are linear. Electricity prices stay the observed 2023-2025 shapes.
The 2026 Düsseldorf high-voltage tariff is held constant in real terms.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.assumptions import load_assumptions
from src.electric_boiler import BC1_OUTPUTS, FULL_FLEX, ScreeningLibrary, investment_cashflows
from src.forward_cost import (
    FORWARD_ANCHOR_YEARS,
    FORWARD_CAPACITIES_MW,
    FORWARD_SCENARIOS,
    PRIMARY_FORWARD_SCENARIO,
    apply_2026_hv_tariff,
    displaced_thermal_asset,
    fuel_stack,
    interpolated_annual_benefit,
    load_forward_paths,
    mean_shape_benefit,
)
from src.investment import payback_label, select_capacity_by_npv
from src.optimization import REDISPATCH_ONLY
from src.plotting import plot_eboiler_benefit_path, plot_eboiler_npv_scenarios

TABLE_DIR = ROOT / "outputs" / "tables"
FIGURE_DIR = ROOT / "outputs" / "figures"
SHAPES = (2023, 2024, 2025)
PROTECTED = list(BC1_OUTPUTS) + [
    "outputs/tables/electric_boiler_sizing.csv",
    "outputs/tables/electric_boiler_market_years.csv",
    "outputs/tables/electric_boiler_cashflows.csv",
    "outputs/tables/electric_boiler_sensitivity.csv",
]


def _hash(path: Path) -> str:
    if not path.is_file():
        return "missing"
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _mean(rows: list[dict], key: str) -> float:
    return sum(float(row[key]) for row in rows) / len(rows)


def main() -> int:
    before = {name: _hash(ROOT / name) for name in PROTECTED}
    assumptions = load_assumptions()
    library = ScreeningLibrary(assumptions)
    apply_2026_hv_tariff(library)
    paths = load_forward_paths(assumptions)
    shapes = [int(year) for year in assumptions.value("electric_boiler", "market_price_years")]
    if shapes != list(SHAPES):
        raise SystemExit(f"Expected price shapes {SHAPES}, found {shapes}")
    first = int(assumptions.value("electric_boiler", "first_operating_year"))
    lifetime = int(assumptions.value("electric_boiler", "lifetime_years"))
    rate = float(assumptions.value("electric_boiler", "real_discount_rate"))
    capex_rate = float(assumptions.value("electric_boiler", "capex_eur_per_mwth"))
    om_fraction = float(assumptions.value("electric_boiler", "fixed_om_fraction_of_capex"))
    production = float(assumptions.value("site", "annual_henkel_production_tonnes"))
    steam_gwh = float(assumptions.value("demand", "annual_steam_heat_gwh"))
    hours = float(assumptions.value("model", "hours_per_year"))
    average_steam_mw = steam_gwh * 1000.0 / hours
    years = list(range(first, first + lifetime))
    if years[0] != 2026 or years[-1] != 2040 or lifetime != 15:
        raise SystemExit("The forward screen expects operating years 2026-2040")

    anchor_rows: list[dict] = []
    benefits: dict[tuple[float, str], dict[int, float]] = {}
    steam: dict[tuple[float, str], dict[int, float]] = {}
    solved_fuel: dict[tuple[str, int], dict[str, float]] = {}

    for scenario in FORWARD_SCENARIOS:
        for anchor in FORWARD_ANCHOR_YEARS:
            point = paths[scenario][anchor]
            stack = fuel_stack(assumptions, point["precarbon_gas_eur_mwh"], point["eua_eur_t"])
            solved_fuel[(scenario, anchor)] = stack
            print(
                f"{scenario} {anchor}: pre-carbon {stack['precarbon_gas_eur_mwh']:.1f}, "
                f"EUA {stack['eua_eur_t']:.1f}, "
                f"effective fossil {stack['effective_fossil_gas_eur_mwh']:.2f}, "
                f"biomethane {stack['biomethane_eur_mwh']:.2f}, "
                f"blended {stack['blended_fuel_eur_mwh']:.2f}",
                flush=True,
            )
            for capacity in FORWARD_CAPACITIES_MW:
                shape_rows = []
                for shape in SHAPES:
                    without = library.regime_case(
                        shape, 0.0, FULL_FLEX, 1.0, stack["blended_fuel_eur_mwh"]
                    )
                    with_boiler = library.regime_case(
                        shape, capacity, FULL_FLEX, 1.0, stack["blended_fuel_eur_mwh"]
                    )
                    benefit = without["screening_cost_eur"] - with_boiler["screening_cost_eur"]
                    shape_rows.append(
                        {
                            "benefit": benefit,
                            "steam_gwh": with_boiler["eboiler_steam_mwh"] / 1000.0,
                            "electricity_gwh": with_boiler["eboiler_electricity_mwh"] / 1000.0,
                            "chp_steam_gwh": with_boiler["chp_steam_mwh"] / 1000.0,
                            "chp_steam_change_gwh": (
                                with_boiler["chp_steam_mwh"] - without["chp_steam_mwh"]
                            )
                            / 1000.0,
                            "chp_electricity_gwh": with_boiler["chp_electricity_mwh"] / 1000.0,
                            "chp_electricity_change_gwh": (
                                with_boiler["chp_electricity_mwh"] - without["chp_electricity_mwh"]
                            )
                            / 1000.0,
                            "gas_boiler_steam_gwh": with_boiler["boiler_steam_mwh"] / 1000.0,
                            "gas_boiler_change_gwh": (
                                with_boiler["boiler_steam_mwh"] - without["boiler_steam_mwh"]
                            )
                            / 1000.0,
                            "import_gwh": with_boiler["grid_import_mwh"] / 1000.0,
                            "export_gwh": with_boiler["grid_export_mwh"] / 1000.0,
                            "peak_import_mw": with_boiler["peak_import_mw"],
                            "peak_import_without_mw": without["peak_import_mw"],
                            "network_eur": with_boiler["network_energy_eur"]
                            + with_boiler["network_demand_eur"],
                            "total_fuel_gwh": with_boiler["total_fuel_mwh"] / 1000.0,
                            "fossil_fuel_gwh": with_boiler["fossil_fuel_mwh"] / 1000.0,
                            "biomethane_gwh": with_boiler["biomethane_mwh"] / 1000.0,
                            "avoided_co2_t": (
                                without["fossil_fuel_mwh"] - with_boiler["fossil_fuel_mwh"]
                            )
                            * stack["emission_factor"],
                            "tariff_regime": with_boiler["tariff_regime"],
                        }
                    )
                mean_benefit = mean_shape_benefit([row["benefit"] for row in shape_rows])
                mean_steam = _mean(shape_rows, "steam_gwh")
                chp_change = _mean(shape_rows, "chp_steam_change_gwh")
                boiler_change = _mean(shape_rows, "gas_boiler_change_gwh")
                benefits[(capacity, scenario)] = benefits.get((capacity, scenario), {})
                benefits[(capacity, scenario)][anchor] = mean_benefit
                steam[(capacity, scenario)] = steam.get((capacity, scenario), {})
                steam[(capacity, scenario)][anchor] = mean_steam
                by_shape = {SHAPES[index]: shape_rows[index]["benefit"] for index in range(3)}
                anchor_rows.append(
                    {
                        "capacity_mw": capacity,
                        "scenario": scenario,
                        "anchor_year": anchor,
                        "precarbon_gas_eur_mwh": stack["precarbon_gas_eur_mwh"],
                        "eua_eur_t": stack["eua_eur_t"],
                        "effective_fossil_gas_eur_mwh": stack["effective_fossil_gas_eur_mwh"],
                        "biomethane_eur_mwh": stack["biomethane_eur_mwh"],
                        "benefit_2023_shape": by_shape[2023],
                        "benefit_2024_shape": by_shape[2024],
                        "benefit_2025_shape": by_shape[2025],
                        "mean_gross_benefit": mean_benefit,
                        "eboiler_steam_gwh": mean_steam,
                        "eboiler_electricity_gwh": _mean(shape_rows, "electricity_gwh"),
                        "full_load_hours": mean_steam * 1000.0 / capacity,
                        "steam_share_percent": 100.0 * mean_steam / steam_gwh,
                        "chp_steam_gwh": _mean(shape_rows, "chp_steam_gwh"),
                        "chp_steam_change_gwh": chp_change,
                        "chp_electricity_gwh": _mean(shape_rows, "chp_electricity_gwh"),
                        "chp_electricity_change_gwh": _mean(shape_rows, "chp_electricity_change_gwh"),
                        "gas_boiler_steam_gwh": _mean(shape_rows, "gas_boiler_steam_gwh"),
                        "gas_boiler_steam_change_gwh": boiler_change,
                        "primary_displaced_asset": displaced_thermal_asset(-chp_change, -boiler_change),
                        "grid_import_gwh": _mean(shape_rows, "import_gwh"),
                        "grid_export_gwh": _mean(shape_rows, "export_gwh"),
                        "peak_import_mw": _mean(shape_rows, "peak_import_mw"),
                        "peak_import_without_mw": _mean(shape_rows, "peak_import_without_mw"),
                        "network_cost_eur": _mean(shape_rows, "network_eur"),
                        "total_fuel_gwh": _mean(shape_rows, "total_fuel_gwh"),
                        "fossil_fuel_gwh": _mean(shape_rows, "fossil_fuel_gwh"),
                        "biomethane_gwh": _mean(shape_rows, "biomethane_gwh"),
                        "avoided_direct_fossil_co2_t": _mean(shape_rows, "avoided_co2_t"),
                        "tariff_regime": shape_rows[0]["tariff_regime"]
                        if len({row["tariff_regime"] for row in shape_rows}) == 1
                        else "mixed",
                    }
                )
                print(
                    f"  {capacity:g} MW {scenario} {anchor}: "
                    f"mean benefit {mean_benefit:.0f} EUR, steam {mean_steam:.2f} GWh, "
                    f"solves {library.solves}",
                    flush=True,
                )

    for capacity in FORWARD_CAPACITIES_MW:
        start = [benefits[(capacity, name)][2026] for name in FORWARD_SCENARIOS]
        if max(start) - min(start) > 1.0:
            raise SystemExit("2026 benefits differ across paths that share the 2026 fuel stack")

    npv_rows = []
    comparison_rows = []
    path_frames = {}
    current_npv = {}
    for capacity in FORWARD_CAPACITIES_MW:
        capex = capacity * capex_rate
        fixed = om_fraction * capex
        constant = [benefits[(capacity, PRIMARY_FORWARD_SCENARIO)][2026]] * lifetime
        current = investment_cashflows(capex, [value - fixed for value in constant], rate)
        current_npv[capacity] = current
        scenario_npv = {}
        for scenario in FORWARD_SCENARIOS:
            annual = interpolated_annual_benefit(benefits[(capacity, scenario)], years)
            annual_steam = interpolated_annual_benefit(steam[(capacity, scenario)], years)
            nets = [annual[year] - fixed for year in years]
            metrics = investment_cashflows(capex, nets, rate)
            scenario_npv[scenario] = metrics
            share = 100.0 * (sum(annual_steam.values()) / len(annual_steam)) / steam_gwh
            npv_rows.append(
                {
                    "capacity_mw": capacity,
                    "scenario": scenario,
                    "capex_m_eur": capex / 1e6,
                    "fixed_opex_m_eur_a": fixed / 1e6,
                    "benefit_2026": benefits[(capacity, scenario)][2026],
                    "benefit_2030": benefits[(capacity, scenario)][2030],
                    "benefit_2040": benefits[(capacity, scenario)][2040],
                    "npv_m_eur": metrics["npv_eur"] / 1e6,
                    "irr_percent": "undefined" if metrics["irr"] is None else 100.0 * metrics["irr"],
                    "simple_payback_years": payback_label(
                        metrics["simple_payback_years"], capex_eur=capex
                    ),
                    "discounted_payback_years": payback_label(
                        metrics["discounted_payback_years"], capex_eur=capex
                    ),
                    "eav_m_eur": metrics["equivalent_annual_value_eur"] / 1e6,
                    "eav_eur_per_t": metrics["equivalent_annual_value_eur"] / production,
                    "capacity_percent_of_average_steam_load": 100.0 * capacity / average_steam_mw,
                    "average_annual_eboiler_steam_share_percent": share,
                }
            )
            if scenario == PRIMARY_FORWARD_SCENARIO:
                path_frames[capacity] = pd.DataFrame(
                    {
                        "year": years,
                        "gross_benefit_m_eur": [annual[year] / 1e6 for year in years],
                        "net_cashflow_m_eur": [nets[index] / 1e6 for index, year in enumerate(years)],
                    }
                )
        comparison_rows.append(
            {
                "capacity_mw": capacity,
                "current_cost_npv_m_eur": current["npv_eur"] / 1e6,
                "low_npv_m_eur": scenario_npv["low"]["npv_eur"] / 1e6,
                "mid_npv_m_eur": scenario_npv["mid"]["npv_eur"] / 1e6,
                "high_npv_m_eur": scenario_npv["high"]["npv_eur"] / 1e6,
                "low_eav_eur_per_t": scenario_npv["low"]["equivalent_annual_value_eur"] / production,
                "mid_eav_eur_per_t": scenario_npv["mid"]["equivalent_annual_value_eur"] / production,
                "high_eav_eur_per_t": scenario_npv["high"]["equivalent_annual_value_eur"] / production,
            }
        )

    anchors = pd.DataFrame(anchor_rows)
    npv = pd.DataFrame(npv_rows)
    comparison = pd.DataFrame(comparison_rows)
    TABLE_DIR.mkdir(parents=True, exist_ok=True)
    FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    anchors.to_csv(TABLE_DIR / "electric_boiler_forward_cost_anchors.csv", index=False)
    npv.to_csv(TABLE_DIR / "electric_boiler_forward_npv.csv", index=False)
    comparison.to_csv(TABLE_DIR / "electric_boiler_current_vs_forward.csv", index=False)

    mid_choice = {
        row["capacity_mw"]: row["npv_m_eur"]
        for row in npv_rows
        if row["scenario"] == PRIMARY_FORWARD_SCENARIO
    }
    optimum = select_capacity_by_npv(mid_choice)
    plot_rows = [{"capacity_mw": capacity, "scenario": "current", "npv_m_eur": current_npv[capacity]["npv_eur"] / 1e6} for capacity in FORWARD_CAPACITIES_MW]
    plot_rows.extend(
        {"capacity_mw": row["capacity_mw"], "scenario": row["scenario"], "npv_m_eur": row["npv_m_eur"]}
        for row in npv_rows
    )
    plot_eboiler_npv_scenarios(pd.DataFrame(plot_rows), FIGURE_DIR / "electric_boiler_npv_scenarios.png")
    plot_eboiler_benefit_path(
        path_frames[optimum],
        FIGURE_DIR / "electric_boiler_annual_benefit_path.png",
        f"MID path gross benefit and net cashflow at {optimum:g} MW",
    )

    print("FORWARD NPV", flush=True)
    print(comparison.to_string(index=False), flush=True)
    print(f"MID forward-looking screening optimum: {optimum:g} MW", flush=True)
    print(npv[npv["scenario"] == PRIMARY_FORWARD_SCENARIO].to_string(index=False), flush=True)
    mid_anchors = anchors[
        (anchors["scenario"] == PRIMARY_FORWARD_SCENARIO) & (anchors["capacity_mw"] == optimum)
    ]
    print(mid_anchors.to_string(index=False), flush=True)
    print(
        "Electricity-price shapes are historical observations. "
        "Fuel and carbon paths are screening scenarios, not hourly price forecasts.",
        flush=True,
    )
    if REDISPATCH_ONLY == FULL_FLEX:
        raise SystemExit("Mode constants collided")
    after = {name: _hash(ROOT / name) for name in PROTECTED}
    changed = [name for name in PROTECTED if before[name] != after[name]]
    if changed:
        print("Protected files changed: " + ", ".join(changed), flush=True)
        return 1
    print("Business Case 1 and the existing e-boiler result files were not changed.", flush=True)
    print(f"Solves: {library.solves}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
