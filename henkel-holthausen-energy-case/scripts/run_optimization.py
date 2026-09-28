#!/usr/bin/env python3
"""Solve price-responsive CHP/boiler dispatch and compare it with the baseline."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.assumptions import AssumptionError, load_assumptions
from src.costs import CostError, cost_baseline
from src.optimization import (
    DISPATCH_VALUE_BASIS,
    FULL_FLEX,
    OPTIMIZATION_MODES,
    OptimizationError,
    add_intertemporal_constraints_note,
    calculate_chp_break_even_price,
    solve_dispatch,
    summarize_optimization,
    validate_optimized_dispatch,
)
from src.plotting import (
    DEFAULT_WEEK_START,
    PlotInputError,
    plot_baseline_vs_optimized_week,
    plot_optimized_dispatch_dashboard,
)

SCENARIOS = ("flat", "base", "variable")
SUMMARY_COLUMNS = [
    "scenario",
    "optimization_mode",
    "baseline_variable_cost_m_eur",
    "optimized_variable_cost_m_eur",
    "dispatch_value_m_eur",
    "dispatch_value_eur_per_t_henkel_production",
    "dispatch_value_percent_of_baseline",
    "baseline_chp_steam_gwh",
    "optimized_chp_steam_gwh",
    "baseline_boiler_steam_gwh",
    "optimized_boiler_steam_gwh",
    "baseline_chp_electricity_gwh",
    "optimized_chp_electricity_gwh",
    "baseline_total_fuel_gwh",
    "optimized_total_fuel_gwh",
    "baseline_grid_import_gwh",
    "optimized_grid_import_gwh",
    "baseline_grid_export_gwh",
    "optimized_grid_export_gwh",
    "baseline_net_grid_import_gwh",
    "optimized_net_grid_import_gwh",
    "optimized_peak_grid_import_mw",
    "optimized_peak_grid_export_mw",
    "optimized_peak_chp_electricity_mw",
    "hours_at_chp_max_or_near_max",
    "hours_at_minimum_feasible_chp",
    "electricity_break_even_eur_mwh",
    "hours_price_below_break_even",
    "hours_price_above_break_even",
    "dispatch_value_basis",
]


def main() -> int:
    try:
        assumptions = load_assumptions()
        timezone = str(assumptions.value("model", "timezone"))
        prices = _load_hourly(
            ROOT / "data" / "processed" / "electricity_day_ahead_prices.csv",
            timezone,
        )
        break_even = calculate_chp_break_even_price(assumptions)
    except (AssumptionError, OptimizationError) as exc:
        print(f"Optimization failed: {exc}", file=sys.stderr)
        return 1

    print("Price-responsive CHP/boiler dispatch")
    print("Screening dispatch value relative to the calibrated reference.")
    print("This is theoretical dispatch optimization potential, not realized Henkel savings.")
    print(add_intertemporal_constraints_note())
    print(f"CHP-vs-boiler electricity break-even: {break_even:.2f} EUR/MWh")
    print(f"EUR/t basis: {DISPATCH_VALUE_BASIS}")
    print()

    rows = []
    saved_frames: dict[tuple[str, str], pd.DataFrame] = {}
    baselines: dict[str, pd.DataFrame] = {}
    for scenario in SCENARIOS:
        try:
            baseline = _load_hourly(
                ROOT / "data" / "processed" / f"baseline_dispatch_{scenario}.csv",
                timezone,
            )
            demand = baseline[["steam_demand_mw", "electricity_demand_mw"]]
            baseline_cost = float(
                cost_baseline(baseline, prices, assumptions)["total_variable_energy_cost_eur"].sum()
            )
        except (OptimizationError, CostError, FileNotFoundError) as exc:
            print(f"{scenario}: {exc}", file=sys.stderr)
            return 1
        baselines[scenario] = baseline
        baseline_chp_mwh = float(baseline["chp_electricity_mw"].sum())
        for mode in OPTIMIZATION_MODES:
            try:
                result = solve_dispatch(
                    demand,
                    prices,
                    assumptions,
                    mode,
                    baseline_chp_electricity_mwh=baseline_chp_mwh,
                )
                validate_optimized_dispatch(
                    result,
                    assumptions,
                    baseline_chp_electricity_mwh=baseline_chp_mwh,
                    baseline_variable_cost_eur=baseline_cost,
                    check_price_response=(mode == FULL_FLEX),
                )
            except OptimizationError as exc:
                print(f"{scenario} {mode}: {exc}", file=sys.stderr)
                return 1
            output_path = (
                ROOT / "data" / "processed" / f"optimized_dispatch_{scenario}_{mode}.csv"
            )
            result.frame.reset_index().to_csv(output_path, index=False)
            summary = summarize_optimization(
                scenario,
                result,
                baseline,
                baseline_cost,
                assumptions,
            )
            rows.append(summary)
            saved_frames[(scenario, mode)] = result.frame
            print(
                f"{scenario:8} {mode:16} "
                f"solver {result.termination_condition}, "
                f"dispatch value {summary['dispatch_value_m_eur']:.3f} M EUR/a "
                f"({summary['dispatch_value_eur_per_t_henkel_production']:.2f} EUR/t)"
            )

    summary_frame = pd.DataFrame(rows, columns=SUMMARY_COLUMNS)
    summary_path = ROOT / "outputs" / "tables" / "optimization_summary.csv"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_frame.to_csv(summary_path, index=False)
    print()
    print(summary_frame.to_string(index=False, float_format=lambda value: f"{value:.4f}"))
    print()
    _print_export_flags(summary_frame)
    try:
        week = DEFAULT_WEEK_START
        figures = ROOT / "outputs" / "figures"
        dashboard = plot_optimized_dispatch_dashboard(
            saved_frames[("base", FULL_FLEX)],
            week,
            figures / "optimized_dispatch_dashboard_week.png",
        )
        comparison = plot_baseline_vs_optimized_week(
            baselines["base"],
            saved_frames[("base", FULL_FLEX)],
            break_even,
            week,
            figures / "baseline_vs_optimized_week.png",
        )
    except PlotInputError as exc:
        print(f"Comparison plots failed: {exc}", file=sys.stderr)
        return 1
    print(dashboard)
    print(comparison)
    print(summary_path)
    return 0


def _print_export_flags(summary: pd.DataFrame) -> None:
    material = summary[
        (summary["optimization_mode"] == FULL_FLEX)
        & (
            (summary["optimized_grid_export_gwh"] > 0.1)
            | (summary["optimized_peak_grid_export_mw"] > 1.0)
        )
    ]
    if material.empty:
        print(
            "Full-flex grid export is not material in these runs. "
            "Export capacity remains TBD and is not enforced."
        )
        return
    print(
        "Full-flex allows unlimited grid export because export capacity is still TBD. "
        "Where annual or peak export is material, the screening dispatch value is "
        "potentially optimistic."
    )
    for _, row in material.iterrows():
        print(
            f"  {row['scenario']}: export {row['optimized_grid_export_gwh']:.2f} GWh/a, "
            f"peak {row['optimized_peak_grid_export_mw']:.2f} MW"
        )
    print()


def _load_hourly(path: Path, timezone: str) -> pd.DataFrame:
    if not path.is_file():
        raise OptimizationError(f"Missing processed input: {path}")
    frame = pd.read_csv(path)
    timestamps = pd.to_datetime(frame["timestamp"], utc=True).dt.tz_convert(timezone)
    loaded = frame.drop(columns=["timestamp"])
    loaded.index = pd.DatetimeIndex(timestamps, name="timestamp")
    return loaded


if __name__ == "__main__":
    sys.exit(main())
