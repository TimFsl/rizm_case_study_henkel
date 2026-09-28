"""Tests for the forward fuel-cost cashflow construction. No hourly solve."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.electric_boiler import investment_cashflows
from src.forward_cost import (
    biomethane_cost,
    effective_fossil_gas_cost,
    interpolated_annual_benefit,
    mean_shape_benefit,
)
from src.investment import net_present_value


def test_effective_fossil_cost_adds_eua_and_biomethane_does_not():
    factor = 0.2016
    fossil = effective_fossil_gas_cost(40.0, 80.0, factor)
    assert fossil == pytest.approx(40.0 + 0.2016 * 80.0)
    assert biomethane_cost(40.0, 15.0) == pytest.approx(55.0)
    assert biomethane_cost(30.0, 15.0) == pytest.approx(45.0)
    assert fossil != biomethane_cost(40.0, 15.0)


def test_anchor_benefit_is_the_unweighted_mean_of_the_three_shapes():
    assert mean_shape_benefit([100.0, 0.0, 50.0]) == pytest.approx(50.0)


def test_annual_benefit_is_linear_between_optimized_anchors():
    years = list(range(2026, 2041))
    series = interpolated_annual_benefit({2026: 100.0, 2030: 180.0, 2040: 280.0}, years)
    assert series[2026] == pytest.approx(100.0)
    assert series[2028] == pytest.approx(140.0)
    assert series[2030] == pytest.approx(180.0)
    assert series[2035] == pytest.approx(230.0)
    assert series[2040] == pytest.approx(280.0)
    assert len(series) == 15


def test_npv_uses_each_interpolated_year_rather_than_one_constant_benefit():
    years = list(range(2026, 2041))
    anchors = {2026: 100_000.0, 2030: 200_000.0, 2040: 200_000.0}
    annual = interpolated_annual_benefit(anchors, years)
    fixed = 17_000.0
    capex = 1_000_000.0
    nets = [annual[year] - fixed for year in years]
    metrics = investment_cashflows(capex, nets, 0.10)
    expected = net_present_value(0.10, [-capex, *nets])
    constant = investment_cashflows(capex, [100_000.0 - fixed] * 15, 0.10)
    assert metrics["npv_eur"] == pytest.approx(expected)
    assert metrics["npv_eur"] > constant["npv_eur"]
    assert annual[2028] == pytest.approx(150_000.0)
