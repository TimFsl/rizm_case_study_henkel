#!/usr/bin/env python3
"""Base-profile CHP ramp sensitivity with regime-consistent tariff values.

The stored energy-only and same-tariff files are left unchanged.
Baseline cost uses the baseline's own consistent public tariff.
Optimized cost uses the tariff of that solve, and the transition value is
reported only when that solve is consistent with its own utilization hours.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.assumptions import AssumptionError, load_assumptions
from src.costs import (
    NETWORK_TARIFF_NAMES,
    CostError,
    commodity_electricity_prices,
    consistent_tariff_name,
    cost_baseline,
    network_cost_breakdown,
    read_network_tariff,
    regime_consistent_value_eur,
    utilization_hours,
)
from src.optimization import (
    ANNUAL_CHP_TOLERANCE_MWH,
    DISPATCH_VALUE_BASIS,
    FULL_FLEX,
    OPTIMIZATION_MODES,
    REDISPATCH_ONLY,
    OptimizationError,
    assign_screening_roles,
    chp_bound_hour_counts,
    chp_ramp_diagnostics,
    solve_dispatch,
)
from src.plotting import DEFAULT_WEEK_START, PlotInputError, plot_final_dispatch_week

RAMP_FRACTIONS = (0.25, 0.50, 1.00)
SUMMARY_COLUMNS = [
    "optimization_mode",
    "chp_ramp_fraction_per_hour",
    "baseline_tariff_regime",
    "optimized_tariff_scenario",
    "optimized_tariff_consistent",
    "baseline_cost_m_eur",
    "optimized_cost_m_eur",
    "regime_consistent_dispatch_value_m_eur",
    "regime_consistent_dispatch_value_eur_per_t",
    "dispatch_value_percent",
    "same_tariff_dispatch_value_m_eur",
    "baseline_chp_electricity_gwh",
    "optimized_chp_electricity_gwh",
    "baseline_grid_import_gwh",
    "optimized_grid_import_gwh",
    "baseline_grid_export_gwh",
    "optimized_grid_export_gwh",
    "baseline_peak_import_mw",
    "optimized_peak_import_mw",
    "baseline_utilization_hours",
    "optimized_utilization_hours",
    "number_of_hours_chp_at_or_near_max",
    "number_of_hours_chp_at_or_near_minimum_feasible",
    "number_of_hours_chp_ramp_up_binding",
    "number_of_hours_chp_ramp_down_binding",
    "max_observed_chp_ramp_mw",
    "screening_role",
]


def main() -> int:
    try:
        assumptions = load_assumptions()
        timezone = str(assumptions.value("model", "timezone"))
        prices = commodity_electricity_prices(
            _load_hourly(ROOT / "data" / "processed" / "electricity_day_ahead_prices.csv", timezone),
            assumptions,
        )
        baseline = _load_hourly(
            ROOT / "data" / "processed" / "baseline_dispatch_base.csv",
            timezone,
        )
        tariffs = {name: read_network_tariff(assumptions, name) for name in NETWORK_TARIFF_NAMES}
        threshold = tariffs["hv_high_utilization"].utilization_threshold_hours
    except (AssumptionError, CostError, OptimizationError) as exc:
        print(f"Final sensitivity failed: {exc}", file=sys.stderr)
        return 1

    baseline_hours = utilization_hours(
        float(baseline["grid_import_mw"].sum()),
        float(baseline["grid_import_mw"].max()),
    )
    baseline_regime = consistent_tariff_name(baseline_hours, threshold)
    if baseline_regime is None:
        print("Baseline utilization hours are undefined, so no tariff regime applies.", file=sys.stderr)
        return 1
    baseline_tariff = tariffs[baseline_regime]
    baseline_variable = float(
        cost_baseline(baseline, prices, assumptions)["total_variable_energy_cost_eur"].sum()
    )
    baseline_network = network_cost_breakdown(baseline["grid_import_mw"], baseline_tariff)
    baseline_screening = baseline_variable + float(baseline_network["total_eur"])
    baseline_chp_mwh = float(baseline["chp_electricity_mw"].sum())
    demand = baseline[["steam_demand_mw", "electricity_demand_mw"]]
    production_tonnes = float(assumptions.value("site", "annual_henkel_production_tonnes"))

    print("Base-profile CHP ramp sensitivity")
    print("Redispatch-only keeps annual CHP generation and drops the fixed hourly 52/48 split.")
    print("Full flex does not keep those annual totals.")
    print(
        f"Baseline tariff regime from utilization {baseline_hours:.0f} h: {baseline_regime}."
    )
    print("The optimizer does not choose that regime.")
    print("Boiler ramping is not constrained. Part-load efficiency curves are not modeled.")
    print(f"EUR/t basis: {DISPATCH_VALUE_BASIS}")
    print("Export-dependent value remains potentially optimistic because export capacity is unknown.")
    print()

    rows = []
    solved_frames: dict[tuple[str, float, str], tuple[pd.DataFrame, float]] = {}
    for ramp in RAMP_FRACTIONS:
        for mode in OPTIMIZATION_MODES:
            for tariff_name in NETWORK_TARIFF_NAMES:
                tariff = tariffs[tariff_name]
                same_tariff_baseline = baseline_variable + float(
                    network_cost_breakdown(baseline["grid_import_mw"], tariff)["total_eur"]
                )
                try:
                    result = solve_dispatch(
                        demand,
                        prices,
                        assumptions,
                        mode,
                        baseline_chp_electricity_mwh=baseline_chp_mwh,
                        network_tariff=tariff,
                        chp_ramp_fraction_per_hour=ramp,
                    )
                except (OptimizationError, CostError) as exc:
                    print(f"{mode} ramp {ramp:g} {tariff_name}: {exc}", file=sys.stderr)
                    return 1
                if mode == REDISPATCH_ONLY:
                    optimized_chp_mwh = float(result.frame["chp_electricity_mw"].sum())
                    if abs(optimized_chp_mwh - baseline_chp_mwh) > ANNUAL_CHP_TOLERANCE_MWH:
                        print(
                            "redispatch_only annual CHP electricity drifted from the baseline",
                            file=sys.stderr,
                        )
                        return 1
                optimized_network = network_cost_breakdown(result.frame["grid_import_mw"], tariff)
                optimized_variable = float(result.frame["total_variable_energy_cost_eur"].sum())
                optimized_screening = optimized_variable + float(optimized_network["total_eur"])
                consistent = bool(optimized_network["consistent"])
                if consistent:
                    value = regime_consistent_value_eur(baseline_screening, optimized_screening)
                    value_per_t = value / production_tonnes
                    percent = 100.0 * value / baseline_screening
                else:
                    value = None
                    value_per_t = None
                    percent = None
                ramp_stats = chp_ramp_diagnostics(
                    result.frame["chp_steam_mw"],
                    float(result.chp_ramp_mw_per_hour),
                )
                bounds = chp_bound_hour_counts(result.frame, assumptions)
                row = {
                    "optimization_mode": mode,
                    "chp_ramp_fraction_per_hour": ramp,
                    "baseline_tariff_regime": baseline_regime,
                    "optimized_tariff_scenario": tariff_name,
                    "optimized_tariff_consistent": consistent,
                    "baseline_cost_m_eur": baseline_screening / 1_000_000.0,
                    "optimized_cost_m_eur": optimized_screening / 1_000_000.0,
                    "regime_consistent_dispatch_value_m_eur": None if value is None else value / 1_000_000.0,
                    "regime_consistent_dispatch_value_eur_per_t": value_per_t,
                    "dispatch_value_percent": percent,
                    "same_tariff_dispatch_value_m_eur": (
                        same_tariff_baseline - optimized_screening
                    ) / 1_000_000.0,
                    "baseline_chp_electricity_gwh": baseline_chp_mwh / 1000.0,
                    "optimized_chp_electricity_gwh": float(result.frame["chp_electricity_mw"].sum()) / 1000.0,
                    "baseline_grid_import_gwh": float(baseline["grid_import_mw"].sum()) / 1000.0,
                    "optimized_grid_import_gwh": float(result.frame["grid_import_mw"].sum()) / 1000.0,
                    "baseline_grid_export_gwh": float(baseline["grid_export_mw"].sum()) / 1000.0,
                    "optimized_grid_export_gwh": float(result.frame["grid_export_mw"].sum()) / 1000.0,
                    "baseline_peak_import_mw": float(baseline["grid_import_mw"].max()),
                    "optimized_peak_import_mw": float(result.frame["grid_import_mw"].max()),
                    "baseline_utilization_hours": baseline_hours,
                    "optimized_utilization_hours": optimized_network["utilization_hours"],
                    "max_observed_chp_ramp_mw": ramp_stats["max_observed_chp_ramp_mw"],
                    "screening_role": "",
                }
                row.update(bounds)
                row.update(
                    {
                        "number_of_hours_chp_ramp_up_binding": ramp_stats[
                            "number_of_hours_chp_ramp_up_binding"
                        ],
                        "number_of_hours_chp_ramp_down_binding": ramp_stats[
                            "number_of_hours_chp_ramp_down_binding"
                        ],
                    }
                )
                rows.append(row)
                solved_frames[(mode, ramp, tariff_name)] = (
                    result.frame,
                    float(result.chp_ramp_mw_per_hour),
                )
                hours_text = (
                    "undefined"
                    if row["optimized_utilization_hours"] is None
                    or pd.isna(row["optimized_utilization_hours"])
                    else f"{float(row['optimized_utilization_hours']):.0f} h"
                )
                flag = "consistent" if consistent else "NOT consistent"
                print(
                    f"{mode:16} ramp {ramp:.2f} {tariff_name:22} "
                    f"max step {ramp_stats['max_observed_chp_ramp_mw']:.2f} MW "
                    f"(limit {float(result.chp_ramp_mw_per_hour):.2f})  "
                    f"utilization {hours_text}  {flag}"
                )

    assign_screening_roles(rows)
    summary = pd.DataFrame(rows, columns=SUMMARY_COLUMNS)
    summary_path = ROOT / "outputs" / "tables" / "dispatch_final_sensitivity.csv"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary.to_csv(summary_path, index=False)
    print()
    print(f"Summary: {summary_path}")
    _print_regime_table(summary)
    _print_primary(summary)

    primary_rows = summary[summary["screening_role"] == "primary_screening"]
    if len(primary_rows) == 1:
        chosen = primary_rows.iloc[0]
        frame, ramp_mw = solved_frames[
            (
                str(chosen["optimization_mode"]),
                float(chosen["chp_ramp_fraction_per_hour"]),
                str(chosen["optimized_tariff_scenario"]),
            )
        ]
        figure_path = ROOT / "outputs" / "figures" / "final_dispatch_primary_week.png"
        try:
            plot_final_dispatch_week(
                frame,
                DEFAULT_WEEK_START,
                figure_path,
                ramp_mw_per_hour=ramp_mw,
            )
        except PlotInputError as exc:
            print(f"Primary-week figure skipped: {exc}", file=sys.stderr)
            return 1
        print(f"Figure: {figure_path}")
    print(
        "Export-dependent value remains potentially optimistic because the actual "
        "site export capacity is unknown."
    )
    return 0


def _print_regime_table(summary: pd.DataFrame) -> None:
    print()
    print("Regime-consistent values only. Blank value means that solve is not internally consistent.")
    print(
        f"{'case':28} {'ramp':>6} {'M EUR/a':>10} {'EUR/t':>8} "
        f"{'CHP GWh':>8} {'import':>8} {'export':>8} {'role':22}"
    )
    order = [REDISPATCH_ONLY, FULL_FLEX]
    ordered = summary.copy()
    ordered["_mode"] = ordered["optimization_mode"].map({name: i for i, name in enumerate(order)})
    ordered = ordered.sort_values(["_mode", "chp_ramp_fraction_per_hour", "optimized_tariff_scenario"])
    for _, row in ordered.iterrows():
        if not bool(row["optimized_tariff_consistent"]):
            value = ""
            per_t = ""
        else:
            value = f"{row['regime_consistent_dispatch_value_m_eur']:.3f}"
            per_t = f"{row['regime_consistent_dispatch_value_eur_per_t']:.2f}"
        label = f"{row['optimization_mode']} {row['optimized_tariff_scenario']}"
        role = str(row["screening_role"])
        if role == "primary_screening":
            role = "PRIMARY SCREENING CASE"
        elif role == "upper_bound":
            role = "UPPER-BOUND CASE"
        print(
            f"{label:28} {row['chp_ramp_fraction_per_hour']:6.2f} {value:>10} {per_t:>8} "
            f"{row['optimized_chp_electricity_gwh']:8.1f} "
            f"{row['optimized_grid_import_gwh']:8.1f} "
            f"{row['optimized_grid_export_gwh']:8.1f} {role:22}"
        )
    print()


def _print_primary(summary: pd.DataFrame) -> None:
    for role, title in (
        ("primary_screening", "Primary screening dispatch value"),
        ("upper_bound", "Structural flexibility upper-bound case"),
    ):
        chosen = summary[summary["screening_role"] == role]
        if chosen.empty:
            print(f"{title}: no internally consistent solve at 50% ramp.")
            continue
        row = chosen.iloc[0]
        print(
            f"{title}: {row['optimization_mode']}, ramp {row['chp_ramp_fraction_per_hour']:.0%}, "
            f"{row['optimized_tariff_scenario']}"
        )
        print(
            f"  {row['regime_consistent_dispatch_value_m_eur']:.3f} M EUR/a, "
            f"{row['regime_consistent_dispatch_value_eur_per_t']:.2f} EUR/t, "
            f"{row['dispatch_value_percent']:.2f}% of baseline screening cost"
        )
        print(
            f"  CHP electricity {row['baseline_chp_electricity_gwh']:.1f} -> "
            f"{row['optimized_chp_electricity_gwh']:.1f} GWh, "
            f"import {row['baseline_grid_import_gwh']:.1f} -> {row['optimized_grid_import_gwh']:.1f} GWh, "
            f"export {row['baseline_grid_export_gwh']:.1f} -> {row['optimized_grid_export_gwh']:.1f} GWh, "
            f"peak import {row['baseline_peak_import_mw']:.2f} -> {row['optimized_peak_import_mw']:.2f} MW"
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
