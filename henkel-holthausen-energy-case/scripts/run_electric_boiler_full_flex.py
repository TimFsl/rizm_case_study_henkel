#!/usr/bin/env python3
"""Size the electrode boiler under full flexibility.

The redispatch-only investment files stay in place. They are the
annual-CHP-utilization-constrained sensitivity. This script writes separate
full-flex tables and figures. Technology, prices, tariffs, and investment
parameters are unchanged.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.assumptions import load_assumptions
from src.electric_boiler import (
    BC1_OUTPUTS,
    FULL_FLEX,
    ScreeningLibrary,
    _capture_week,
    build_primary_tables,
    cashflow_frame,
    framework_comparison_frame,
    full_flex_sizing_frame,
    run_selected_sensitivities,
)
from src.investment import capital_recovery_factor, select_capacity_by_npv
from src.plotting import plot_electric_boiler_dispatch_week, plot_npv_redispatch_vs_full_flex

TABLE_DIR = ROOT / "outputs" / "tables"
FIGURE_DIR = ROOT / "outputs" / "figures"
PROCESSED = ROOT / "data" / "processed"
REDISPATCH_RESULTS = (
    "outputs/tables/electric_boiler_sizing.csv",
    "outputs/tables/electric_boiler_market_years.csv",
    "outputs/tables/electric_boiler_cashflows.csv",
    "outputs/tables/electric_boiler_sensitivity.csv",
)
FULL_FLEX_TABLES = (
    "electric_boiler_full_flex_sizing.csv",
    "electric_boiler_full_flex_market_years.csv",
    "electric_boiler_full_flex_cashflows.csv",
    "electric_boiler_operating_framework_comparison.csv",
)


def _hash(path: Path) -> str:
    if not path.is_file():
        return "missing"
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _price_qa(row: dict) -> str:
    electricity = float(row["annual_eboiler_electricity_mwh"])
    hours = float(row["operating_hours"])
    if electricity <= 1e-9 or hours <= 0.0:
        return "E-boiler operating hours: 0"
    average_price = float(row["elec_price_product"]) / electricity
    return (
        f"Operating hours {hours:.0f}; "
        f">25% load {row['hours_above_25']:.0f}; "
        f">50% {row['hours_above_50']:.0f}; "
        f">90% {row['hours_above_90']:.0f}; "
        f"electricity-weighted day-ahead price {average_price:.1f} EUR/MWh; "
        f"median operating price {row['operating_price_median']:.1f} EUR/MWh; "
        f"share of e-boiler electricity at negative prices "
        f"{row['elec_negative_mwh'] / electricity:.1%}; "
        f"below 25 EUR/MWh {row['elec_below_25_mwh'] / electricity:.1%}; "
        f"below 50 EUR/MWh {row['elec_below_50_mwh'] / electricity:.1%}"
    )


def _sources(row: dict) -> str:
    def gwh(key: str) -> float:
        return float(row[key]) / 1000.0

    network_delta = (
        row["network_energy_eur"]
        + row["network_demand_eur"]
        - row["counterfactual_network_energy_eur"]
        - row["counterfactual_network_demand_eur"]
    ) / 1e6
    return (
        f"Gas-boiler steam change {gwh('annual_boiler_steam_mwh') - gwh('counterfactual_boiler_steam_mwh'):+.2f} GWh; "
        f"CHP steam change {gwh('annual_chp_steam_mwh') - gwh('counterfactual_chp_steam_mwh'):+.2f} GWh; "
        f"CHP electricity change {gwh('annual_chp_electricity_mwh') - gwh('counterfactual_chp_electricity_mwh'):+.2f} GWh; "
        f"grid import change {gwh('annual_grid_import_mwh') - gwh('counterfactual_grid_import_mwh'):+.2f} GWh; "
        f"grid export change {gwh('annual_grid_export_mwh') - gwh('counterfactual_grid_export_mwh'):+.2f} GWh; "
        f"fossil fuel change {gwh('annual_fossil_fuel_mwh') - gwh('counterfactual_fossil_fuel_mwh'):+.2f} GWh; "
        f"network cost change {network_delta:+.3f} M EUR/a"
    )


def main() -> int:
    protected = list(BC1_OUTPUTS) + list(REDISPATCH_RESULTS)
    before = {name: _hash(ROOT / name) for name in protected}
    assumptions = load_assumptions()
    library = ScreeningLibrary(assumptions)
    primary = build_primary_tables(library, mode=FULL_FLEX)
    sizing = full_flex_sizing_frame(primary["economics"])
    market = pd.DataFrame(primary["market_rows"])
    cashflows = cashflow_frame(primary)
    redispatch = pd.read_csv(TABLE_DIR / "electric_boiler_sizing.csv")
    comparison = framework_comparison_frame(redispatch, sizing)

    TABLE_DIR.mkdir(parents=True, exist_ok=True)
    sizing.to_csv(TABLE_DIR / FULL_FLEX_TABLES[0], index=False)
    market.to_csv(TABLE_DIR / FULL_FLEX_TABLES[1], index=False)
    cashflows.to_csv(TABLE_DIR / FULL_FLEX_TABLES[2], index=False)
    comparison.to_csv(TABLE_DIR / FULL_FLEX_TABLES[3], index=False)

    k_star = float(primary["k_star"])
    positive = {
        capacity: row["npv_eur"]
        for capacity, row in primary["economics"].items()
        if capacity > 0.0
    }
    diagnostic_capacity = k_star if k_star > 0.0 else select_capacity_by_npv(positive)
    non_economic = k_star <= 0.0
    week = primary["week_frame"]
    if week is None or diagnostic_capacity != k_star:
        week = _capture_week(
            library,
            diagnostic_capacity,
            primary["first_year"],
            primary["carbon"][primary["first_year"]],
            [2023, 2024, 2025],
            mode=FULL_FLEX,
        )
    if week is not None:
        week.to_csv(
            PROCESSED / f"electric_boiler_full_flex_dispatch_{diagnostic_capacity:g}mw_price2025_year2026.csv"
        )
        title = (
            f"Full-flex e-boiler dispatch at {diagnostic_capacity:g} MW, 2025 price shape"
        )
        if non_economic:
            title += ". Non-economic: least-negative NPV, not K*"
        plot_electric_boiler_dispatch_week(
            week,
            FIGURE_DIR / "electric_boiler_full_flex_dispatch_week.png",
            title=title,
        )
    plot_npv_redispatch_vs_full_flex(
        comparison,
        FIGURE_DIR / "electric_boiler_npv_redispatch_vs_full_flex.png",
    )

    print(f"K*_full_flex = {k_star:g} MW", flush=True)
    print(sizing.to_string(index=False), flush=True)
    print(comparison.to_string(index=False), flush=True)
    chosen = primary["economics"][diagnostic_capacity]
    print(_sources(chosen), flush=True)
    print(_price_qa(chosen), flush=True)
    shape = (
        market[market["eboiler_capacity_mwth"] == diagnostic_capacity]
        .groupby("price_shape_year")["gross_operating_benefit_eur"]
        .mean()
    )
    print("Mean gross benefit by price shape, EUR/a", flush=True)
    print(shape.to_string(), flush=True)
    transitions = (
        market["tariff_regime"] != market["counterfactual_tariff_regime"]
    ).sum()
    print(f"Tariff-regime transitions across all cases: {int(transitions)}", flush=True)

    if k_star > 0.0:
        print("One-way sensitivities at K*_full_flex", flush=True)
        sensitivities = run_selected_sensitivities(library, primary)
        sensitivities.to_csv(TABLE_DIR / "electric_boiler_full_flex_sensitivity.csv", index=False)
        print(sensitivities.to_string(index=False), flush=True)
    else:
        gross = chosen["average_gross_eur"]
        fixed = chosen["fixed_opex_eur"]
        crf = capital_recovery_factor(primary["discount_rate"], primary["lifetime"])
        annualized_capex = chosen["capex_eur"] * crf
        gap = gross - fixed - annualized_capex
        print(
            f"Closest positive size {diagnostic_capacity:g} MW is non-economic. "
            f"Average gross benefit {gross / 1e6:.4f} M EUR/a, "
            f"fixed O&M {fixed / 1e6:.4f} M EUR/a, "
            f"annualized CAPEX {annualized_capex / 1e6:.4f} M EUR/a, "
            f"gap {gap / 1e6:.4f} M EUR/a.",
            flush=True,
        )

    after = {name: _hash(ROOT / name) for name in protected}
    changed = [name for name in protected if before[name] != after[name]]
    if changed:
        print("Protected files changed: " + ", ".join(changed), flush=True)
        return 1
    print("Business Case 1 and redispatch-only e-boiler results were not changed.", flush=True)
    print(f"Solves: {library.solves}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
