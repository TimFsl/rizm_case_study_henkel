#!/usr/bin/env python3
"""Build synthetic hourly demand profiles and write QA outputs."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.assumptions import AssumptionError, load_assumptions
from src.profiles import (
    ENERGY_TOLERANCE_MWH,
    ProfileError,
    build_demand_profile,
    save_normalized_week_plot,
    summarize_profile,
)

SCENARIOS = ("flat", "base", "variable")
SUMMARY_COLUMNS = [
    "scenario",
    "steam_energy_gwh",
    "steam_average_mw",
    "steam_min_mw",
    "steam_max_mw",
    "steam_min_to_average",
    "steam_max_to_average",
    "electricity_energy_gwh",
    "electricity_average_mw",
    "electricity_min_mw",
    "electricity_max_mw",
    "electricity_min_to_average",
    "electricity_max_to_average",
]


def main() -> int:
    try:
        assumptions = load_assumptions()
    except AssumptionError as exc:
        print(f"Assumptions check failed: {exc}", file=sys.stderr)
        return 1

    processed = ROOT / "data" / "processed"
    tables = ROOT / "outputs" / "tables"
    figures = ROOT / "outputs" / "figures"
    processed.mkdir(parents=True, exist_ok=True)
    tables.mkdir(parents=True, exist_ok=True)

    hours = int(assumptions.value("model", "hours_per_year"))
    steam_gwh = float(assumptions.value("demand", "annual_steam_heat_gwh"))
    electricity_gwh = float(assumptions.value("demand", "annual_electricity_gwh"))
    steam_average = steam_gwh * 1000.0 / hours
    electricity_average = electricity_gwh * 1000.0 / hours
    steam_peak_reference = float(assumptions.value("storen_reference", "steam_peak_2030_mw"))
    electricity_peak_reference = float(
        assumptions.value("storen_reference", "electricity_peak_2030_mw")
    )
    historical = _historical_steam_ratios(assumptions)

    print("Synthetic demand profiles")
    print(f"Configured steam annual average:       {steam_average:.3f} MW")
    print(f"Configured electricity annual average: {electricity_average:.3f} MW")
    print()
    _print_historical_reference(historical)

    rows = []
    warnings = []
    for scenario in SCENARIOS:
        try:
            profile = build_demand_profile(assumptions, scenario)
        except ProfileError as exc:
            print(f"{scenario}: {exc}", file=sys.stderr)
            return 1
        problems = _profile_problems(profile, hours, steam_gwh, electricity_gwh)
        print(scenario.upper())
        if problems:
            for problem in problems:
                print(f"  FAIL: {problem}")
            return 1
        summary = summarize_profile(profile)
        _print_scenario_checks(profile, summary, steam_gwh, electricity_gwh)
        scenario_warnings = _storen_warnings(
            scenario,
            summary,
            steam_peak_reference,
            electricity_peak_reference,
        )
        for warning in scenario_warnings:
            print(f"  {warning}")
            warnings.append(warning)
        _print_shape_comparison(summary, historical)
        print()

        output = profile.reset_index()
        output.to_csv(processed / f"demand_profile_{scenario}.csv", index=False)
        save_normalized_week_plot(
            profile,
            scenario,
            figures / f"demand_profile_{scenario}_week.png",
        )
        rows.append({"scenario": scenario, **summary})

    summary_frame = pd.DataFrame(rows, columns=SUMMARY_COLUMNS)
    summary_frame.to_csv(tables / "demand_profile_summary.csv", index=False, float_format="%.6f")
    print("SUMMARY")
    print(summary_frame.to_string(index=False, float_format=lambda value: f"{value:.4f}"))
    print()
    if warnings:
        print("StoREN plausibility warnings:")
        for warning in warnings:
            print(f"- {warning}")
    else:
        print("StoREN plausibility warnings: none")
    return 0


def _profile_problems(profile, hours: int, steam_gwh: float, electricity_gwh: float) -> list[str]:
    problems = []
    if len(profile) != hours:
        problems.append(f"timestep count is {len(profile)}, expected {hours}")
    if not profile.index.is_unique:
        problems.append("timestamps are not unique")
    targets = {
        "steam_demand_mw": steam_gwh * 1000.0,
        "electricity_demand_mw": electricity_gwh * 1000.0,
    }
    for column, target_mwh in targets.items():
        series = profile[column]
        if series.isna().any():
            problems.append(f"{column} contains NaN")
        if (series < 0).any():
            problems.append(f"{column} contains a negative value")
        energy_mwh = float(series.sum())
        if abs(energy_mwh - target_mwh) > ENERGY_TOLERANCE_MWH:
            problems.append(
                f"{column} annual energy is {energy_mwh:.6f} MWh, "
                f"expected {target_mwh:.6f} MWh"
            )
    return problems


def _print_scenario_checks(profile, summary: dict, steam_gwh: float, electricity_gwh: float) -> None:
    print(f"  Timesteps: {len(profile)}")
    print(f"  Unique timestamps: {profile.index.is_unique}")
    print(
        f"  Steam energy: {summary['steam_energy_gwh']:.6f} GWh "
        f"(target {steam_gwh:.6f} GWh)"
    )
    print(
        f"  Electricity energy: {summary['electricity_energy_gwh']:.6f} GWh "
        f"(target {electricity_gwh:.6f} GWh)"
    )
    print(
        "  Steam MW average/min/max: "
        f"{summary['steam_average_mw']:.3f} / "
        f"{summary['steam_min_mw']:.3f} / "
        f"{summary['steam_max_mw']:.3f}"
    )
    print(
        "  Electricity MW average/min/max: "
        f"{summary['electricity_average_mw']:.3f} / "
        f"{summary['electricity_min_mw']:.3f} / "
        f"{summary['electricity_max_mw']:.3f}"
    )


def _storen_warnings(
    scenario: str,
    summary: dict,
    steam_peak_mw: float,
    electricity_peak_mw: float,
) -> list[str]:
    warnings = []
    if summary["steam_max_mw"] > steam_peak_mw:
        warnings.append(
            f"WARNING: {scenario} synthetic steam peak exceeds "
            "StoREN 2030 modeled peak reference."
        )
    if summary["electricity_max_mw"] > electricity_peak_mw:
        warnings.append(
            f"WARNING: {scenario} synthetic electricity peak exceeds "
            "StoREN 2030 modeled peak reference."
        )
    return warnings


def _historical_steam_ratios(assumptions) -> dict[str, float]:
    annual_tonnes = float(assumptions.value("historical_reference", "steam_production_2012"))
    hours = float(assumptions.value("model", "hours_per_year"))
    minimum = float(assumptions.value("historical_reference", "steam_load_min_2012"))
    maximum = float(assumptions.value("historical_reference", "steam_load_max_2012"))
    average = annual_tonnes / hours
    return {
        "average_t_per_h": average,
        "min_to_average": minimum / average,
        "max_to_average": maximum / average,
    }


def _print_historical_reference(historical: dict[str, float]) -> None:
    print("Historical 2012 steam shape reference (dimensionless, not a constraint)")
    print(f"  Average mass flow: {historical['average_t_per_h']:.3f} t/h")
    print(f"  Min / average:     {historical['min_to_average']:.3f}")
    print(f"  Max / average:     {historical['max_to_average']:.3f}")
    print()


def _print_shape_comparison(summary: dict, historical: dict[str, float]) -> None:
    print(
        "  Steam min/average "
        f"{summary['steam_min_to_average']:.3f} "
        f"(2012 reference {historical['min_to_average']:.3f}); "
        "max/average "
        f"{summary['steam_max_to_average']:.3f} "
        f"(2012 reference {historical['max_to_average']:.3f})"
    )


if __name__ == "__main__":
    sys.exit(main())
