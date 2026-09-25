"""Screening variable-energy cost of the rule-based baseline.

This is not Henkel's energy bill. The EUA cost sits inside the fossil-gas
component of the blended fuel price and is not added a second time.
"""

from __future__ import annotations

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


def cost_baseline(
    dispatch: pd.DataFrame,
    prices: pd.DataFrame,
    assumptions: Any,
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
    blended = breakdown["blended_fuel_cost_eur_mwh"]
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
