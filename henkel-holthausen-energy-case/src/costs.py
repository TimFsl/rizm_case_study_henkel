"""Screening variable-energy cost of the rule-based baseline.

This is not Henkel's energy bill. The EUA cost sits inside the fossil-gas
component of the blended fuel price and is not added a second time.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

COST_COLUMNS = (
    "day_ahead_price_eur_mwh",
    "electricity_import_price_eur_mwh",
    "electricity_export_price_eur_mwh",
    "steam_demand_mw",
    "electricity_demand_mw",
    "chp_steam_mw",
    "boiler_steam_mw",
    "chp_electricity_mw",
    "chp_fuel_mwh",
    "boiler_fuel_mwh",
    "grid_import_mw",
    "grid_export_mw",
    "blended_fuel_price_eur_mwh",
    "chp_fuel_cost_eur",
    "boiler_fuel_cost_eur",
    "grid_import_cost_eur",
    "grid_export_revenue_eur",
    "embedded_fossil_co2_cost_eur",
    "total_variable_energy_cost_eur",
)

EUR_PER_TONNE_LABEL = (
    "site-level screening cost normalized by Henkel production"
)


class CostError(ValueError):
    """Raised when a screening cost cannot be formed."""


def fuel_price_breakdown(assumptions: Any) -> dict[str, float]:
    """Component and blended fuel prices in EUR per MWh of fuel, LHV."""
    commodity = float(_value(assumptions, "market", "natural_gas_commodity_eur_per_mwh"))
    adder = float(_value(assumptions, "market", "gas_variable_adder_eur_per_mwh"))
    premium = float(_value(assumptions, "market", "biomethane_premium_eur_per_mwh"))
    carbon_price = float(_value(assumptions, "emissions", "carbon_price_eur_per_tco2"))
    fossil_factor = float(
        _value(assumptions, "emissions", "fossil_gas_emission_factor_tco2_per_mwh_fuel")
    )
    fossil_share = float(_value(assumptions, "fuel_mix", "fossil_gas_share"))
    biomethane_share = float(_value(assumptions, "fuel_mix", "biomethane_share"))
    coal_share = float(_value(assumptions, "fuel_mix", "coal_share"))
    if coal_share > 1e-9:
        raise CostError(
            "Coal share is positive, but v0.1 has no coal price. "
            "The screening fuel cost covers the gaseous mix only."
        )
    eua_per_mwh = fossil_factor * carbon_price
    fossil_cost = commodity + adder + eua_per_mwh
    # Base-case biomethane uses a zero EU ETS combustion factor, so no EUA term.
    biomethane_cost = commodity + adder + premium
    blended = fossil_share * fossil_cost + biomethane_share * biomethane_cost
    return {
        "fossil_gas_cost_eur_mwh": fossil_cost,
        "biomethane_cost_eur_mwh": biomethane_cost,
        "blended_fuel_cost_eur_mwh": blended,
        "eua_cost_eur_per_mwh_fossil_fuel": eua_per_mwh,
    }


def scenario_blended_fuel_price(
    assumptions: Any,
    carbon_price_eur_per_t: float,
    fossil_pre_carbon_eur_per_mwh: float | None = None,
    biomethane_eur_per_mwh: float | None = None,
) -> float:
    """Blended fuel price for one investment year.

    The primary case keeps the configured gas commodity, adder, and biomethane
    premium, and only replaces the carbon price. A fossil pre-carbon override
    replaces commodity plus adder. A biomethane override replaces the whole
    biomethane price and is not tied to the fossil-gas price.
    """
    commodity = float(_value(assumptions, "market", "natural_gas_commodity_eur_per_mwh"))
    adder = float(_value(assumptions, "market", "gas_variable_adder_eur_per_mwh"))
    premium = float(_value(assumptions, "market", "biomethane_premium_eur_per_mwh"))
    fossil_factor = float(
        _value(assumptions, "emissions", "fossil_gas_emission_factor_tco2_per_mwh_fuel")
    )
    fossil_share = float(_value(assumptions, "fuel_mix", "fossil_gas_share"))
    biomethane_share = float(_value(assumptions, "fuel_mix", "biomethane_share"))
    pre_carbon = (
        commodity + adder
        if fossil_pre_carbon_eur_per_mwh is None
        else float(fossil_pre_carbon_eur_per_mwh)
    )
    fossil = pre_carbon + fossil_factor * float(carbon_price_eur_per_t)
    biomethane = (
        commodity + adder + premium
        if biomethane_eur_per_mwh is None
        else float(biomethane_eur_per_mwh)
    )
    return fossil_share * fossil + biomethane_share * biomethane


@dataclass(frozen=True)
class NetworkTariff:
    """One published tariff structure applied as a fixed screening scenario."""

    name: str
    utilization_threshold_hours: float
    energy_charge_eur_per_mwh: float
    demand_charge_eur_per_kw_a: float


NETWORK_TARIFF_NAMES = ("hv_high_utilization", "hv_low_utilization")
UTILIZATION_TOLERANCE_HOURS = 1e-3
PEAK_IMPORT_TOLERANCE_MW = 1e-6


def read_network_tariff(assumptions: Any, name: str) -> NetworkTariff:
    """Read one fixed tariff scenario. The optimizer does not choose it."""
    if name not in NETWORK_TARIFF_NAMES:
        known = ", ".join(NETWORK_TARIFF_NAMES)
        raise CostError(f"Unknown network tariff {name!r}. Known tariffs: {known}")
    try:
        block = assumptions.section("grid_tariffs")[name]
    except (AttributeError, KeyError, CostError) as exc:
        raise CostError(f"grid_tariffs.{name} is missing") from exc
    if not isinstance(block, dict):
        raise CostError(f"grid_tariffs.{name} must be a mapping")
    threshold = _nested_value(block, "utilization_threshold_hours", f"grid_tariffs.{name}")
    energy = _nested_value(block, "network_energy_charge_eur_per_mwh", f"grid_tariffs.{name}")
    demand = _nested_value(block, "network_demand_charge_eur_per_kw_a", f"grid_tariffs.{name}")
    if threshold <= 0 or energy < 0 or demand < 0:
        raise CostError(f"grid_tariffs.{name} has a non-positive threshold or a negative charge")
    return NetworkTariff(
        name=name,
        utilization_threshold_hours=float(threshold),
        energy_charge_eur_per_mwh=float(energy),
        demand_charge_eur_per_kw_a=float(demand),
    )


def network_energy_cost_eur(import_mw: pd.Series, energy_charge_eur_per_mwh: float) -> float:
    """Annual network energy charge. One hour makes MW numerically equal to MWh."""
    imported_mwh = float(pd.Series(import_mw, dtype=float).sum())
    return imported_mwh * float(energy_charge_eur_per_mwh)


def network_demand_charge_eur(peak_import_mw: float, demand_charge_eur_per_kw_a: float) -> float:
    """Annual peak charge. The factor 1000 converts MW to kW."""
    return float(peak_import_mw) * 1000.0 * float(demand_charge_eur_per_kw_a)


def utilization_hours(import_mwh: float, peak_import_mw: float) -> float | None:
    """Annual imported MWh divided by the annual peak MW. Undefined at a zero peak."""
    if float(peak_import_mw) <= PEAK_IMPORT_TOLERANCE_MW:
        return None
    return float(import_mwh) / float(peak_import_mw)


def network_cost_breakdown(import_mw: pd.Series, tariff: NetworkTariff) -> dict[str, float | bool | None]:
    """Energy charge, demand charge, utilization hours, and regime consistency."""
    imported = pd.Series(import_mw, dtype=float)
    imported_mwh = float(imported.sum())
    peak_mw = float(imported.max()) if len(imported) else 0.0
    energy = network_energy_cost_eur(imported, tariff.energy_charge_eur_per_mwh)
    demand = network_demand_charge_eur(peak_mw, tariff.demand_charge_eur_per_kw_a)
    hours = utilization_hours(imported_mwh, peak_mw)
    return {
        "import_mwh": imported_mwh,
        "peak_mw": peak_mw,
        "energy_eur": energy,
        "demand_eur": demand,
        "total_eur": energy + demand,
        "utilization_hours": hours,
        "consistent": tariff_regime_is_consistent(
            tariff.name, hours, tariff.utilization_threshold_hours
        ),
    }


def tariff_regime_is_consistent(
    tariff_name: str,
    utilization: float | None,
    threshold_hours: float,
) -> bool:
    """Report whether the solved import pattern matches the tariff that was applied.

    Near the threshold, a result within 0.001 h below 2,500 h is treated as
    meeting the >= 2,500 h regime. It is not also treated as the < 2,500 h regime.
    """
    if utilization is None:
        return False
    hours = float(utilization)
    threshold = float(threshold_hours)
    if tariff_name == "hv_high_utilization":
        return hours >= threshold - UTILIZATION_TOLERANCE_HOURS
    if tariff_name == "hv_low_utilization":
        return hours < threshold - UTILIZATION_TOLERANCE_HOURS
    raise CostError(f"Unknown network tariff {tariff_name!r}")


def consistent_tariff_name(
    utilization: float | None,
    threshold_hours: float,
) -> str | None:
    """Public proxy that matches these utilization hours.

    A zero peak has no utilization hours, so neither published regime applies.
    The caller does not choose the regime. It only classifies a completed result.
    """
    if utilization is None:
        return None
    hours = float(utilization)
    if hours >= float(threshold_hours) - UTILIZATION_TOLERANCE_HOURS:
        return "hv_high_utilization"
    return "hv_low_utilization"


def regime_consistent_value_eur(
    baseline_screening_eur: float,
    optimized_screening_eur: float,
) -> float:
    """Baseline cost under the baseline regime, minus optimized cost under its regime."""
    return float(baseline_screening_eur) - float(optimized_screening_eur)


def commodity_electricity_prices(prices: pd.DataFrame, assumptions: Any) -> pd.DataFrame:
    """Day-ahead import and discounted export, without a generic network adder.

    Network energy and demand charges are added outside this commodity price.
    A non-zero import adder is rejected here so it cannot be stacked on the tariff.
    """
    adder = float(_value(assumptions, "market", "electricity_import_adder_eur_per_mwh"))
    discount = float(_value(assumptions, "market", "electricity_export_discount_eur_per_mwh"))
    if adder != 0.0:
        raise CostError(
            "The generic electricity import adder must stay at 0 when the "
            "explicit network tariff is applied. A non-zero adder would "
            f"double-count network cost; got {adder:g} EUR/MWh."
        )
    if "day_ahead_price_eur_mwh" not in prices.columns:
        raise CostError("Prices are missing day_ahead_price_eur_mwh")
    commodity = prices.copy()
    day_ahead = commodity["day_ahead_price_eur_mwh"].astype(float)
    commodity["electricity_import_price_eur_mwh"] = day_ahead + adder
    commodity["electricity_export_price_eur_mwh"] = day_ahead - discount
    return commodity


def _nested_value(block: dict, key: str, prefix: str) -> float:
    item = block.get(key)
    if not isinstance(item, dict) or "value" not in item:
        raise CostError(f"{prefix}.{key} must be a mapping with a value")
    value = item["value"]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CostError(f"{prefix}.{key} must be a number")
    return float(value)


def cost_baseline(
    dispatch: pd.DataFrame,
    prices: pd.DataFrame,
    assumptions: Any,
    blended_fuel_price_eur_mwh: float | None = None,
) -> pd.DataFrame:
    """Value one physical baseline at the screening fuel and electricity prices.

    Henkel production tonnes are not an input. They are only a later
    reporting denominator.
    """
    if not dispatch.index.equals(prices.index):
        raise CostError(
            "Dispatch hours and model price hours do not match one-to-one"
        )
    prices_needed = prices[
        [
            "day_ahead_price_eur_mwh",
            "electricity_import_price_eur_mwh",
            "electricity_export_price_eur_mwh",
        ]
    ]
    if prices_needed.isna().any().any():
        raise CostError("Electricity price series contains NaN")

    breakdown = fuel_price_breakdown(assumptions)
    blended = (
        float(blended_fuel_price_eur_mwh)
        if blended_fuel_price_eur_mwh is not None
        else breakdown["blended_fuel_cost_eur_mwh"]
    )
    fossil_share = float(_value(assumptions, "fuel_mix", "fossil_gas_share"))
    fossil_factor = float(
        _value(assumptions, "emissions", "fossil_gas_emission_factor_tco2_per_mwh_fuel")
    )
    carbon_price = float(_value(assumptions, "emissions", "carbon_price_eur_per_tco2"))

    costed = dispatch.copy()
    costed["day_ahead_price_eur_mwh"] = prices_needed["day_ahead_price_eur_mwh"]
    costed["electricity_import_price_eur_mwh"] = prices_needed["electricity_import_price_eur_mwh"]
    costed["electricity_export_price_eur_mwh"] = prices_needed["electricity_export_price_eur_mwh"]
    costed["blended_fuel_price_eur_mwh"] = blended
    costed["chp_fuel_cost_eur"] = costed["chp_fuel_mwh"] * blended
    costed["boiler_fuel_cost_eur"] = costed["boiler_fuel_mwh"] * blended
    costed["grid_import_cost_eur"] = (
        costed["grid_import_mw"] * costed["electricity_import_price_eur_mwh"]
    )
    costed["grid_export_revenue_eur"] = (
        costed["grid_export_mw"] * costed["electricity_export_price_eur_mwh"]
    )
    # Memo only. This EUA component is already inside the blended fossil price.
    # The fossil share is taken from current assumptions, not from an older dispatch file.
    fossil_fuel_mwh = (costed["chp_fuel_mwh"] + costed["boiler_fuel_mwh"]) * fossil_share
    costed["embedded_fossil_co2_cost_eur"] = fossil_fuel_mwh * fossil_factor * carbon_price
    costed["total_variable_energy_cost_eur"] = (
        costed["chp_fuel_cost_eur"]
        + costed["boiler_fuel_cost_eur"]
        + costed["grid_import_cost_eur"]
        - costed["grid_export_revenue_eur"]
    )
    return costed.loc[:, list(COST_COLUMNS)]


def summarize_costed_baseline(costed: pd.DataFrame, assumptions: Any) -> dict[str, float | str]:
    """Annual screening-cost metrics. The EUR/t figure is a site-level normalization."""
    total_eur = float(costed["total_variable_energy_cost_eur"].sum())
    production_tonnes = float(
        _value(assumptions, "site", "annual_henkel_production_tonnes")
    )
    day_ahead = costed["day_ahead_price_eur_mwh"]
    market_year = int(_value(assumptions, "market", "representative_market_year"))
    return {
        "market_year": market_year,
        "average_day_ahead_price_eur_mwh": float(day_ahead.mean()),
        "minimum_day_ahead_price_eur_mwh": float(day_ahead.min()),
        "maximum_day_ahead_price_eur_mwh": float(day_ahead.max()),
        "negative_price_hours": float((day_ahead < 0).sum()),
        "blended_fuel_price_eur_mwh": float(costed["blended_fuel_price_eur_mwh"].iloc[0]),
        "chp_fuel_cost_m_eur": _million(costed["chp_fuel_cost_eur"]),
        "boiler_fuel_cost_m_eur": _million(costed["boiler_fuel_cost_eur"]),
        "total_fuel_cost_m_eur": _million(
            costed["chp_fuel_cost_eur"] + costed["boiler_fuel_cost_eur"]
        ),
        "grid_import_cost_m_eur": _million(costed["grid_import_cost_eur"]),
        "grid_export_revenue_m_eur": _million(costed["grid_export_revenue_eur"]),
        "total_variable_energy_cost_m_eur": total_eur / 1_000_000.0,
        "total_variable_energy_cost_eur_per_t_henkel_production": total_eur / production_tonnes,
        "eur_per_t_basis": EUR_PER_TONNE_LABEL,
    }


def _million(series: pd.Series) -> float:
    return float(series.sum()) / 1_000_000.0


def _value(assumptions: Any, section: str, key: str):
    if hasattr(assumptions, "value"):
        return assumptions.value(section, key)
    return assumptions[section][key]["value"]
