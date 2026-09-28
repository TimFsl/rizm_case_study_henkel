#!/usr/bin/env python3
"""Base-profile export-capacity sensitivity at the 50%/h CHP ramp.

Earlier dispatch files stay unchanged. None means the export limit is omitted.
Baseline cost uses the baseline's consistent tariff. The dispatch value is
reported only when the optimized solve matches its own tariff.
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
    BOUND_TOLERANCE_MW,
    DISPATCH_VALUE_BASIS,
    FULL_FLEX,
    OPTIMIZATION_MODES,
    REDISPATCH_ONLY,
    OptimizationError,
    solve_dispatch,
)

RAMP_FRACTION = 0.50
EXPORT_LABELS = {5.0: "conservative", 10.0: "primary", 20.0: "high_export"}
SUMMARY_COLUMNS = [
    "optimization_mode",
    "export_capacity_mw",
    "export_case_label",
    "baseline_tariff_regime",
    "optimized_tariff_regime",
    "optimized_tariff_consistent",
    "chp_ramp_fraction_per_hour",
    "baseline_total_cost_m_eur",
    "optimized_total_cost_m_eur",
    "dispatch_value_m_eur",
    "dispatch_value_eur_per_t",
    "dispatch_value_percent",
    "optimized_chp_electricity_gwh",
    "optimized_chp_steam_gwh",
    "optimized_boiler_steam_gwh",
    "optimized_grid_import_gwh",
    "optimized_grid_export_gwh",
    "optimized_peak_grid_import_mw",
    "optimized_peak_grid_export_mw",
    "export_constraint_binding_hours",
    "max_export_mw",
    "optimized_utilization_hours",
    "screening_role",
]


def main() -> int:
    try:
        assumptions = load_assumptions()
        timezone = str(assumptions.value("model", "timezone"))
        primary_export = float(assumptions.value("grid", "export_capacity_mw"))
        stored = [float(item) for item in assumptions.parameter("grid", "export_capacity_mw")["sensitivity"]]
        export_cases = [(value, EXPORT_LABELS.get(value, f"{value:g}_mw")) for value in stored]
        export_cases.append((None, "unconstrained"))
        prices = commodity_electricity_prices(
            _load_hourly(ROOT / "data" / "processed" / "electricity_day_ahead_prices.csv", timezone),
            assumptions,
        )
        baseline = _load_hourly(ROOT / "data" / "processed" / "baseline_dispatch_base.csv", timezone)
        tariffs = {name: read_network_tariff(assumptions, name) for name in NETWORK_TARIFF_NAMES}
        threshold = tariffs["hv_high_utilization"].utilization_threshold_hours
    except (AssumptionError, CostError, OptimizationError) as exc:
        print(f"Export sensitivity failed: {exc}", file=sys.stderr)
        return 1

    baseline_hours = utilization_hours(
        float(baseline["grid_import_mw"].sum()),
        float(baseline["grid_import_mw"].max()),
    )
    baseline_regime = consistent_tariff_name(baseline_hours, threshold)
    if baseline_regime is None:
        print("Baseline utilization hours are undefined.", file=sys.stderr)
        return 1
    baseline_tariff = tariffs[baseline_regime]
    baseline_variable = float(
        cost_baseline(baseline, prices, assumptions)["total_variable_energy_cost_eur"].sum()
    )
    baseline_screening = baseline_variable + float(
        network_cost_breakdown(baseline["grid_import_mw"], baseline_tariff)["total_eur"]
    )
    baseline_chp_mwh = float(baseline["chp_electricity_mw"].sum())
    demand = baseline[["steam_demand_mw", "electricity_demand_mw"]]
    production_tonnes = float(assumptions.value("site", "annual_henkel_production_tonnes"))

    print("Base-profile export-capacity sensitivity")
    print(f"CHP ramp {RAMP_FRACTION:.0%} of heat capacity per hour.")
    print(f"Primary export limit from assumptions: {primary_export:.0f} MW. Not the 64 MW import connection.")
    print(f"Baseline tariff regime from utilization {baseline_hours:.0f} h: {baseline_regime}.")
    print(f"EUR/t basis: {DISPATCH_VALUE_BASIS}")
    print()

    rows = []
    for export_mw, label in export_cases:
        for mode in OPTIMIZATION_MODES:
            for tariff_name in NETWORK_TARIFF_NAMES:
                tariff = tariffs[tariff_name]
                try:
                    result = solve_dispatch(
                        demand,
                        prices,
                        assumptions,
                        mode,
                        baseline_chp_electricity_mwh=baseline_chp_mwh,
                        network_tariff=tariff,
                        chp_ramp_fraction_per_hour=RAMP_FRACTION,
                        export_capacity_mw=export_mw,
                    )
                except (OptimizationError, CostError) as exc:
                    print(f"{mode} export {label} {tariff_name}: {exc}", file=sys.stderr)
                    return 1
                if mode == REDISPATCH_ONLY:
                    optimized_chp = float(result.frame["chp_electricity_mw"].sum())
                    if abs(optimized_chp - baseline_chp_mwh) > ANNUAL_CHP_TOLERANCE_MWH:
                        print("redispatch annual CHP electricity drifted", file=sys.stderr)
                        return 1
                optimized_network = network_cost_breakdown(result.frame["grid_import_mw"], tariff)
                optimized_screening = float(result.frame["total_variable_energy_cost_eur"].sum()) + float(
                    optimized_network["total_eur"]
                )
                consistent = bool(optimized_network["consistent"])
                if consistent:
                    value = regime_consistent_value_eur(baseline_screening, optimized_screening)
                    per_t = value / production_tonnes
                    percent = 100.0 * value / baseline_screening
                else:
                    value = per_t = percent = None
                export = result.frame["grid_export_mw"].astype(float)
                max_export = float(export.max())
                if export_mw is None:
                    binding_hours = 0
                else:
                    binding_hours = int((export >= float(export_mw) - BOUND_TOLERANCE_MW).sum())
                rows.append(
                    {
                        "optimization_mode": mode,
                        "export_capacity_mw": export_mw,
                        "export_case_label": label,
                        "baseline_tariff_regime": baseline_regime,
                        "optimized_tariff_regime": tariff_name,
                        "optimized_tariff_consistent": consistent,
                        "chp_ramp_fraction_per_hour": RAMP_FRACTION,
                        "baseline_total_cost_m_eur": baseline_screening / 1_000_000.0,
                        "optimized_total_cost_m_eur": optimized_screening / 1_000_000.0,
                        "dispatch_value_m_eur": None if value is None else value / 1_000_000.0,
                        "dispatch_value_eur_per_t": per_t,
                        "dispatch_value_percent": percent,
                        "optimized_chp_electricity_gwh": float(result.frame["chp_electricity_mw"].sum()) / 1000.0,
                        "optimized_chp_steam_gwh": float(result.frame["chp_steam_mw"].sum()) / 1000.0,
                        "optimized_boiler_steam_gwh": float(result.frame["boiler_steam_mw"].sum()) / 1000.0,
                        "optimized_grid_import_gwh": float(result.frame["grid_import_mw"].sum()) / 1000.0,
                        "optimized_grid_export_gwh": float(export.sum()) / 1000.0,
                        "optimized_peak_grid_import_mw": float(result.frame["grid_import_mw"].max()),
                        "optimized_peak_grid_export_mw": max_export,
                        "export_constraint_binding_hours": binding_hours,
                        "max_export_mw": max_export,
                        "optimized_utilization_hours": optimized_network["utilization_hours"],
                        "screening_role": "",
                    }
                )
                hours = rows[-1]["optimized_utilization_hours"]
                hours_text = "undefined" if hours is None or pd.isna(hours) else f"{float(hours):.0f} h"
                cap_text = "unconstrained" if export_mw is None else f"{export_mw:.0f} MW"
                print(
                    f"{mode:16} {cap_text:14} {tariff_name:22} "
                    f"max export {max_export:.2f} MW  binding {binding_hours:4d} h  "
                    f"utilization {hours_text}  {'consistent' if consistent else 'NOT consistent'}"
                )

    _assign_roles(rows, primary_export)
    summary = pd.DataFrame(rows, columns=SUMMARY_COLUMNS)
    summary_path = ROOT / "outputs" / "tables" / "dispatch_export_sensitivity.csv"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary.to_csv(summary_path, index=False)
    print()
    print(f"Summary: {summary_path}")
    _print_table(summary)
    _print_monotonicity(summary, export_cases)
    _print_versus_previous(summary)
    return 0


def _assign_roles(rows: list[dict], primary_export_mw: float) -> None:
    _mark(rows, REDISPATCH_ONLY, primary_export_mw, "primary_screening")
    _mark(rows, FULL_FLEX, primary_export_mw, "upper_bound")
    _mark(rows, FULL_FLEX, None, "unconstrained_upper_bound")


def _mark(rows: list[dict], mode: str, export_mw: float | None, role: str) -> None:
    candidates = []
    for row in rows:
        if row["optimization_mode"] != mode or not row["optimized_tariff_consistent"]:
            continue
        if export_mw is None:
            if row["export_capacity_mw"] is not None:
                continue
        elif row["export_capacity_mw"] is None or abs(float(row["export_capacity_mw"]) - export_mw) > 1e-9:
            continue
        candidates.append(row)
    if not candidates:
        return
    baseline_regime = candidates[0]["baseline_tariff_regime"]
    same = [row for row in candidates if row["optimized_tariff_regime"] == baseline_regime]
    chosen = same[0] if same else candidates[0]
    chosen["screening_role"] = role


def _print_table(summary: pd.DataFrame) -> None:
    print()
    print("Regime-consistent values. Blank value means the solve does not match its tariff.")
    print(f"{'case':42} {'limit':>14} {'M EUR/a':>10} {'EUR/t':>8} {'export GWh':>11} {'peak MW':>8}")
    ordered = summary.copy()
    ordered["_mode"] = ordered["optimization_mode"].map({REDISPATCH_ONLY: 0, FULL_FLEX: 1})
    ordered["_cap"] = ordered["export_capacity_mw"].fillna(1e9)
    ordered = ordered.sort_values(["_mode", "_cap", "optimized_tariff_regime"])
    for _, row in ordered.iterrows():
        if not bool(row["optimized_tariff_consistent"]):
            continue
        limit = "unconstrained" if pd.isna(row["export_capacity_mw"]) else f"{row['export_capacity_mw']:.0f} MW"
        role = ""
        if row["screening_role"] == "primary_screening":
            role = "  PRIMARY"
        elif row["screening_role"] == "upper_bound":
            role = "  UPPER BOUND 10 MW"
        elif row["screening_role"] == "unconstrained_upper_bound":
            role = "  UNCONSTRAINED UPPER BOUND"
        label = f"{row['optimization_mode']} {row['optimized_tariff_regime']}"
        print(
            f"{label:42} {limit:>14} {row['dispatch_value_m_eur']:10.3f} "
            f"{row['dispatch_value_eur_per_t']:8.2f} {row['optimized_grid_export_gwh']:11.2f} "
            f"{row['optimized_peak_grid_export_mw']:8.2f}{role}"
        )
    print()


def _print_monotonicity(summary: pd.DataFrame, export_cases: list[tuple[float | None, str]]) -> None:
    print("Observed peak export should not fall as the allowed export capacity rises.")
    print("Regime-consistent value should generally not fall either. A drop is reported, not overwritten.")
    for mode in OPTIMIZATION_MODES:
        for tariff_name in NETWORK_TARIFF_NAMES:
            block = summary[
                (summary["optimization_mode"] == mode)
                & (summary["optimized_tariff_regime"] == tariff_name)
            ]
            peaks = []
            values = []
            for cap, _label in export_cases:
                subset = block[block["export_capacity_mw"].isna()] if cap is None else block[
                    block["export_capacity_mw"] == cap
                ]
                if subset.empty:
                    continue
                peaks.append(float(subset.iloc[0]["max_export_mw"]))
                if bool(subset.iloc[0]["optimized_tariff_consistent"]):
                    values.append(float(subset.iloc[0]["dispatch_value_m_eur"]))
            peak_ok = all(peaks[i] <= peaks[i + 1] + 1e-6 for i in range(len(peaks) - 1))
            value_ok = all(values[i] <= values[i + 1] + 1e-6 for i in range(len(values) - 1))
            print(
                f"  {mode} {tariff_name}: peak export MW "
                f"{', '.join(f'{peak:.2f}' for peak in peaks)} "
                f"({'nondecreasing' if peak_ok else 'NOT nondecreasing'})"
            )
            if values:
                print(
                    f"    consistent value M EUR {', '.join(f'{value:.3f}' for value in values)} "
                    f"({'nondecreasing' if value_ok else 'NOT nondecreasing'})"
                )
    print()


def _print_versus_previous(summary: pd.DataFrame) -> None:
    prior_path = ROOT / "outputs" / "tables" / "dispatch_final_sensitivity.csv"
    primary = summary[summary["screening_role"] == "primary_screening"]
    if primary.empty or not prior_path.is_file():
        return
    prior = pd.read_csv(prior_path)
    old = prior[prior["screening_role"] == "primary_screening"]
    if old.empty:
        return
    new = primary.iloc[0]
    previous_m = float(old.iloc[0]["regime_consistent_dispatch_value_m_eur"])
    previous_t = float(old.iloc[0]["regime_consistent_dispatch_value_eur_per_t"])
    delta_m = float(new["dispatch_value_m_eur"]) - previous_m
    delta_t = float(new["dispatch_value_eur_per_t"]) - previous_t
    delta_pct = 100.0 * delta_m / previous_m
    print("Previous primary case: redispatch-only, 50%/h ramp, unconstrained export")
    print(f"  {previous_m:.3f} M EUR/a, {previous_t:.2f} EUR/t")
    print("New primary case: redispatch-only, 50%/h ramp, 10 MW export limit")
    print(
        f"  {float(new['dispatch_value_m_eur']):.3f} M EUR/a, "
        f"{float(new['dispatch_value_eur_per_t']):.2f} EUR/t"
    )
    print(f"Change from the export limit: {delta_m:.3f} M EUR/a, {delta_t:.2f} EUR/t, {delta_pct:.1f}%")
    print(
        f"10 MW binding hours: {int(new['export_constraint_binding_hours'])}, "
        f"annual export {float(new['optimized_grid_export_gwh']):.2f} GWh, "
        f"peak export {float(new['optimized_peak_grid_export_mw']):.2f} MW"
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
