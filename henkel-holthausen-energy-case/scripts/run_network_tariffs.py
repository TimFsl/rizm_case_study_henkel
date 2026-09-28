#!/usr/bin/env python3
"""Value price-responsive dispatch under two fixed Düsseldorf HV tariff proxies.

The optimizer does not choose the tariff. Each scenario is solved twice.
Energy-only dispatch files are left unchanged.
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
    cost_baseline,
    network_cost_breakdown,
    read_network_tariff,
)
from src.optimization import (
    DISPATCH_VALUE_BASIS,
    FULL_FLEX,
    OPTIMIZATION_MODES,
    OptimizationError,
    solve_dispatch,
    summarize_network_tariff,
    validate_optimized_dispatch,
)
from src.plotting import DEFAULT_WEEK_START, PlotInputError, plot_grid_import_peak_week

SCENARIOS = ("flat", "base", "variable")
SUMMARY_COLUMNS = [
    "scenario",
    "optimization_mode",
    "tariff_scenario",
    "network_energy_charge_eur_mwh",
    "network_demand_charge_eur_kw_a",
    "baseline_grid_import_gwh",
    "optimized_grid_import_gwh",
    "baseline_peak_grid_import_mw",
    "optimized_peak_grid_import_mw",
    "baseline_utilization_hours",
    "optimized_utilization_hours",
    "tariff_regime_consistent",
    "baseline_network_energy_cost_m_eur",
    "baseline_network_demand_charge_m_eur",
    "baseline_total_network_cost_m_eur",
    "optimized_network_energy_cost_m_eur",
    "optimized_network_demand_charge_m_eur",
    "optimized_total_network_cost_m_eur",
    "baseline_total_screening_cost_m_eur",
    "optimized_total_screening_cost_m_eur",
    "dispatch_value_m_eur",
    "dispatch_value_eur_per_t_henkel_production",
    "dispatch_value_percent",
    "optimized_chp_electricity_gwh",
    "optimized_chp_steam_gwh",
    "optimized_boiler_steam_gwh",
    "optimized_total_fuel_gwh",
    "optimized_grid_export_gwh",
    "optimized_peak_grid_export_mw",
]


def main() -> int:
    try:
        assumptions = load_assumptions()
        timezone = str(assumptions.value("model", "timezone"))
        _print_capacity_audit(assumptions)
        stored_prices = _load_hourly(
            ROOT / "data" / "processed" / "electricity_day_ahead_prices.csv",
            timezone,
        )
        prices = commodity_electricity_prices(stored_prices, assumptions)
        tariffs = [read_network_tariff(assumptions, name) for name in NETWORK_TARIFF_NAMES]
    except (AssumptionError, CostError, OptimizationError) as exc:
        print(f"Network-tariff optimization failed: {exc}", file=sys.stderr)
        return 1

    discount = float(assumptions.value("market", "electricity_export_discount_eur_per_mwh"))
    print("Düsseldorf high-voltage network-tariff screening")
    print("Two fixed tariff structures. The optimizer does not switch between them.")
    print("High voltage is an assumption from the ~64 MW connection, not a verified billing level.")
    print(f"Commodity import price is the day-ahead price. Export price is day-ahead minus {discount:.1f} EUR/MWh.")
    print("The 5 EUR/MWh export discount is a marketing / balancing / transaction allowance, not a network charge.")
    print("The generic import adder stays at zero so it is not stacked on the explicit tariff.")
    print(f"EUR/t basis: {DISPATCH_VALUE_BASIS}")
    print("Export-dependent value remains potentially optimistic because the actual site export capacity is unknown.")
    print()

    rows = []
    for scenario in SCENARIOS:
        try:
            baseline = _load_hourly(
                ROOT / "data" / "processed" / f"baseline_dispatch_{scenario}.csv",
                timezone,
            )
            demand = baseline[["steam_demand_mw", "electricity_demand_mw"]]
            baseline_variable = float(
                cost_baseline(baseline, prices, assumptions)["total_variable_energy_cost_eur"].sum()
            )
        except (OptimizationError, CostError, FileNotFoundError) as exc:
            print(f"{scenario}: {exc}", file=sys.stderr)
            return 1
        baseline_chp_mwh = float(baseline["chp_electricity_mw"].sum())
        for tariff in tariffs:
            baseline_network = network_cost_breakdown(baseline["grid_import_mw"], tariff)
            baseline_screening = baseline_variable + float(baseline_network["total_eur"])
            for mode in OPTIMIZATION_MODES:
                try:
                    result = solve_dispatch(
                        demand,
                        prices,
                        assumptions,
                        mode,
                        baseline_chp_electricity_mwh=baseline_chp_mwh,
                        network_tariff=tariff,
                    )
                    validate_optimized_dispatch(
                        result,
                        assumptions,
                        baseline_chp_electricity_mwh=baseline_chp_mwh,
                        baseline_screening_cost_eur=baseline_screening,
                    )
                    row = summarize_network_tariff(
                        scenario,
                        result,
                        baseline,
                        baseline_variable,
                        tariff,
                        assumptions,
                    )
                except (OptimizationError, CostError) as exc:
                    print(f"{scenario} {mode} {tariff.name}: {exc}", file=sys.stderr)
                    return 1
                out_path = (
                    ROOT
                    / "data"
                    / "processed"
                    / f"optimized_dispatch_{scenario}_{mode}_{tariff.name}.csv"
                )
                result.frame.to_csv(out_path, index_label="timestamp")
                rows.append(row)
                hours = row["optimized_utilization_hours"]
                if hours is None or pd.isna(hours):
                    hours_text = "undefined (peak import is zero)"
                else:
                    hours_text = f"{hours:.0f} h"
                consistent = "consistent" if row["tariff_regime_consistent"] else "NOT consistent"
                print(
                    f"{scenario:8} {mode:16} {tariff.name:22} "
                    f"value {row['dispatch_value_m_eur']:.3f} M EUR  "
                    f"utilization {hours_text}  {consistent}"
                )
                if scenario == "base" and mode == FULL_FLEX and tariff.name == "hv_high_utilization":
                    figure_path = (
                        ROOT / "outputs" / "figures" / "baseline_vs_optimized_import_peak_week.png"
                    )
                    try:
                        plot_grid_import_peak_week(
                            baseline,
                            result.frame,
                            float(row["optimized_peak_grid_import_mw"]),
                            DEFAULT_WEEK_START,
                            figure_path,
                        )
                    except PlotInputError as exc:
                        print(f"Import-peak figure skipped: {exc}", file=sys.stderr)
                        return 1

    summary = pd.DataFrame(rows, columns=SUMMARY_COLUMNS)
    summary_path = ROOT / "outputs" / "tables" / "optimization_network_tariff_summary.csv"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary.to_csv(summary_path, index=False)
    print()
    print(f"Summary: {summary_path}")
    _print_base_comparison(summary)
    _print_export_flag(summary)
    print(
        "Modeled peaks come from the synthetic hourly profile. Actual billed peaks can be "
        "higher because of outages, maintenance, and operating states that are not in these profiles."
    )
    print(
        "Netzgesellschaft Düsseldorf publishes reserve-capacity pricing for customers with "
        "decentralized generation. That product is not modeled. Henkel's arrangement is unknown."
    )
    return 0


def _print_capacity_audit(assumptions) -> None:
    chp = float(assumptions.value("chp", "max_heat_output_mw"))
    boiler = float(assumptions.value("boiler", "max_heat_output_mw"))
    grid = float(assumptions.value("grid", "import_capacity_mw"))
    print("Capacity constraints already in the optimizer, read from assumptions.yaml:")
    print(f"  CHP heat <= {chp:.1f} MW_th")
    print(f"  boiler heat <= {boiler:.1f} MW_th")
    print(f"  grid import <= {grid:.1f} MW_el")
    print("  steam demand must stay within CHP capacity plus boiler capacity")
    print("  no separate 89 MW electrical cap; CHP electricity follows heat times power-to-heat")
    print("  no export-capacity constraint")
    print()


def _print_base_comparison(summary: pd.DataFrame) -> None:
    energy_only_path = ROOT / "outputs" / "tables" / "optimization_summary.csv"
    if not energy_only_path.is_file():
        print("Energy-only summary is missing, so the base comparison was skipped.")
        return
    energy_only = pd.read_csv(energy_only_path)
    print()
    print("BASE demand comparison. Energy-only used export discount 0 from the stored price file.")
    print("Tariff cases use export discount 5 EUR/MWh plus the explicit network tariff.")
    print(
        f"{'case':28} {'mode':16} {'value M EUR':>12} {'EUR/t':>8} "
        f"{'import GWh':>11} {'export GWh':>11} {'peak MW':>8} "
        f"{'network M EUR':>14} {'CHP el GWh':>11}"
    )
    base_energy = energy_only[energy_only["scenario"] == "base"]
    for _, row in base_energy.iterrows():
        print(
            f"{'energy_only':28} {row['optimization_mode']:16} "
            f"{row['dispatch_value_m_eur']:12.3f} "
            f"{row['dispatch_value_eur_per_t_henkel_production']:8.2f} "
            f"{row['optimized_grid_import_gwh']:11.2f} "
            f"{row['optimized_grid_export_gwh']:11.2f} "
            f"{row['optimized_peak_grid_import_mw']:8.2f} "
            f"{0.0:14.3f} "
            f"{row['optimized_chp_electricity_gwh']:11.2f}"
        )
    base_tariff = summary[summary["scenario"] == "base"]
    for _, row in base_tariff.iterrows():
        print(
            f"{row['tariff_scenario']:28} {row['optimization_mode']:16} "
            f"{row['dispatch_value_m_eur']:12.3f} "
            f"{row['dispatch_value_eur_per_t_henkel_production']:8.2f} "
            f"{row['optimized_grid_import_gwh']:11.2f} "
            f"{row['optimized_grid_export_gwh']:11.2f} "
            f"{row['optimized_peak_grid_import_mw']:8.2f} "
            f"{row['optimized_total_network_cost_m_eur']:14.3f} "
            f"{row['optimized_chp_electricity_gwh']:11.2f}"
        )
    print()


def _print_export_flag(summary: pd.DataFrame) -> None:
    print(
        "Export-dependent value remains potentially optimistic because the actual "
        "site export capacity is unknown."
    )
    for _, row in summary.iterrows():
        print(
            f"  {row['scenario']} {row['optimization_mode']} {row['tariff_scenario']}: "
            f"export {row['optimized_grid_export_gwh']:.2f} GWh/a, "
            f"peak export {row['optimized_peak_grid_export_mw']:.2f} MW"
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
