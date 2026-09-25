"""Tests for synthetic hourly demand profiles."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.assumptions import AssumptionError, load_assumptions, validate_assumptions
from src.profiles import (
    ENERGY_TOLERANCE_MWH,
    ProfileError,
    build_demand_profile,
    build_hourly_index,
)

ASSUMPTIONS = load_assumptions()
SCENARIOS = ("flat", "base", "variable")


def _spread(profile, column: str) -> float:
    series = profile[column]
    return float((series.max() - series.min()) / series.mean())


def test_hourly_index_has_8760_unique_timestamps():
    index = build_hourly_index(2026, "Europe/Berlin")
    assert len(index) == 8760
    assert index.is_unique
    assert str(index.tz) == "Europe/Berlin"
    spring = index[index.date == pd.Timestamp("2026-03-29").date()]
    autumn = index[index.date == pd.Timestamp("2026-10-25").date()]
    assert len(spring) == 23
    assert len(autumn) == 25


def test_all_scenarios_match_annual_energy_without_invalid_values():
    steam_mwh = ASSUMPTIONS.value("demand", "annual_steam_heat_gwh") * 1000
    electricity_mwh = ASSUMPTIONS.value("demand", "annual_electricity_gwh") * 1000
    for scenario in SCENARIOS:
        profile = build_demand_profile(ASSUMPTIONS, scenario)
        assert len(profile) == 8760
        assert profile.index.is_unique
        assert list(profile.columns) == ["steam_demand_mw", "electricity_demand_mw"]
        assert not profile.isna().any().any()
        assert (profile >= 0).all().all()
        assert profile["steam_demand_mw"].sum() == pytest.approx(
            steam_mwh, abs=ENERGY_TOLERANCE_MWH
        )
        assert profile["electricity_demand_mw"].sum() == pytest.approx(
            electricity_mwh, abs=ENERGY_TOLERANCE_MWH
        )


def test_scenario_shapes_increase_from_flat_to_variable():
    profiles = {name: build_demand_profile(ASSUMPTIONS, name) for name in SCENARIOS}
    flat_steam = profiles["flat"]["steam_demand_mw"]
    assert flat_steam.max() == pytest.approx(flat_steam.min())
    assert profiles["flat"]["electricity_demand_mw"].nunique() == 1
    for column in ("steam_demand_mw", "electricity_demand_mw"):
        flat_spread = _spread(profiles["flat"], column)
        base_spread = _spread(profiles["base"], column)
        variable_spread = _spread(profiles["variable"], column)
        assert flat_spread == pytest.approx(0.0, abs=1e-12)
        assert base_spread > flat_spread
        assert variable_spread > base_spread


def test_unknown_scenario_raises():
    with pytest.raises(ProfileError, match="Unknown demand profile scenario"):
        build_demand_profile(ASSUMPTIONS, "measured")


def test_missing_shape_factor_raises():
    data = ASSUMPTIONS.as_dict()
    del data["demand_profile_scenarios"]["base"]["steam"]["hourly_factors"][0]
    with pytest.raises(AssumptionError, match="hourly_factors"):
        validate_assumptions(data)
