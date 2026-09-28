"""Investment-metric tests for the electrode-boiler screen."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.investment import (
    capital_recovery_factor,
    carbon_prices_for_years,
    equivalent_annual_value,
    internal_rate_of_return,
    net_present_value,
    payback_years,
    select_capacity_by_npv,
)


def test_carbon_path_interpolates_between_anchors_and_holds_the_ends():
    anchors = {2026: 80, 2030: 110, 2040: 150}
    prices = carbon_prices_for_years(anchors, [2026, 2028, 2030, 2035, 2040, 2042])
    assert prices[2026] == pytest.approx(80)
    assert prices[2028] == pytest.approx(95)
    assert prices[2030] == pytest.approx(110)
    assert prices[2035] == pytest.approx(130)
    assert prices[2040] == pytest.approx(150)
    assert prices[2042] == pytest.approx(150)


def test_high_carbon_path_midpoints():
    prices = carbon_prices_for_years(
        {2026: 80, 2030: 140, 2040: 300},
        [2028, 2035],
    )
    assert prices[2028] == pytest.approx(110)
    assert prices[2035] == pytest.approx(220)


def test_npv_discounts_the_first_operating_cashflow_by_one_year():
    value = net_present_value(0.10, [-100.0, 60.0, 60.0])
    expected = -100.0 + 60.0 / 1.10 + 60.0 / 1.21
    assert value == pytest.approx(expected)


def test_irr_of_a_one_year_10_percent_project():
    assert internal_rate_of_return([-100.0, 110.0]) == pytest.approx(0.10, abs=1e-6)


def test_irr_of_a_zero_project_is_undefined():
    assert internal_rate_of_return([0.0, 0.0, 0.0]) is None


def test_simple_and_discounted_payback():
    assert payback_years([-100.0, 40.0, 40.0, 40.0], 0.0) == pytest.approx(2.5)
    discounted = payback_years([-100.0, 60.0, 60.0], 0.10)
    recovered = 45.4545454545 / (60.0 / 1.21)
    assert discounted == pytest.approx(1.0 + recovered, abs=1e-6)
    assert payback_years([-100.0, 10.0], 0.0) is None


def test_crf_and_eav():
    crf = capital_recovery_factor(0.10, 15)
    growth = 1.10**15
    assert crf == pytest.approx(0.10 * growth / (growth - 1.0))
    assert equivalent_annual_value(1000.0, 0.10, 15) == pytest.approx(1000.0 * crf)


def test_capacity_is_selected_by_maximum_npv_and_ties_prefer_the_smaller_size():
    assert select_capacity_by_npv({0: 0.0, 10: 5.0, 20: 4.0}) == pytest.approx(10)
    assert select_capacity_by_npv({10: 5.0, 20: 5.0}) == pytest.approx(10)
