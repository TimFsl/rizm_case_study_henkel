"""Network-tariff cost identities and the continuous peak-import variable."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pyomo.environ as pyo
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.assumptions import load_assumptions
from src.costs import (
    CostError,
    commodity_electricity_prices,
    cost_baseline,
    network_cost_breakdown,
    network_demand_charge_eur,
    network_energy_cost_eur,
    read_network_tariff,
    tariff_regime_is_consistent,
    utilization_hours,
)
from src.optimization import FULL_FLEX, build_dispatch_model, solve_dispatch

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


def test_network_energy_cost_uses_imported_mwh():
    imports = pd.Series([1.5, 2.0, 0.5])
    assert network_energy_cost_eur(imports, 7.90) == pytest.approx(4.0 * 7.90)


def test_demand_charge_converts_mw_to_kw():
    assert network_demand_charge_eur(2.0, 123.93) == pytest.approx(2.0 * 1000.0 * 123.93)
    assert network_demand_charge_eur(0.5, 19.30) == pytest.approx(500.0 * 19.30)


def test_utilization_flags_around_2500_hours():
    assert tariff_regime_is_consistent("hv_high_utilization", 2500.0, 2500.0)
    assert tariff_regime_is_consistent("hv_high_utilization", 2499.9995, 2500.0)
    assert not tariff_regime_is_consistent("hv_high_utilization", 2499.0, 2500.0)
    assert tariff_regime_is_consistent("hv_low_utilization", 2499.0, 2500.0)
    assert not tariff_regime_is_consistent("hv_low_utilization", 2500.0, 2500.0)
    assert not tariff_regime_is_consistent("hv_low_utilization", 2499.9995, 2500.0)
    assert utilization_hours(10.0, 0.0) is None
    assert not tariff_regime_is_consistent("hv_high_utilization", None, 2500.0)


def test_published_tariff_proxy_values():
    high = read_network_tariff(ASSUMPTIONS, "hv_high_utilization")
    low = read_network_tariff(ASSUMPTIONS, "hv_low_utilization")
    assert high.energy_charge_eur_per_mwh == pytest.approx(7.90)
    assert high.demand_charge_eur_per_kw_a == pytest.approx(123.93)
    assert low.energy_charge_eur_per_mwh == pytest.approx(49.70)
    assert low.demand_charge_eur_per_kw_a == pytest.approx(19.30)
    assert ASSUMPTIONS.value("grid_tariffs", "voltage_level") == "high_voltage"


def test_baseline_and_optimized_network_costs_are_separate_from_commodity_cost():
    high = read_network_tariff(ASSUMPTIONS, "hv_high_utilization")
    imports = pd.Series([1.0, 3.0, 0.0], dtype=float)
    breakdown = network_cost_breakdown(imports, high)
    assert breakdown["energy_eur"] == pytest.approx(4.0 * 7.90)
    assert breakdown["demand_eur"] == pytest.approx(3.0 * 1000.0 * 123.93)
    assert breakdown["total_eur"] == pytest.approx(
        breakdown["energy_eur"] + breakdown["demand_eur"]
    )
    assert breakdown["utilization_hours"] == pytest.approx(4.0 / 3.0)
    assert breakdown["consistent"] is False

    index = _hours(1)
    dispatch = pd.DataFrame(
        {
            "steam_demand_mw": [100.0],
            "electricity_demand_mw": [40.0],
            "chp_steam_mw": [52.0],
            "boiler_steam_mw": [48.0],
            "chp_electricity_mw": [26.0],
            "chp_fuel_mwh": [90.6976744186],
            "boiler_fuel_mwh": [53.3333333333],
            "grid_import_mw": [14.0],
            "grid_export_mw": [0.0],
        },
        index=index,
    )
    prices = commodity_electricity_prices(_prices([100.0]), ASSUMPTIONS)
    costed = cost_baseline(dispatch, prices, ASSUMPTIONS)
    assert float(costed["grid_import_cost_eur"].sum()) == pytest.approx(14.0 * 100.0)
    assert float(costed["grid_export_revenue_eur"].sum()) == pytest.approx(0.0)
    network = network_energy_cost_eur(dispatch["grid_import_mw"], high.energy_charge_eur_per_mwh)
    assert network == pytest.approx(14.0 * 7.90)
    assert float(costed["grid_import_cost_eur"].sum()) == pytest.approx(1400.0)


def test_export_discount_is_applied_and_import_adder_is_not_stacked():
    prices = commodity_electricity_prices(_prices([80.0, 10.0]), ASSUMPTIONS)
    discount = ASSUMPTIONS.value("market", "electricity_export_discount_eur_per_mwh")
    assert discount == pytest.approx(5.0)
    assert prices["electricity_import_price_eur_mwh"].tolist() == pytest.approx([80.0, 10.0])
    assert prices["electricity_export_price_eur_mwh"].tolist() == pytest.approx([75.0, 5.0])
    doubled = _Override(ASSUMPTIONS, {("market", "electricity_import_adder_eur_per_mwh"): 20.0})
    with pytest.raises(CostError, match="double-count"):
        commodity_electricity_prices(_prices([80.0]), doubled)


def test_solved_peak_covers_every_hour_and_matches_the_maximum():
    tariff = read_network_tariff(ASSUMPTIONS, "hv_high_utilization")
    demand = _demand([100.0, 100.0, 100.0, 100.0], [20.0, 70.0, 20.0, 20.0])
    prices = commodity_electricity_prices(_prices([40.0, 120.0, 40.0, 120.0]), ASSUMPTIONS)
    result = solve_dispatch(
        demand, prices, ASSUMPTIONS, FULL_FLEX, network_tariff=tariff
    )
    peak = result.grid_peak_import_mw
    hourly = result.frame["grid_import_mw"]
    assert peak is not None
    assert (hourly <= peak + 1e-6).all()
    assert peak == pytest.approx(float(hourly.max()), abs=1e-4)
    assert result.network_energy_cost_eur == pytest.approx(float(hourly.sum()) * 7.90)
    assert result.network_demand_charge_eur == pytest.approx(peak * 1000.0 * 123.93)
    screening = (
        float(result.frame["total_variable_energy_cost_eur"].sum())
        + result.network_energy_cost_eur
        + result.network_demand_charge_eur
    )
    assert result.solver_objective_eur - result.tie_breaker_cost_eur == pytest.approx(
        screening, abs=1.0
    )
    assert "network_energy_cost_eur" in result.frame.columns
    assert "network_demand_charge_eur" not in result.frame.columns


def test_capacity_constraints_remain_and_no_binary_variables_are_added():
    tariff = read_network_tariff(ASSUMPTIONS, "hv_low_utilization")
    demand = _demand([80.0, 90.0], [30.0, 30.0])
    prices = _prices([50.0, 80.0])
    model = build_dispatch_model(
        steam=demand["steam_demand_mw"],
        electricity=demand["electricity_demand_mw"],
        import_price=prices["electricity_import_price_eur_mwh"],
        export_price=prices["electricity_export_price_eur_mwh"],
        fuel_price=50.0,
        assumptions=ASSUMPTIONS,
        mode=FULL_FLEX,
        network_energy_charge_eur_per_mwh=tariff.energy_charge_eur_per_mwh,
        network_demand_charge_eur_per_kw_a=tariff.demand_charge_eur_per_kw_a,
    )
    assert hasattr(model, "chp_capacity")
    assert hasattr(model, "boiler_capacity")
    assert hasattr(model, "import_capacity")
    assert hasattr(model, "grid_peak_import")
    assert pyo.value(model.chp_capacity[0].upper) == pytest.approx(
        ASSUMPTIONS.value("chp", "max_heat_output_mw")
    )
    assert pyo.value(model.boiler_capacity[0].upper) == pytest.approx(
        ASSUMPTIONS.value("boiler", "max_heat_output_mw")
    )
    assert pyo.value(model.import_capacity[0].upper) == pytest.approx(
        ASSUMPTIONS.value("grid", "import_capacity_mw")
    )
    for variable in model.component_data_objects(pyo.Var):
        assert not variable.is_binary()
        assert not variable.is_integer()
