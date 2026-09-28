"""Tests for the linear price-responsive dispatch."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.assumptions import load_assumptions
from src.baseline import dispatch_baseline
from src.costs import cost_baseline, fuel_price_breakdown
from src.optimization import (
    FULL_FLEX,
    REDISPATCH_ONLY,
    OptimizationError,
    OptimizationWarning,
    _warn_if_arbitrage_prices,
    calculate_chp_break_even_price,
    solve_dispatch,
)

ASSUMPTIONS = load_assumptions()


class _Override:
    def __init__(self, base, overrides):
        self.base = base
        self.overrides = overrides

    def value(self, section, key):
        if (section, key) in self.overrides:
            return self.overrides[(section, key)]
        return self.base.value(section, key)


def _hours(n: int = 4) -> pd.DatetimeIndex:
    return pd.date_range("2026-01-06", periods=n, freq="h", tz="Europe/Berlin")


def _prices(index: pd.DatetimeIndex, day_ahead: list[float]) -> pd.DataFrame:
    series = pd.Series(day_ahead, index=index, dtype=float)
    return pd.DataFrame(
        {
            "day_ahead_price_eur_mwh": series,
            "electricity_import_price_eur_mwh": series,
            "electricity_export_price_eur_mwh": series,
        }
    )


def _demand(index: pd.DatetimeIndex, steam: list[float], electricity: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {"steam_demand_mw": steam, "electricity_demand_mw": electricity},
        index=index,
    )


def _screen_case():
    break_even = calculate_chp_break_even_price(ASSUMPTIONS)
    index = _hours()
    demand = _demand(index, [80.0, 150.0, 120.0, 90.0], [30.0, 30.0, 30.0, 30.0])
    prices = _prices(
        index,
        [
            break_even - 40.0,
            break_even + 40.0,
            break_even - 40.0,
            break_even + 40.0,
        ],
    )
    return demand, prices, break_even


def test_solver_returns_optimal_for_a_small_model():
    demand, prices, _ = _screen_case()
    result = solve_dispatch(demand, prices, ASSUMPTIONS, FULL_FLEX)
    assert result.termination_condition == "optimal"
    assert len(result.frame) == 4


def test_physical_balances_capacities_and_fuel_identity():
    demand, prices, _ = _screen_case()
    frame = solve_dispatch(demand, prices, ASSUMPTIONS, FULL_FLEX).frame
    chp_max = ASSUMPTIONS.value("chp", "max_heat_output_mw")
    boiler_max = ASSUMPTIONS.value("boiler", "max_heat_output_mw")
    import_capacity = ASSUMPTIONS.value("grid", "import_capacity_mw")
    power_to_heat = ASSUMPTIONS.value("chp", "power_to_heat_ratio")
    chp_efficiency = ASSUMPTIONS.value("chp", "total_utilization_efficiency")
    boiler_efficiency = ASSUMPTIONS.value("boiler", "thermal_efficiency")

    assert frame["chp_steam_mw"].add(frame["boiler_steam_mw"]).tolist() == pytest.approx(
        demand["steam_demand_mw"].tolist()
    )
    assert (
        frame["chp_electricity_mw"] + frame["grid_import_mw"] - frame["grid_export_mw"]
    ).tolist() == pytest.approx(demand["electricity_demand_mw"].tolist())
    assert frame["chp_steam_mw"].max() <= chp_max + 1e-4
    assert frame["boiler_steam_mw"].max() <= boiler_max + 1e-4
    assert frame["grid_import_mw"].max() <= import_capacity + 1e-4
    assert (frame["grid_import_mw"].clip(lower=0) * frame["grid_export_mw"].clip(lower=0)).max() == pytest.approx(0)
    assert frame["chp_fuel_mwh"].tolist() == pytest.approx(
        ((frame["chp_steam_mw"] + frame["chp_electricity_mw"]) / chp_efficiency).tolist()
    )
    assert frame["boiler_fuel_mwh"].tolist() == pytest.approx(
        (frame["boiler_steam_mw"] / boiler_efficiency).tolist()
    )
    assert frame["chp_electricity_mw"].tolist() == pytest.approx(
        (frame["chp_steam_mw"] * power_to_heat).tolist()
    )


def test_high_price_favors_chp_and_low_price_favors_boiler():
    demand, prices, _ = _screen_case()
    chp = solve_dispatch(demand, prices, ASSUMPTIONS, FULL_FLEX).frame["chp_steam_mw"]
    # Low, high, low, high. Boiler capacity is 100 MW and CHP capacity is 110 MW.
    assert chp.tolist() == pytest.approx([0.0, 110.0, 20.0, 90.0], abs=1e-3)


def test_break_even_uses_current_fuel_and_efficiency_assumptions():
    fuel = fuel_price_breakdown(ASSUMPTIONS)["blended_fuel_cost_eur_mwh"]
    boiler_efficiency = ASSUMPTIONS.value("boiler", "thermal_efficiency")
    chp_efficiency = ASSUMPTIONS.value("chp", "total_utilization_efficiency")
    power_to_heat = ASSUMPTIONS.value("chp", "power_to_heat_ratio")
    expected = (
        fuel * (1.0 + power_to_heat) / chp_efficiency - fuel / boiler_efficiency
    ) / power_to_heat
    assert calculate_chp_break_even_price(ASSUMPTIONS) == pytest.approx(expected)


def test_full_flex_cost_does_not_exceed_the_reference_dispatch():
    demand, prices, _ = _screen_case()
    reference = dispatch_baseline(demand, ASSUMPTIONS)
    reference_cost = float(
        cost_baseline(reference, prices, ASSUMPTIONS)["total_variable_energy_cost_eur"].sum()
    )
    optimized = solve_dispatch(demand, prices, ASSUMPTIONS, FULL_FLEX)
    optimized_cost = float(optimized.frame["total_variable_energy_cost_eur"].sum())
    assert optimized_cost <= reference_cost + 1e-6


def test_redispatch_only_preserves_annual_chp_electricity():
    demand, prices, _ = _screen_case()
    reference = dispatch_baseline(demand, ASSUMPTIONS)
    target = float(reference["chp_electricity_mw"].sum())
    optimized = solve_dispatch(
        demand,
        prices,
        ASSUMPTIONS,
        REDISPATCH_ONLY,
        baseline_chp_electricity_mwh=target,
    )
    assert float(optimized.frame["chp_electricity_mw"].sum()) == pytest.approx(target, abs=1e-3)


def test_import_capacity_limits_how_far_chp_can_fall():
    break_even = calculate_chp_break_even_price(ASSUMPTIONS)
    index = _hours(1)
    demand = _demand(index, [100.0], [40.0])
    prices = _prices(index, [break_even - 40.0])
    limited = _Override(ASSUMPTIONS, {("grid", "import_capacity_mw"): 10.0})
    frame = solve_dispatch(demand, prices, limited, FULL_FLEX).frame
    assert frame["grid_import_mw"].iloc[0] == pytest.approx(10.0, abs=1e-3)
    assert frame["chp_steam_mw"].iloc[0] == pytest.approx(60.0, abs=1e-3)


def test_infeasible_steam_demand_raises_before_a_useless_solve():
    index = _hours(1)
    demand = _demand(index, [300.0], [30.0])
    prices = _prices(index, [100.0])
    with pytest.raises(OptimizationError, match="Steam demand is infeasible"):
        solve_dispatch(demand, prices, ASSUMPTIONS, FULL_FLEX)


def test_import_price_below_export_price_warns():
    index = _hours(1)
    prices = _prices(index, [100.0])
    prices["electricity_export_price_eur_mwh"] = 120.0
    with pytest.warns(OptimizationWarning, match="grid arbitrage"):
        _warn_if_arbitrage_prices(
            prices["electricity_import_price_eur_mwh"],
            prices["electricity_export_price_eur_mwh"],
        )
