#!/usr/bin/env python3
"""Report-facing BC2 metrics and the 20 MW cash-flow figure.

Reads frozen tables only. Does not solve a dispatch model.
"""

from __future__ import annotations

import sys
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.investment import equivalent_annual_value, interpolate_anchor_series, net_present_value

TONNES = 455_000.0
RATE = 0.10
LIFE = 15
YEARS = list(range(2026, 2041))
TABLES = ROOT / "outputs" / "tables"
FIGURES = ROOT / "outputs" / "figures"


def _discounted_cumulative(cashflows: list[float]) -> list[float]:
    total = 0.0
    cumulative = []
    for year, cashflow in enumerate(cashflows):
        total += cashflow / (1.0 + RATE) ** year
        cumulative.append(total)
    return cumulative


def _path(anchor_2026: float, anchor_2030: float, anchor_2040: float, fixed_opex: float, capex: float) -> list[float]:
    annual = interpolate_anchor_series(
        {2026: anchor_2026, 2030: anchor_2030, 2040: anchor_2040},
        YEARS,
    )
    return [-capex, *[annual[year] - fixed_opex for year in YEARS]]


def main() -> None:
    forward = pd.read_csv(TABLES / "electric_boiler_forward_npv.csv")
    current_npv = pd.read_csv(TABLES / "electric_boiler_current_vs_forward.csv")
    mid = forward[forward["scenario"] == "mid"].set_index("capacity_mw")

    current_rows = []
    for capacity in (5.0, 10.0, 20.0):
        gross = float(mid.loc[capacity, "benefit_2026"])
        npv_m = float(current_npv.set_index("capacity_mw").loc[capacity, "current_cost_npv_m_eur"])
        eav = equivalent_annual_value(npv_m * 1e6, RATE, LIFE)
        current_rows.append(
            {
                "capacity_mw": capacity,
                "average_annual_gross_operating_savings_eur": gross,
                "gross_operating_savings_eur_per_t": gross / TONNES,
                "npv_m_eur": npv_m,
                "annualized_investment_value_eav_eur_per_t": eav / TONNES,
            }
        )
    current = pd.DataFrame(current_rows)

    forward_rows = []
    for capacity in (5.0, 10.0, 20.0, 30.0):
        row = mid.loc[capacity]
        annual = interpolate_anchor_series(
            {
                2026: float(row["benefit_2026"]),
                2030: float(row["benefit_2030"]),
                2040: float(row["benefit_2040"]),
            },
            YEARS,
        )
        gross = sum(annual.values()) / len(annual)
        forward_rows.append(
            {
                "capacity_mw": capacity,
                "average_annual_gross_operating_savings_eur": gross,
                "gross_operating_savings_eur_per_t": gross / TONNES,
                "npv_m_eur": float(row["npv_m_eur"]),
                "annualized_investment_value_eav_eur_per_t": float(row["eav_eur_per_t"]),
            }
        )
    forward_report = pd.DataFrame(forward_rows)
    current.to_csv(TABLES / "electric_boiler_report_current_cost.csv", index=False)
    forward_report.to_csv(TABLES / "electric_boiler_report_forward_mid.csv", index=False)

    stored = mid.loc[20.0]
    capex = float(stored["capex_m_eur"]) * 1e6
    fixed = float(stored["fixed_opex_m_eur_a"]) * 1e6
    current_flows = _path(float(stored["benefit_2026"]), float(stored["benefit_2026"]), float(stored["benefit_2026"]), fixed, capex)
    forward_flows = _path(float(stored["benefit_2026"]), float(stored["benefit_2030"]), float(stored["benefit_2040"]), fixed, capex)
    current_npv_check = net_present_value(RATE, current_flows) / 1e6
    forward_npv_check = net_present_value(RATE, forward_flows) / 1e6
    if abs(current_npv_check - float(current_npv.set_index("capacity_mw").loc[20.0, "current_cost_npv_m_eur"])) > 1e-6:
        raise SystemExit(f"Current-cost NPV mismatch: {current_npv_check}")
    if abs(forward_npv_check - float(stored["npv_m_eur"])) > 1e-6:
        raise SystemExit(f"Forward MID NPV mismatch: {forward_npv_check}")

    figure, axes = plt.subplots(1, 2, figsize=(10, 4.4), sharey=True)
    panels = (
        (axes[0], current_flows, "Current-cost case", "NPV −0.61 M EUR"),
        (axes[1], forward_flows, "Forward Mid case", "NPV +1.46 M EUR"),
    )
    years = list(range(16))
    for axis, flows, title, npv_label in panels:
        cumulative = [value / 1e6 for value in _discounted_cumulative(flows)]
        axis.bar(years, [value / 1e6 for value in flows], color="#9ecae1", label="Annual net cash flow")
        axis.plot(years, cumulative, color="#08519c", marker="o", markersize=3.5, label="Cumulative discounted cash flow")
        axis.axhline(0.0, color="#666666", linewidth=0.8)
        axis.set_title(title)
        axis.set_xlabel("Year")
        axis.set_xticks([0, 5, 10, 15])
        axis.text(0.98, 0.95, npv_label, transform=axis.transAxes, ha="right", va="top", fontsize=9)
    axes[0].set_ylabel("M EUR")
    axes[1].legend(frameon=False, fontsize=8, loc="lower right")
    figure.suptitle("20 MW electrode boiler, annual cash flow")
    figure.tight_layout()
    FIGURES.mkdir(parents=True, exist_ok=True)
    figure.savefig(FIGURES / "final_bc2_dcf_comparison.png", dpi=150, bbox_inches="tight")
    plt.close(figure)

    pd.set_option("display.float_format", lambda value: f"{value:.10g}")
    print("CURRENT")
    print(current.to_string(index=False))
    print("FORWARD MID")
    print(forward_report.to_string(index=False))
    print(f"20 MW current NPV check {current_npv_check:.10f}")
    print(f"20 MW forward NPV check {forward_npv_check:.10f}")


if __name__ == "__main__":
    main()
