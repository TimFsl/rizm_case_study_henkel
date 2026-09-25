#!/usr/bin/env python3
"""Assign screening variable-energy costs to the rule-based baseline."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.assumptions import AssumptionError, load_assumptions
from src.costs import (
    EUR_PER_TONNE_LABEL,
    CostError,
    cost_baseline,
    fuel_price_breakdown,
    summarize_costed_baseline,
)
from src.market_prices import MarketPriceError

SCENARIOS = ("flat", "base", "variable")
SUMMARY_COLUMNS = [
    "scenario",
    "market_year",
    "average_day_ahead_price_eur_mwh",
    "minimum_day_ahead_price_eur_mwh",
    "maximum_day_ahead_price_eur_mwh",
    "negative_price_hours",
    "blended_fuel_price_eur_mwh",
    "chp_fuel_cost_m_eur",
    "boiler_fuel_cost_m_eur",
    "total_fuel_cost_m_eur",
    "grid_import_cost_m_eur",
    "grid_export_revenue_m_eur",
    "total_variable_energy_cost_m_eur",
    "total_variable_energy_cost_eur_per_t_henkel_production",
    "eur_per_t_basis",
]


def main() -> int:
    try:
        assumptions = load_assumptions()
        prices = _load_prices(ROOT / "data" / "processed" / "electricity_day_ahead_prices.csv")
        breakdown = fuel_price_breakdown(assumptions)
    except (AssumptionError, CostError, MarketPriceError) as exc:
        print(f"Baseline costing failed: {exc}", file=sys.stderr)
        return 1

    print("Screening variable energy cost")
    print("This is not Henkel's actual energy bill.")
    print(f"EUR/t basis: {EUR_PER_TONNE_LABEL}")
    print()
    print(f"Fossil gas:  {breakdown['fossil_gas_cost_eur_mwh']:.2f} EUR/MWh_fuel")
    print(f"Biomethane:  {breakdown['biomethane_cost_eur_mwh']:.2f} EUR/MWh_fuel")
    print(f"Blended:     {breakdown['blended_fuel_cost_eur_mwh']:.2f} EUR/MWh_fuel")
    print(
        "Embedded EUA in fossil gas: "
        f"{breakdown['eua_cost_eur_per_mwh_fossil_fuel']:.2f} EUR/MWh_fuel "
        "(included once, inside the blended price)"
    )
    print()

    rows = []
    for scenario in SCENARIOS:
        dispatch_path = ROOT / "data" / "processed" / f"baseline_dispatch_{scenario}.csv"
        if not dispatch_path.is_file():
            print(f"Missing baseline dispatch: {dispatch_path}", file=sys.stderr)
            return 1
        dispatch = _load_dispatch(dispatch_path, assumptions.value("model", "timezone"))
        try:
            costed = cost_baseline(dispatch, prices, assumptions)
        except CostError as exc:
            print(f"{scenario}: {exc}", file=sys.stderr)
            return 1
        costed.reset_index().to_csv(
            ROOT / "data" / "processed" / f"baseline_costed_{scenario}.csv",
            index=False,
        )
        summary = summarize_costed_baseline(costed, assumptions)
        rows.append({"scenario": scenario, **summary})

    summary_frame = pd.DataFrame(rows, columns=SUMMARY_COLUMNS)
    summary_path = ROOT / "outputs" / "tables" / "baseline_cost_summary.csv"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_frame.to_csv(summary_path, index=False)
    print(summary_frame.to_string(index=False, float_format=lambda value: f"{value:.4f}"))
    print()
    print(EUR_PER_TONNE_LABEL)
    print("Not an attributable Henkel cost: the site also supplies third parties.")
    return 0


def _load_prices(path: Path) -> pd.DataFrame:
    if not path.is_file():
        raise MarketPriceError(
            f"Missing {path}. Run scripts/build_market_prices.py first."
        )
    frame = pd.read_csv(path)
    timestamps = pd.to_datetime(frame["timestamp"], utc=True).dt.tz_convert("Europe/Berlin")
    prices = frame.drop(columns=["timestamp"])
    prices.index = pd.DatetimeIndex(timestamps, name="timestamp")
    return prices


def _load_dispatch(path: Path, timezone: str) -> pd.DataFrame:
    frame = pd.read_csv(path)
    timestamps = pd.to_datetime(frame["timestamp"], utc=True).dt.tz_convert(timezone)
    dispatch = frame.drop(columns=["timestamp"])
    dispatch.index = pd.DatetimeIndex(timestamps, name="timestamp")
    return dispatch


if __name__ == "__main__":
    sys.exit(main())
