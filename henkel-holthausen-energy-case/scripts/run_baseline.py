#!/usr/bin/env python3
"""Run the calibrated rule-based reference dispatch for each demand scenario."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.assumptions import AssumptionError, load_assumptions
from src.baseline import (
    BaselineError,
    dispatch_baseline,
    save_base_dispatch_plots,
    summarize_baseline,
    unconstrained_annual_expectations,
    validate_baseline_dispatch,
)

SCENARIOS = ("flat", "base", "variable")
SUMMARY_COLUMNS = [
    "scenario",
    "steam_demand_gwh",
    "chp_steam_gwh",
    "boiler_steam_gwh",
    "chp_steam_share",
    "electricity_demand_gwh",
    "chp_electricity_gwh",
    "chp_fuel_gwh",
    "boiler_fuel_gwh",
    "total_fuel_gwh",
    "fossil_gas_gwh",
    "biomethane_gwh",
    "coal_gwh",
    "grid_import_gwh",
    "grid_export_gwh",
    "net_grid_import_gwh",
    "peak_grid_import_mw",
    "peak_grid_export_mw",
    "grid_export_hours",
    "peak_steam_demand_mw",
    "peak_chp_steam_mw",
    "peak_boiler_steam_mw",
    "chp_heat_capacity_utilization_peak",
    "boiler_heat_capacity_utilization_peak",
    "chp_hours_near_capacity",
    "boiler_hours_near_capacity",
    "chp_electricity_per_steam_demand",
]


def main() -> int:
    try:
        assumptions = load_assumptions()
    except AssumptionError as exc:
        print(f"Assumptions check failed: {exc}", file=sys.stderr)
        return 1

    print("Calibrated rule-based reference dispatch")
    print("This is not a reconstruction of Henkel's actual control logic.")
    print()

    chp_max = float(assumptions.value("chp", "max_heat_output_mw"))
    boiler_max = float(assumptions.value("boiler", "max_heat_output_mw"))
    steam_gwh = float(assumptions.value("demand", "annual_steam_heat_gwh"))
    electricity_gwh = float(assumptions.value("demand", "annual_electricity_gwh"))
    expected = unconstrained_annual_expectations(steam_gwh, electricity_gwh, assumptions)
    _print_expectations(expected, chp_max, boiler_max)

    rows = []
    export_notes = []
    for scenario in SCENARIOS:
        path = ROOT / "data" / "processed" / f"demand_profile_{scenario}.csv"
        if not path.is_file():
            print(f"Missing demand profile: {path}", file=sys.stderr)
            return 1
        demand = _load_demand_profile(path, assumptions.value("model", "timezone"))
        try:
            dispatch = dispatch_baseline(demand, assumptions)
            validate_baseline_dispatch(dispatch, assumptions)
        except BaselineError as exc:
            print(f"{scenario}: {exc}", file=sys.stderr)
            return 1

        summary = summarize_baseline(dispatch, assumptions)
        print(scenario.upper())
        _print_scenario(summary, chp_max, boiler_max, expected)
        if summary["grid_export_gwh"] > 0:
            export_notes.append(scenario)
        print()

        output = dispatch.reset_index()
        output.to_csv(
            ROOT / "data" / "processed" / f"baseline_dispatch_{scenario}.csv",
            index=False,
        )
        if scenario == "base":
            save_base_dispatch_plots(dispatch, ROOT / "outputs" / "figures" / "baseline_base")
        rows.append({"scenario": scenario, **summary})

    summary_frame = pd.DataFrame(rows, columns=SUMMARY_COLUMNS)
    summary_path = ROOT / "outputs" / "tables" / "baseline_dispatch_summary.csv"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_frame.to_csv(summary_path, index=False, float_format="%.6f")

    print("SUMMARY")
    print(summary_frame.to_string(index=False, float_format=lambda value: f"{value:.4f}"))
    print()
    _print_comparison(summary_frame, expected)
    if export_notes:
        joined = ", ".join(export_notes)
        print(
            "NOTE: baseline contains electricity export, while site-specific "
            f"export capacity remains TBD. Export occurs in: {joined}."
        )
    else:
        print("No scenario exports electricity. Export capacity remains TBD.")
    return 0


def _load_demand_profile(path: Path, timezone: str) -> pd.DataFrame:
    frame = pd.read_csv(path)
    timestamps = pd.to_datetime(frame["timestamp"], utc=True).dt.tz_convert(timezone)
    demand = frame.drop(columns=["timestamp"])
    demand.index = pd.DatetimeIndex(timestamps)
    demand.index.name = "timestamp"
    return demand


def _print_expectations(expected: dict[str, float], chp_max: float, boiler_max: float) -> None:
    print("Unconstrained analytical baseline, from the configured assumptions:")
    print(f"  CHP steam:        {expected['chp_steam_gwh']:.3f} GWh")
    print(f"  Boiler steam:     {expected['boiler_steam_gwh']:.3f} GWh")
    print(f"  CHP electricity:  {expected['chp_electricity_gwh']:.3f} GWh")
    print(f"  CHP fuel:         {expected['chp_fuel_gwh']:.3f} GWh")
    print(f"  Boiler fuel:      {expected['boiler_fuel_gwh']:.3f} GWh")
    print(f"  Total fuel:       {expected['total_fuel_gwh']:.3f} GWh")
    print(f"  Net grid import:  {expected['net_grid_import_gwh']:.3f} GWh")
    print(f"  Heat capacity:    CHP {chp_max:.1f} MW, boiler {boiler_max:.1f} MW, total {chp_max + boiler_max:.1f} MW")
    print()


def _print_scenario(
    summary: dict[str, float],
    chp_max: float,
    boiler_max: float,
    expected: dict[str, float],
) -> None:
    print(f"  Maximum steam demand: {summary['peak_steam_demand_mw']:.3f} MW")
    print(
        f"  Maximum CHP steam:    {summary['peak_chp_steam_mw']:.3f} MW "
        f"of {chp_max:.1f} MW "
        f"({summary['chp_hours_near_capacity']:.0f} h at or above 99%)"
    )
    print(
        f"  Maximum boiler steam: {summary['peak_boiler_steam_mw']:.3f} MW "
        f"of {boiler_max:.1f} MW "
        f"({summary['boiler_hours_near_capacity']:.0f} h at or above 99%)"
    )
    print(
        "  Gross grid import / export / net import: "
        f"{summary['grid_import_gwh']:.3f} / "
        f"{summary['grid_export_gwh']:.3f} / "
        f"{summary['net_grid_import_gwh']:.3f} GWh"
    )
    print(
        "  Peak grid import / export: "
        f"{summary['peak_grid_import_mw']:.3f} / "
        f"{summary['peak_grid_export_mw']:.3f} MW "
        f"({summary['grid_export_hours']:.0f} export hours)"
    )
    share_gap = summary["chp_steam_share"] - (
        expected["chp_steam_gwh"] / (expected["chp_steam_gwh"] + expected["boiler_steam_gwh"])
    )
    if abs(share_gap) > 1e-4:
        print("  Capacity limits changed the annual CHP steam share relative to the configured fraction.")
    else:
        print("  Annual CHP steam share matches the configured fraction. Capacities do not bind.")


def _print_comparison(summary: pd.DataFrame, expected: dict[str, float]) -> None:
    print("Comparison with the unconstrained analytical baseline:")
    fields = (
        "chp_steam_gwh",
        "boiler_steam_gwh",
        "chp_electricity_gwh",
        "total_fuel_gwh",
        "net_grid_import_gwh",
    )
    for field in fields:
        values = ", ".join(f"{scenario} {value:.3f}" for scenario, value in zip(summary["scenario"], summary[field]))
        print(f"  {field}: expected {expected[field]:.3f}; {values}")
    print()


if __name__ == "__main__":
    sys.exit(main())
