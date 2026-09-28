"""Tests for screening prices and baseline variable-energy cost."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.assumptions import load_assumptions
from src.baseline import dispatch_baseline
from src.costs import cost_baseline, fuel_price_breakdown, summarize_costed_baseline
from src.market_prices import (
    align_prices_to_model,
    parse_decimal,
    parse_smard_day_ahead,
    smard_csv_for_year,
)
from src.profiles import build_demand_profile, build_hourly_index

ASSUMPTIONS = load_assumptions()


def test_fuel_shares_sum_to_one():
    coal = ASSUMPTIONS.value("fuel_mix", "coal_share")
    fossil = ASSUMPTIONS.value("fuel_mix", "fossil_gas_share")
    biomethane = ASSUMPTIONS.value("fuel_mix", "biomethane_share")
    assert fossil == pytest.approx(0.68)
    assert biomethane == pytest.approx(0.32)
    assert coal + fossil + biomethane == pytest.approx(1.0)


def test_fuel_price_equations_and_single_eua_count():
    breakdown = fuel_price_breakdown(ASSUMPTIONS)
    commodity = ASSUMPTIONS.value("market", "natural_gas_commodity_eur_per_mwh")
    adder = ASSUMPTIONS.value("market", "gas_variable_adder_eur_per_mwh")
    premium = ASSUMPTIONS.value("market", "biomethane_premium_eur_per_mwh")
    factor = ASSUMPTIONS.value("emissions", "fossil_gas_emission_factor_tco2_per_mwh_fuel")
    carbon = ASSUMPTIONS.value("emissions", "carbon_price_eur_per_tco2")
    fossil_share = ASSUMPTIONS.value("fuel_mix", "fossil_gas_share")
    biomethane_share = ASSUMPTIONS.value("fuel_mix", "biomethane_share")

    fossil_cost = commodity + adder + factor * carbon
    biomethane_cost = commodity + adder + premium
    blended = fossil_share * fossil_cost + biomethane_share * biomethane_cost
    assert breakdown["fossil_gas_cost_eur_mwh"] == pytest.approx(fossil_cost)
    assert breakdown["biomethane_cost_eur_mwh"] == pytest.approx(biomethane_cost)
    assert breakdown["blended_fuel_cost_eur_mwh"] == pytest.approx(blended)
    assert breakdown["eua_cost_eur_per_mwh_fossil_fuel"] == pytest.approx(factor * carbon)
    assert biomethane_cost == pytest.approx(commodity + adder + premium)


def test_parse_decimal_accepts_german_commas_and_dots():
    assert parse_decimal("2.16") == pytest.approx(2.16)
    assert parse_decimal("2,16") == pytest.approx(2.16)
    assert parse_decimal("1.234,56") == pytest.approx(1234.56)
    assert parse_decimal("-0,01") == pytest.approx(-0.01)


def test_smard_file_maps_onto_8760_model_hours_and_keeps_negative_prices():
    source = smard_csv_for_year(ROOT / "data" / "raw", 2025)
    parsed = parse_smard_day_ahead(source)
    aligned = align_prices_to_model(parsed, ASSUMPTIONS)
    assert len(aligned) == 8760
    assert aligned.index.is_unique
    assert int(aligned["source_market_year"].iloc[0]) == 2025
    assert (aligned["day_ahead_price_eur_mwh"] < 0).any()
    assert not aligned["day_ahead_price_eur_mwh"].isna().any()
    model_index = build_hourly_index(
        ASSUMPTIONS.value("model", "model_year"),
        ASSUMPTIONS.value("model", "timezone"),
    )
    assert aligned.index.equals(model_index)


def test_cost_identity_for_all_baseline_scenarios():
    prices = align_prices_to_model(
        parse_smard_day_ahead(smard_csv_for_year(ROOT / "data" / "raw", 2025)),
        ASSUMPTIONS,
    )
    breakdown = fuel_price_breakdown(ASSUMPTIONS)
    for scenario in ("flat", "base", "variable"):
        dispatch = dispatch_baseline(build_demand_profile(ASSUMPTIONS, scenario), ASSUMPTIONS)
        costed = cost_baseline(dispatch, prices, ASSUMPTIONS)
        identity = (
            costed["chp_fuel_cost_eur"]
            + costed["boiler_fuel_cost_eur"]
            + costed["grid_import_cost_eur"]
            - costed["grid_export_revenue_eur"]
        )
        gap = (identity - costed["total_variable_energy_cost_eur"]).abs().max()
        assert gap == pytest.approx(0.0, abs=1e-6)
        fuel_cost = (
            costed["chp_fuel_mwh"] + costed["boiler_fuel_mwh"]
        ) * breakdown["blended_fuel_cost_eur_mwh"]
        fuel_gap = (
            fuel_cost - costed["chp_fuel_cost_eur"] - costed["boiler_fuel_cost_eur"]
        ).abs().max()
        assert fuel_gap == pytest.approx(0.0, abs=1e-4)
        # EUA memo is not added on top of the blended fuel cost.
        double_counted = (
            costed["total_variable_energy_cost_eur"]
            - identity
            - costed["embedded_fossil_co2_cost_eur"]
        )
        assert double_counted.abs().min() > 1.0


def test_zero_trade_has_zero_electricity_cashflow_and_export_reduces_cost():
    index = build_hourly_index(2026, "Europe/Berlin")[:1]
    dispatch = pd.DataFrame(
        {
            "steam_demand_mw": [0.0],
            "electricity_demand_mw": [0.0],
            "chp_steam_mw": [0.0],
            "boiler_steam_mw": [0.0],
            "chp_electricity_mw": [0.0],
            "chp_fuel_mwh": [0.0],
            "boiler_fuel_mwh": [0.0],
            "total_fuel_mwh": [0.0],
            "fossil_gas_fuel_mwh": [0.0],
            "biomethane_fuel_mwh": [0.0],
            "coal_fuel_mwh": [0.0],
            "grid_import_mw": [0.0],
            "grid_export_mw": [0.0],
            "net_grid_import_mw": [0.0],
        },
        index=index,
    )
    prices = pd.DataFrame(
        {
            "day_ahead_price_eur_mwh": [40.0],
            "electricity_import_price_eur_mwh": [40.0],
            "electricity_export_price_eur_mwh": [40.0],
        },
        index=index,
    )
    quiet = cost_baseline(dispatch, prices, ASSUMPTIONS)
    assert quiet["grid_import_cost_eur"].iloc[0] == pytest.approx(0.0)
    assert quiet["grid_export_revenue_eur"].iloc[0] == pytest.approx(0.0)

    exporting = dispatch.copy()
    exporting["grid_export_mw"] = 2.0
    exporting["chp_electricity_mw"] = 2.0
    with_export = cost_baseline(exporting, prices, ASSUMPTIONS)
    assert with_export["total_variable_energy_cost_eur"].iloc[0] < (
        quiet["total_variable_energy_cost_eur"].iloc[0]
    )


def test_production_denominator_does_not_change_hourly_cost():
    index = build_hourly_index(2026, "Europe/Berlin")[:1]
    dispatch = pd.DataFrame(
        {
            "steam_demand_mw": [10.0],
            "electricity_demand_mw": [5.0],
            "chp_steam_mw": [5.0],
            "boiler_steam_mw": [5.0],
            "chp_electricity_mw": [2.5],
            "chp_fuel_mwh": [8.0],
            "boiler_fuel_mwh": [6.0],
            "total_fuel_mwh": [14.0],
            "fossil_gas_fuel_mwh": [9.0],
            "biomethane_fuel_mwh": [5.0],
            "coal_fuel_mwh": [0.0],
            "grid_import_mw": [2.5],
            "grid_export_mw": [0.0],
            "net_grid_import_mw": [2.5],
        },
        index=index,
    )
    prices = pd.DataFrame(
        {
            "day_ahead_price_eur_mwh": [50.0],
            "electricity_import_price_eur_mwh": [50.0],
            "electricity_export_price_eur_mwh": [50.0],
        },
        index=index,
    )
    costed = cost_baseline(dispatch, prices, ASSUMPTIONS)
    summary = summarize_costed_baseline(costed, ASSUMPTIONS)
    hourly_total = float(costed["total_variable_energy_cost_eur"].sum())
    production = ASSUMPTIONS.value("site", "annual_henkel_production_tonnes")
    assert hourly_total == pytest.approx(
        summary["total_variable_energy_cost_eur_per_t_henkel_production"] * production
    )
    assert production not in costed.columns
    assert summary["eur_per_t_basis"].startswith("site-level screening cost")
