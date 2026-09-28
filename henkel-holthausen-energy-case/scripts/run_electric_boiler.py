#!/usr/bin/env python3
"""Screen an electrode boiler against the optimized Holthausen system.

Business Case 1 files are not written. CAPEX is applied after the hourly
dispatch. The primary size is the capacity with the highest NPV.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.assumptions import load_assumptions
from src.costs import scenario_blended_fuel_price
from src.electric_boiler import (
    BC1_OUTPUTS,
    ScreeningLibrary,
    build_primary_tables,
    cashflow_frame,
    configured_market_years,
    eboiler_electricity_break_even_price,
    extend_carbon_years_note,
    primary_fossil_pre_carbon,
    run_selected_sensitivities,
    sizing_frame,
    structural_flexibility_snapshot,
)
from src.market_prices import parse_smard_day_ahead, price_qa_statistics, smard_csv_for_year
from src.plotting import (
    plot_electric_boiler_cashflow,
    plot_electric_boiler_dispatch_week,
    plot_electric_boiler_full_load_hours,
    plot_electric_boiler_market_years,
    plot_electric_boiler_npv,
    plot_electric_boiler_operating_value,
)
from src.profiles import build_demand_profile_for_year

TABLE_DIR = ROOT / "outputs" / "tables"
FIGURE_DIR = ROOT / "outputs" / "figures"
PROCESSED = ROOT / "data" / "processed"
NEW_TABLES = (
    "electric_boiler_sizing.csv",
    "electric_boiler_market_years.csv",
    "electric_boiler_cashflows.csv",
    "electric_boiler_sensitivity.csv",
)


def _hash(path: Path) -> str:
    if not path.is_file():
        return "missing"
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _print_price_qa(assumptions) -> None:
    print("SMARD DE/LU day-ahead QA", flush=True)
    for year in (2023, 2024, 2025):
        parsed = parse_smard_day_ahead(smard_csv_for_year(ROOT / "data" / "raw", year))
        stats = price_qa_statistics(parsed)
        negative = int((parsed["day_ahead_price_eur_mwh"] < 0).sum())
        print(
            f"  {year}: n={stats['hours']} min={stats['minimum']:.2f} "
            f"mean={stats['mean']:.2f} median={stats['median']:.2f} "
            f"max={stats['maximum']:.2f} negative_hours={negative}",
            flush=True,
        )
        profile = build_demand_profile_for_year(assumptions, "base", year)
        profile.to_csv(PROCESSED / f"demand_profile_base_{year}.csv")


def main() -> int:
    assumptions = load_assumptions()
    before = {name: _hash(ROOT / name) for name in BC1_OUTPUTS}
    _print_price_qa(assumptions)
    print(extend_carbon_years_note(), flush=True)
    library = ScreeningLibrary(assumptions)
    primary = build_primary_tables(library)
    sizing = sizing_frame(primary["economics"])
    market = pd.DataFrame(primary["market_rows"])
    cashflows = cashflow_frame(primary)
    sensitivities = run_selected_sensitivities(library, primary)
    snapshot = structural_flexibility_snapshot(library, primary)

    TABLE_DIR.mkdir(parents=True, exist_ok=True)
    sizing.to_csv(TABLE_DIR / NEW_TABLES[0], index=False)
    market.to_csv(TABLE_DIR / NEW_TABLES[1], index=False)
    cashflows.to_csv(TABLE_DIR / NEW_TABLES[2], index=False)
    sensitivities.to_csv(TABLE_DIR / NEW_TABLES[3], index=False)
    k_star = float(primary["k_star"])
    week_capacity = k_star if k_star > 0.0 else 5.0
    if primary["week_frame"] is None and week_capacity > 0.0:
        from src.electric_boiler import _capture_week

        primary["week_frame"] = _capture_week(
            library,
            week_capacity,
            primary["first_year"],
            primary["carbon"][primary["first_year"]],
            configured_market_years(assumptions),
        )
    if primary["week_frame"] is not None:
        week_path = PROCESSED / (
            f"electric_boiler_dispatch_{week_capacity:g}mw_price2025_year2026.csv"
        )
        primary["week_frame"].to_csv(week_path)
        week_title = (
            f"E-boiler dispatch at {week_capacity:g} MW, 2025 price shape, early January"
        )
        if k_star <= 0.0:
            week_title += ". K* is 0 MW; this is the smallest positive size"
        plot_electric_boiler_dispatch_week(
            primary["week_frame"],
            FIGURE_DIR / "electric_boiler_primary_dispatch_week.png",
            title=week_title,
        )
    plot_electric_boiler_npv(sizing, k_star, FIGURE_DIR / "electric_boiler_npv_vs_size.png")
    plot_electric_boiler_operating_value(
        sizing, FIGURE_DIR / "electric_boiler_operating_value_vs_size.png"
    )
    plot_electric_boiler_full_load_hours(
        sizing, FIGURE_DIR / "electric_boiler_full_load_hours_vs_size.png"
    )
    plot_electric_boiler_market_years(
        market, k_star, FIGURE_DIR / "electric_boiler_market_year_comparison.png"
    )
    plot_electric_boiler_cashflow(
        cashflows, k_star, FIGURE_DIR / "electric_boiler_cumulative_cashflow.png"
    )

    chosen = primary["economics"][k_star]
    fuel = primary_fossil_pre_carbon(assumptions)
    carbon_2026 = primary["carbon"][primary["first_year"]]
    blended = scenario_blended_fuel_price(assumptions, carbon_2026)
    break_even = eboiler_electricity_break_even_price(assumptions, blended, library.eta)
    print(f"K* = {k_star:g} MW_th", flush=True)
    print(f"2026 blended fuel {blended:.2f} EUR/MWh; analytical electricity break-even {break_even:.2f} EUR/MWh")
    print(f"pre-carbon fossil reference {fuel:.2f} EUR/MWh")
    print(sizing.to_string(index=False), flush=True)
    print(sensitivities.to_string(index=False), flush=True)
    print(snapshot, flush=True)
    if primary["week_frame"] is not None:
        frame = primary["week_frame"]
        steam = frame["eboiler_steam_mw"]
        total = float(steam.sum())
        if total > 0:
            cheap = frame["electricity_import_price_eur_mwh"] <= break_even
            share = float(steam[cheap].sum()) / total
            print(f"Share of 2025-shape e-boiler steam at or below the analytical break-even: {share:.3f}")
    after = {name: _hash(ROOT / name) for name in BC1_OUTPUTS}
    changed = [name for name in BC1_OUTPUTS if before[name] != after[name]]
    if changed:
        print("Business Case 1 files changed: " + ", ".join(changed), flush=True)
        return 1
    print("Business Case 1 files were not changed.", flush=True)
    print(f"Solves: {library.solves}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
