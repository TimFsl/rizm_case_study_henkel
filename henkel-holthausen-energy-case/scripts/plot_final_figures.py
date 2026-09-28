#!/usr/bin/env python3
"""Draw the final submission figures from frozen results.

The only new solve is the already frozen Business Case 1 primary dispatch,
which was not saved as an hourly file. No new capacity, tariff, or fuel case
is introduced, and no result table is rewritten.
"""

from __future__ import annotations

import sys
from pathlib import Path

import matplotlib.pyplot as plt
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
from src.plotting import DEFAULT_WEEK_START, plot_final_dispatch_week

FIGURES = ROOT / "outputs" / "figures"
TABLES = ROOT / "outputs" / "tables"


def _load_hourly(path: Path, timezone: str) -> pd.DataFrame:
    frame = pd.read_csv(path, parse_dates=["timestamp"])
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True).dt.tz_convert(timezone)
    return frame.set_index("timestamp").sort_index()


def _bc1_week(assumptions) -> None:
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
    result = solve_dispatch(
        baseline[["steam_demand_mw", "electricity_demand_mw"]],
        prices,
        assumptions,
        REDISPATCH_ONLY,
        baseline_chp_electricity_mwh=float(baseline["chp_electricity_mw"].sum()),
        network_tariff=tariff,
        chp_ramp_fraction_per_hour=0.50,
        export_capacity_mw=10.0,
    )
    optimized = float(result.frame["total_variable_energy_cost_eur"].sum()) + float(
        network_cost_breakdown(result.frame["grid_import_mw"], tariff)["total_eur"]
    )
    tonnes = float(assumptions.value("site", "annual_henkel_production_tonnes"))
    value = regime_consistent_value_eur(baseline_cost, optimized)
    stored = pd.read_csv(TABLES / "dispatch_export_sensitivity.csv")
    primary = stored[stored["screening_role"] == "primary_screening"].iloc[0]
    if abs(value / 1e6 - float(primary["dispatch_value_m_eur"])) > 1e-4:
        raise SystemExit("Primary week dispatch does not match the frozen export table")
    if abs(value / tonnes - float(primary["dispatch_value_eur_per_t"])) > 1e-4:
        raise SystemExit("Primary EUR/t does not match the frozen export table")
    plot_final_dispatch_week(
        result.frame,
        DEFAULT_WEEK_START,
        FIGURES / "final_bc1_dispatch_week.png",
        ramp_mw_per_hour=55.0,
        title=(
            "Business Case 1 primary dispatch, redispatch only, "
            f"10 MW export, week of {DEFAULT_WEEK_START}"
        ),
    )
    print(f"BC1 week matches frozen value {value / 1e6:.6f} M EUR/a")


def _bc1_export() -> None:
    table = pd.read_csv(TABLES / "dispatch_export_sensitivity.csv")
    rows = table[
        (table["optimization_mode"] == "redispatch_only")
        & (table["optimized_tariff_regime"] == "hv_high_utilization")
        & (table["optimized_tariff_consistent"] == True)  # noqa: E712
    ].copy()
    rows["label"] = rows["export_capacity_mw"].apply(
        lambda value: "Unconstrained" if pd.isna(value) else f"{value:.0f} MW"
    )
    order = ["5 MW", "10 MW", "20 MW", "Unconstrained"]
    rows["label"] = pd.Categorical(rows["label"], order, ordered=True)
    rows = rows.sort_values("label")
    figure, axis = plt.subplots(figsize=(7, 4.2))
    colors = ["#9ecae1" if label != "10 MW" else "#08519c" for label in rows["label"]]
    axis.bar(rows["label"].astype(str), rows["dispatch_value_eur_per_t"], color=colors)
    axis.set_ylabel("Site-level screening value, EUR/t")
    axis.set_xlabel("Export capacity")
    axis.set_title("Business Case 1 export sensitivity\n10 MW is the primary screening assumption")
    figure.tight_layout()
    figure.savefig(FIGURES / "final_bc1_export_sensitivity.png", dpi=150, bbox_inches="tight")
    plt.close(figure)


def _bc2_npv() -> None:
    current = pd.read_csv(TABLES / "electric_boiler_current_vs_forward.csv")
    forward = pd.read_csv(TABLES / "electric_boiler_forward_npv.csv")
    mid = forward[forward["scenario"] == "mid"].sort_values("capacity_mw")
    figure, axis = plt.subplots(figsize=(7, 4.2))
    axis.plot(
        current["capacity_mw"],
        current["current_cost_npv_m_eur"],
        marker="o",
        color="#7f7f7f",
        label="Current-cost screening",
    )
    axis.plot(
        mid["capacity_mw"],
        mid["npv_m_eur"],
        marker="o",
        color="#1f77b4",
        label="Forward MID scenario",
    )
    axis.axhline(0.0, color="#666666", linewidth=0.8)
    axis.axvline(20.0, color="#d62728", linewidth=1.0, linestyle="--", label="Best screened MID capacity")
    axis.set_xlabel("Electrode-boiler capacity, MW thermal")
    axis.set_ylabel("NPV, M EUR")
    axis.set_xticks([5, 10, 20, 30])
    axis.set_title("Business Case 2 NPV by size")
    axis.legend(frameon=False)
    figure.tight_layout()
    figure.savefig(FIGURES / "final_bc2_npv_by_size.png", dpi=150, bbox_inches="tight")
    plt.close(figure)


def _schematic() -> None:
    figure, axis = plt.subplots(figsize=(8, 5))
    axis.set_xlim(0, 10)
    axis.set_ylim(0, 8)
    axis.axis("off")
    boxes = {
        (3.2, 6.2, 3.6, 1.0): "Natural gas / biomethane",
        (1.2, 4.0, 3.0, 1.1): "CHP",
        (5.8, 4.0, 3.0, 1.1): "Gas boiler",
        (1.2, 1.6, 3.0, 1.1): "Site electricity demand",
        (5.8, 1.6, 3.0, 1.1): "Site steam demand",
        (0.2, 3.2, 2.2, 0.9): "Grid import\n/ export",
    }
    for (x, y, w, h), text in boxes.items():
        axis.add_patch(plt.Rectangle((x, y), w, h, fill=False, linewidth=1.2))
        axis.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=9)
    axis.annotate("", xy=(2.7, 5.1), xytext=(4.2, 6.2), arrowprops={"arrowstyle": "->"})
    axis.annotate("", xy=(7.3, 5.1), xytext=(5.8, 6.2), arrowprops={"arrowstyle": "->"})
    axis.annotate("", xy=(2.7, 2.7), xytext=(2.7, 4.0), arrowprops={"arrowstyle": "->"})
    axis.annotate("", xy=(7.3, 2.7), xytext=(7.3, 4.0), arrowprops={"arrowstyle": "->"})
    axis.annotate("", xy=(2.4, 3.6), xytext=(1.4, 3.6), arrowprops={"arrowstyle": "<->"})
    axis.plot([8.6, 8.6], [2.7, 4.6], linestyle="--", color="#9467bd")
    axis.annotate(
        "",
        xy=(7.3, 2.2),
        xytext=(8.6, 2.7),
        arrowprops={"arrowstyle": "->", "linestyle": "--", "color": "#9467bd"},
    )
    axis.text(8.7, 3.5, "E-boiler\n(investment\noption)", color="#9467bd", fontsize=8, va="center")
    axis.set_title("Screening energy system, not to scale")
    figure.tight_layout()
    figure.savefig(FIGURES / "final_energy_system_schematic.png", dpi=150, bbox_inches="tight")
    plt.close(figure)


def main() -> None:
    FIGURES.mkdir(parents=True, exist_ok=True)
    assumptions = load_assumptions()
    _bc1_week(assumptions)
    _bc1_export()
    _bc2_npv()
    _schematic()
    print("Final figures written")


if __name__ == "__main__":
    main()
