#!/usr/bin/env python3
"""Supply-stack week figures for the frozen Business Case 1 primary case.

Baseline is read from the stored dispatch file. The redispatch hourly series
was not saved, so this script repeats that same frozen primary solve only to
draw the week. It checks the annual value against the frozen export table and
does not write a result table.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.assumptions import load_assumptions
from src.costs import (
    commodity_electricity_prices,
    cost_baseline,
    network_cost_breakdown,
    read_network_tariff,
    regime_consistent_value_eur,
    utilization_hours,
    consistent_tariff_name,
)
from src.optimization import REDISPATCH_ONLY, solve_dispatch
from src.plotting import DEFAULT_WEEK_START, plot_supply_stack_week

FIGURES = ROOT / "outputs" / "figures"
TABLES = ROOT / "outputs" / "tables"


def _load_hourly(path: Path, timezone: str) -> pd.DataFrame:
    frame = pd.read_csv(path, parse_dates=["timestamp"])
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True).dt.tz_convert(timezone)
    return frame.set_index("timestamp").sort_index()


def main() -> None:
    assumptions = load_assumptions()
    timezone = str(assumptions.value("model", "timezone"))
    prices = commodity_electricity_prices(
        _load_hourly(ROOT / "data" / "processed" / "electricity_day_ahead_prices.csv", timezone),
        assumptions,
    )
    baseline = _load_hourly(ROOT / "data" / "processed" / "baseline_dispatch_base.csv", timezone)
    tariff = read_network_tariff(assumptions, "hv_high_utilization")
    hours = utilization_hours(
        float(baseline["grid_import_mw"].sum()),
        float(baseline["grid_import_mw"].max()),
    )
    regime = consistent_tariff_name(hours, tariff.utilization_threshold_hours)
    baseline_tariff = read_network_tariff(assumptions, regime)
    baseline_cost = float(
        cost_baseline(baseline, prices, assumptions)["total_variable_energy_cost_eur"].sum()
    ) + float(network_cost_breakdown(baseline["grid_import_mw"], baseline_tariff)["total_eur"])
    demand = baseline[["steam_demand_mw", "electricity_demand_mw"]]
    annual_chp = float(baseline["chp_electricity_mw"].sum())
    redispatch = solve_dispatch(
        demand,
        prices,
        assumptions,
        REDISPATCH_ONLY,
        baseline_chp_electricity_mwh=annual_chp,
        network_tariff=tariff,
        chp_ramp_fraction_per_hour=0.50,
        export_capacity_mw=10.0,
    )
    optimized_cost = float(redispatch.frame["total_variable_energy_cost_eur"].sum()) + float(
        network_cost_breakdown(redispatch.frame["grid_import_mw"], tariff)["total_eur"]
    )
    value = regime_consistent_value_eur(baseline_cost, optimized_cost)
    stored = pd.read_csv(TABLES / "dispatch_export_sensitivity.csv")
    primary = stored[stored["screening_role"] == "primary_screening"].iloc[0]
    if abs(value / 1e6 - float(primary["dispatch_value_m_eur"])) > 1e-4:
        raise SystemExit("Redispatch week does not match the frozen primary value")

    FIGURES.mkdir(parents=True, exist_ok=True)
    week = f"week of {DEFAULT_WEEK_START}"
    plot_supply_stack_week(
        baseline,
        DEFAULT_WEEK_START,
        FIGURES / "final_bc1_baseline_week_stack.png",
        f"Business Case 1 baseline, {week}",
    )
    plot_supply_stack_week(
        redispatch.frame,
        DEFAULT_WEEK_START,
        FIGURES / "final_bc1_redispatch_week_stack.png",
        f"Business Case 1 redispatch only, 10 MW export, {week}",
    )
    print(f"Redispatch week matches frozen value {value / 1e6:.6f} M EUR/a")
    print("Supply-stack figures written")


if __name__ == "__main__":
    main()
