"""Physics, data, and incremental-value tests for the electrode boiler."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.assumptions import load_assumptions
from src.costs import read_network_tariff, scenario_blended_fuel_price
from src.electric_boiler import BC1_OUTPUTS, eboiler_electricity_break_even_price
from src.market_prices import parse_smard_day_ahead, smard_csv_for_year
from src.optimization import (
    REDISPATCH_ONLY,
    build_dispatch_model,
    finalize_dispatch,
    solve_dispatch,
)
from src.profiles import build_calendar_hourly_index, build_demand_profile, build_demand_profile_for_year

ASSUMPTIONS = load_assumptions()
SCRIPT = (ROOT / "scripts" / "run_electric_boiler.py").read_text(encoding="utf-8")


def test_smard_years_have_the_expected_hourly_length_and_no_missing_prices():
    expected = {2023: 8760, 2024: 8784, 2025: 8760}
    for year, hours in expected.items():
        parsed = parse_smard_day_ahead(smard_csv_for_year(ROOT / "data" / "raw", year))
        assert len(parsed) == hours
        assert parsed.index.is_unique
        assert not parsed["day_ahead_price_eur_mwh"].isna().any()
        assert (parsed["day_ahead_price_eur_mwh"] < 0).any()
        steps = parsed.index.to_series().diff().dropna()
        assert (steps == pd.Timedelta(hours=1)).all()
        calendar = build_calendar_hourly_index(year, "Europe/Berlin")
        assert parsed.index.difference(calendar).empty
        assert len(calendar) == hours


def test_leap_year_base_profile_keeps_the_configured_annual_totals():
    profile = build_demand_profile_for_year(ASSUMPTIONS, "base", 2024)
    assert len(profile) == 8784
    assert profile["steam_demand_mw"].sum() / 1000.0 == pytest.approx(1040.0)
    assert profile["electricity_demand_mw"].sum() / 1000.0 == pytest.approx(290.0)
    unchanged = build_demand_profile(ASSUMPTIONS, "base")
    assert len(unchanged) == 8760
    assert unchanged["steam_demand_mw"].sum() / 1000.0 == pytest.approx(1040.0)


def _index(hours: int = 8) -> pd.DatetimeIndex:
    return pd.date_range("2026-01-06", periods=hours, freq="h", tz="Europe/Berlin")


def _case(hours: int, steam: float, electricity: float, prices: list[float]):
    index = _index(hours)
    demand = pd.DataFrame(
        {
            "steam_demand_mw": [steam] * hours,
            "electricity_demand_mw": [electricity] * hours,
        },
        index=index,
    )
    series = pd.Series(prices, index=index, dtype=float)
    price_frame = pd.DataFrame(
        {
            "day_ahead_price_eur_mwh": series,
            "electricity_import_price_eur_mwh": series,
            "electricity_export_price_eur_mwh": series - 5.0,
        }
    )
    return demand, price_frame


def test_zero_capacity_does_not_add_eboiler_columns_or_value():
    demand, prices = _case(4, 80.0, 30.0, [20.0, 40.0, 80.0, 120.0])
    without = solve_dispatch(
        demand,
        prices,
        ASSUMPTIONS,
        "full_flex",
        export_capacity_mw=10.0,
        chp_ramp_fraction_per_hour=0.50,
    )
    zero = solve_dispatch(
        demand,
        prices,
        ASSUMPTIONS,
        "full_flex",
        export_capacity_mw=10.0,
        chp_ramp_fraction_per_hour=0.50,
        eboiler_capacity_mw=0.0,
    )
    assert "eboiler_steam_mw" not in without.frame.columns
    assert "eboiler_steam_mw" not in zero.frame.columns
    assert without.solver_objective_eur == pytest.approx(zero.solver_objective_eur)


def test_eboiler_physics_caps_and_redispatch_identity():
    # No annual demand charge on this short horizon: that charge is a yearly
    # EUR/kW term and would dominate a six-hour example.
    demand, prices = _case(6, 90.0, 40.0, [0.0, 0.0, 0.0, 250.0, 250.0, 250.0])
    target = 30.0
    result = solve_dispatch(
        demand,
        prices,
        ASSUMPTIONS,
        REDISPATCH_ONLY,
        baseline_chp_electricity_mwh=target,
        chp_ramp_fraction_per_hour=0.50,
        export_capacity_mw=10.0,
        eboiler_capacity_mw=15.0,
        eboiler_efficiency=0.99,
    )
    frame = result.frame
    assert frame["eboiler_steam_mw"].max() <= 15.0 + 1e-6
    assert (frame["eboiler_electricity_mw"] * 0.99).sum() == pytest.approx(
        frame["eboiler_steam_mw"].sum()
    )
    assert (
        frame["chp_steam_mw"] + frame["boiler_steam_mw"] + frame["eboiler_steam_mw"]
    ).sum() == pytest.approx(frame["steam_demand_mw"].sum())
    balance = (
        frame["chp_electricity_mw"]
        + frame["grid_import_mw"]
        - frame["eboiler_electricity_mw"]
        - frame["grid_export_mw"]
    )
    assert balance.sum() == pytest.approx(frame["electricity_demand_mw"].sum())
    assert frame["grid_import_mw"].max() <= 64.0 + 1e-6
    assert frame["grid_export_mw"].max() <= 10.0 + 1e-6
    assert frame["chp_electricity_mw"].sum() == pytest.approx(target)
    assert frame["chp_steam_mw"].diff().iloc[1:].abs().max() <= 55.0 + 1e-6
    assert frame["eboiler_steam_mw"].iloc[:3].sum() > frame["eboiler_steam_mw"].iloc[3:].sum()


def test_import_cap_limits_the_eboiler_and_export_cap_binds_at_10_mw():
    demand, prices = _case(4, 120.0, 20.0, [0.0, 0.0, 250.0, 250.0])
    result = solve_dispatch(
        demand,
        prices,
        ASSUMPTIONS,
        "full_flex",
        export_capacity_mw=10.0,
        eboiler_capacity_mw=40.0,
        eboiler_efficiency=0.99,
    )
    assert result.frame["grid_import_mw"].max() <= 64.0 + 1e-6
    assert result.frame["grid_export_mw"].max() == pytest.approx(10.0, abs=1e-4)
    assert result.frame["eboiler_steam_mw"].max() <= 40.0 + 1e-6


def test_larger_capacity_cannot_increase_screening_cost():
    demand, prices = _case(8, 100.0, 35.0, [0.0, 5.0, 15.0, 40.0, 80.0, 120.0, 180.0, 20.0])
    tariff = read_network_tariff(ASSUMPTIONS, "hv_high_utilization")
    small = solve_dispatch(
        demand,
        prices,
        ASSUMPTIONS,
        "full_flex",
        network_tariff=tariff,
        export_capacity_mw=10.0,
        chp_ramp_fraction_per_hour=0.50,
        eboiler_capacity_mw=5.0,
    )
    large = solve_dispatch(
        demand,
        prices,
        ASSUMPTIONS,
        "full_flex",
        network_tariff=tariff,
        export_capacity_mw=10.0,
        chp_ramp_fraction_per_hour=0.50,
        eboiler_capacity_mw=20.0,
    )

    def screening(result):
        return (
            float(result.frame["total_variable_energy_cost_eur"].sum())
            + result.network_energy_cost_eur
            + result.network_demand_charge_eur
        )

    assert screening(large) <= screening(small) + 1.0


def test_mutable_fuel_price_changes_the_objective_of_a_built_model():
    demand, prices = _case(4, 80.0, 30.0, [40.0, 40.0, 40.0, 40.0])
    model = build_dispatch_model(
        steam=demand["steam_demand_mw"],
        electricity=demand["electricity_demand_mw"],
        import_price=prices["electricity_import_price_eur_mwh"],
        export_price=prices["electricity_export_price_eur_mwh"],
        fuel_price=40.0,
        assumptions=ASSUMPTIONS,
        mode="full_flex",
        export_capacity_mw=10.0,
        eboiler_capacity_mw=10.0,
        eboiler_efficiency=0.99,
    )
    cheap = finalize_dispatch(
        model,
        demand=demand,
        prices=prices,
        assumptions=ASSUMPTIONS,
        mode="full_flex",
        baseline_chp_electricity_mwh=None,
        network_tariff=None,
        chp_ramp_fraction_per_hour=None,
        ramp_mw=None,
        export_capacity_mw=10.0,
        eboiler_capacity_mw=10.0,
        eboiler_efficiency=0.99,
        fuel_price=40.0,
    )
    expensive = finalize_dispatch(
        model,
        demand=demand,
        prices=prices,
        assumptions=ASSUMPTIONS,
        mode="full_flex",
        baseline_chp_electricity_mwh=None,
        network_tariff=None,
        chp_ramp_fraction_per_hour=None,
        ramp_mw=None,
        export_capacity_mw=10.0,
        eboiler_capacity_mw=10.0,
        eboiler_efficiency=0.99,
        fuel_price=80.0,
    )
    assert expensive.solver_objective_eur > cheap.solver_objective_eur + 1.0


def test_capex_and_fixed_om_follow_the_screening_rates():
    capacity = 20.0
    capex_rate = ASSUMPTIONS.value("electric_boiler", "capex_eur_per_mwth")
    om = ASSUMPTIONS.value("electric_boiler", "fixed_om_fraction_of_capex")
    assert capex_rate == 200_000
    assert om == pytest.approx(0.017)
    assert capacity * capex_rate == pytest.approx(4_000_000)
    assert 0.017 * capacity * capex_rate == pytest.approx(68_000)


def test_analytical_break_even_uses_boiler_efficiency_and_fuel_price():
    fuel = scenario_blended_fuel_price(ASSUMPTIONS, 80.0)
    price = eboiler_electricity_break_even_price(ASSUMPTIONS, fuel, 0.99)
    boiler = ASSUMPTIONS.value("boiler", "thermal_efficiency")
    assert price == pytest.approx(fuel / boiler * 0.99)


def test_full_flex_has_no_annual_chp_constraint_and_the_boiler_can_change_chp():
    from src.optimization import FULL_FLEX, build_dispatch_model

    demand, prices = _case(4, 140.0, 30.0, [0.0, 0.0, 0.0, 0.0])
    model = build_dispatch_model(
        steam=demand["steam_demand_mw"],
        electricity=demand["electricity_demand_mw"],
        import_price=prices["electricity_import_price_eur_mwh"],
        export_price=prices["electricity_export_price_eur_mwh"],
        fuel_price=50.0,
        assumptions=ASSUMPTIONS,
        mode=FULL_FLEX,
        export_capacity_mw=10.0,
        chp_ramp_mw_per_hour=55.0,
        eboiler_capacity_mw=20.0,
        eboiler_efficiency=0.99,
    )
    assert not hasattr(model, "annual_chp_electricity")
    without = solve_dispatch(
        demand,
        prices,
        ASSUMPTIONS,
        FULL_FLEX,
        export_capacity_mw=10.0,
        chp_ramp_fraction_per_hour=0.50,
        eboiler_capacity_mw=0.0,
    )
    with_boiler = solve_dispatch(
        demand,
        prices,
        ASSUMPTIONS,
        FULL_FLEX,
        export_capacity_mw=10.0,
        chp_ramp_fraction_per_hour=0.50,
        eboiler_capacity_mw=20.0,
        eboiler_efficiency=0.99,
    )
    assert with_boiler.frame["chp_electricity_mw"].sum() < without.frame["chp_electricity_mw"].sum() - 1.0
    steam = (
        with_boiler.frame["chp_steam_mw"]
        + with_boiler.frame["boiler_steam_mw"]
        + with_boiler.frame["eboiler_steam_mw"]
    )
    assert steam.sum() == pytest.approx(with_boiler.frame["steam_demand_mw"].sum())
    benefit = (
        float(without.frame["total_variable_energy_cost_eur"].sum())
        - float(with_boiler.frame["total_variable_energy_cost_eur"].sum())
    )
    assert benefit > 0.0


def test_full_flex_runner_does_not_overwrite_redispatch_results():
    script = (ROOT / "scripts" / "run_electric_boiler_full_flex.py").read_text(encoding="utf-8")
    assert "electric_boiler_full_flex_sizing.csv" in script
    assert "to_csv(TABLE_DIR / \"electric_boiler_sizing.csv\"" not in script
    assert "electric_boiler_operating_framework_comparison.csv" in script


def test_full_flex_npv_selection_uses_the_same_maximum_npv_rule():
    from src.investment import select_capacity_by_npv

    assert select_capacity_by_npv({0.0: 0.0, 10.0: 2.0, 20.0: 1.5}) == pytest.approx(10.0)
    assert select_capacity_by_npv({0.0: 0.0, 10.0: -1.0, 20.0: -0.4}) == pytest.approx(0.0)


def test_runner_does_not_name_business_case_1_outputs_as_write_targets():
    for relative in BC1_OUTPUTS:
        assert relative not in SCRIPT
