"""CHP ramp limits and regime-consistent tariff transition value."""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pandas as pd
import pyomo.environ as pyo
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.assumptions import load_assumptions
from src.baseline import dispatch_baseline
from src.costs import consistent_tariff_name, regime_consistent_value_eur
from src.optimization import (
    FULL_FLEX,
    REDISPATCH_ONLY,
    assign_screening_roles,
    build_dispatch_model,
    solve_dispatch,
)
import src.optimization as optimization

ASSUMPTIONS = load_assumptions()
CHP_MAX = float(ASSUMPTIONS.value("chp", "max_heat_output_mw"))


def _hours(n: int) -> pd.DatetimeIndex:
    return pd.date_range("2026-01-06", periods=n, freq="h", tz="Europe/Berlin")


def _demand(steam: list[float], electricity: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {"steam_demand_mw": steam, "electricity_demand_mw": electricity},
        index=_hours(len(steam)),
    )


def _prices(day_ahead: list[float]) -> pd.DataFrame:
    index = _hours(len(day_ahead))
    series = pd.Series(day_ahead, index=index, dtype=float)
    return pd.DataFrame(
        {
            "day_ahead_price_eur_mwh": series,
            "electricity_import_price_eur_mwh": series,
            "electricity_export_price_eur_mwh": series,
        },
        index=index,
    )


def _jump_case(prices: list[float], ramp: float):
    demand = _demand([80.0, 80.0, 80.0, 80.0], [30.0, 30.0, 30.0, 30.0])
    return solve_dispatch(
        demand,
        _prices(prices),
        ASSUMPTIONS,
        FULL_FLEX,
        chp_ramp_fraction_per_hour=ramp,
    )


def test_chp_up_ramp_limits_the_hourly_increase():
    result = _jump_case([20.0, 200.0, 200.0, 200.0], 0.25)
    chp = result.frame["chp_steam_mw"]
    limit = 0.25 * CHP_MAX
    delta = chp.diff().iloc[1:]
    assert float(delta.max()) == pytest.approx(limit, abs=1e-3)
    assert float(delta.abs().max()) <= limit + 1e-3
    assert float(chp.iloc[1] - chp.iloc[0]) == pytest.approx(limit, abs=1e-3)


def test_chp_down_ramp_limits_the_hourly_decrease():
    result = _jump_case([200.0, 20.0, 20.0, 20.0], 0.25)
    chp = result.frame["chp_steam_mw"]
    limit = 0.25 * CHP_MAX
    delta = chp.diff().iloc[1:]
    assert float((-delta).max()) == pytest.approx(limit, abs=1e-3)
    assert float(chp.iloc[0] - chp.iloc[1]) == pytest.approx(limit, abs=1e-3)


def test_ramp_fractions_25_50_and_100_percent():
    prices = [20.0, 200.0, 200.0, 200.0]
    unrestricted_step = _jump_case(prices, 1.00).frame["chp_steam_mw"]
    assert float(unrestricted_step.iloc[1] - unrestricted_step.iloc[0]) == pytest.approx(80.0, abs=1e-2)
    for fraction in (0.25, 0.50, 1.00):
        chp = _jump_case(prices, fraction).frame["chp_steam_mw"]
        limit = fraction * CHP_MAX
        assert float(chp.diff().iloc[1:].abs().max()) <= limit + 1e-3
    mid = _jump_case(prices, 0.50).frame["chp_steam_mw"]
    assert float(mid.iloc[1] - mid.iloc[0]) == pytest.approx(0.50 * CHP_MAX, abs=1e-3)


def test_ramp_constraints_are_continuous_and_skip_the_first_hour():
    index = _hours(3)
    demand = _demand([80.0, 80.0, 80.0], [30.0, 30.0, 30.0])
    prices = _prices([50.0, 80.0, 40.0])
    model = build_dispatch_model(
        steam=demand["steam_demand_mw"],
        electricity=demand["electricity_demand_mw"],
        import_price=prices["electricity_import_price_eur_mwh"],
        export_price=prices["electricity_export_price_eur_mwh"],
        fuel_price=50.0,
        assumptions=ASSUMPTIONS,
        mode=FULL_FLEX,
        chp_ramp_mw_per_hour=0.25 * CHP_MAX,
    )
    assert pyo.value(model.chp_ramp_up[1].upper) == pytest.approx(0.25 * CHP_MAX)
    assert pyo.value(model.chp_ramp_down[1].upper) == pytest.approx(0.25 * CHP_MAX)
    assert 0 not in list(model.chp_ramp_up)
    assert not hasattr(model, "boiler_ramp_up")
    for variable in model.component_data_objects(pyo.Var):
        assert not variable.is_binary()


def test_redispatch_annual_chp_equality_holds_with_a_ramp():
    demand = _demand([100.0] * 6, [35.0] * 6)
    prices = _prices([20.0, 200.0, 20.0, 200.0, 20.0, 200.0])
    reference = dispatch_baseline(demand, ASSUMPTIONS)
    target = float(reference["chp_electricity_mw"].sum())
    optimized = solve_dispatch(
        demand,
        prices,
        ASSUMPTIONS,
        REDISPATCH_ONLY,
        baseline_chp_electricity_mwh=target,
        chp_ramp_fraction_per_hour=0.25,
    )
    assert float(optimized.frame["chp_electricity_mw"].sum()) == pytest.approx(target, abs=1e-2)


def test_tariff_regime_classification_uses_the_2500_hour_threshold():
    assert consistent_tariff_name(5805.0, 2500.0) == "hv_high_utilization"
    assert consistent_tariff_name(2500.0, 2500.0) == "hv_high_utilization"
    assert consistent_tariff_name(2499.0, 2500.0) == "hv_low_utilization"
    assert consistent_tariff_name(1145.0, 2500.0) == "hv_low_utilization"
    assert consistent_tariff_name(None, 2500.0) is None


def test_cross_regime_transition_uses_the_baseline_regime_cost():
    baseline_high = 84_500_000.0
    baseline_low = 85_000_000.0
    optimized_low = 78_400_000.0
    value = regime_consistent_value_eur(baseline_high, optimized_low)
    same_tariff_low = regime_consistent_value_eur(baseline_low, optimized_low)
    assert consistent_tariff_name(5805.0, 2500.0) == "hv_high_utilization"
    assert consistent_tariff_name(1145.0, 2500.0) == "hv_low_utilization"
    assert value == pytest.approx(baseline_high - optimized_low)
    assert value != pytest.approx(same_tariff_low)


def test_primary_case_follows_solved_consistency():
    high = "hv_high_utilization"
    low = "hv_low_utilization"
    rows = [
        _row(REDISPATCH_ONLY, 0.50, high, True),
        _row(REDISPATCH_ONLY, 0.50, low, True),
        _row(FULL_FLEX, 0.50, high, False),
        _row(FULL_FLEX, 0.50, low, True),
    ]
    assign_screening_roles(rows)
    roles = {(row["optimization_mode"], row["optimized_tariff_scenario"]): row["screening_role"] for row in rows}
    assert roles[(REDISPATCH_ONLY, high)] == "primary_screening"
    assert roles[(FULL_FLEX, low)] == "upper_bound"
    assert roles[(REDISPATCH_ONLY, low)] == ""

    forced = [
        _row(REDISPATCH_ONLY, 0.50, high, False),
        _row(REDISPATCH_ONLY, 0.50, low, True),
    ]
    assign_screening_roles(forced)
    assert forced[0]["screening_role"] == ""
    assert forced[1]["screening_role"] == "primary_screening"


def test_export_limit_caps_hourly_export_and_reads_the_passed_limit():
    demand = _demand([120.0, 120.0], [20.0, 20.0])
    prices = _prices([200.0, 200.0])
    capped = solve_dispatch(
        demand,
        prices,
        ASSUMPTIONS,
        FULL_FLEX,
        export_capacity_mw=5.0,
    )
    assert float(capped.frame["grid_export_mw"].max()) == pytest.approx(5.0, abs=1e-3)
    open_export = solve_dispatch(demand, prices, ASSUMPTIONS, FULL_FLEX)
    assert float(open_export.frame["grid_export_mw"].max()) > 10.0
    model = build_dispatch_model(
        steam=demand["steam_demand_mw"],
        electricity=demand["electricity_demand_mw"],
        import_price=prices["electricity_import_price_eur_mwh"],
        export_price=prices["electricity_export_price_eur_mwh"],
        fuel_price=50.0,
        assumptions=ASSUMPTIONS,
        mode=FULL_FLEX,
        export_capacity_mw=5.0,
    )
    assert pyo.value(model.export_capacity[0].upper) == pytest.approx(5.0)
    assert not hasattr(
        build_dispatch_model(
            steam=demand["steam_demand_mw"],
            electricity=demand["electricity_demand_mw"],
            import_price=prices["electricity_import_price_eur_mwh"],
            export_price=prices["electricity_export_price_eur_mwh"],
            fuel_price=50.0,
            assumptions=ASSUMPTIONS,
            mode=FULL_FLEX,
        ),
        "export_capacity",
    )


def test_physical_annual_demand_is_unchanged_and_part_load_is_absent():
    assert ASSUMPTIONS.value("demand", "annual_steam_heat_gwh") == 1040
    assert ASSUMPTIONS.value("demand", "annual_electricity_gwh") == 290
    source = inspect.getsource(optimization)
    assert "part_load" not in source
    assert "Binary" not in source
    assert ASSUMPTIONS.value("chp", "chp_ramp_fraction_of_heat_capacity_per_hour") == pytest.approx(0.50)


def _row(mode: str, ramp: float, tariff: str, consistent: bool) -> dict:
    return {
        "optimization_mode": mode,
        "chp_ramp_fraction_per_hour": ramp,
        "baseline_tariff_regime": "hv_high_utilization",
        "optimized_tariff_scenario": tariff,
        "optimized_tariff_consistent": consistent,
    }
