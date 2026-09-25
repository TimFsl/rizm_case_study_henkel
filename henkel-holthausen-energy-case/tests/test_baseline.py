"""Tests for the calibrated rule-based reference dispatch."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.assumptions import load_assumptions
from src.baseline import (
    BALANCE_TOLERANCE_MW,
    BaselineError,
    dispatch_baseline,
    validate_baseline_dispatch,
)
from src.profiles import build_demand_profile

ASSUMPTIONS = load_assumptions()
SCENARIOS = ("flat", "base", "variable")
COST_COLUMNS = (
    "electricity_price",
    "gas_price",
    "carbon_price",
    "fuel_cost",
    "electricity_cost",
    "total_cost",
    "savings",
    "EUR_per_tonne",
)


def _profiles():
    return {name: build_demand_profile(ASSUMPTIONS, name) for name in SCENARIOS}


def test_all_scenarios_close_physical_balances():
    power_to_heat = ASSUMPTIONS.value("chp", "power_to_heat_ratio")
    chp_max = ASSUMPTIONS.value("chp", "max_heat_output_mw")
    boiler_max = ASSUMPTIONS.value("boiler", "max_heat_output_mw")
    import_capacity = ASSUMPTIONS.value("grid", "import_capacity_mw")
    chp_efficiency = ASSUMPTIONS.value("chp", "total_utilization_efficiency")
    boiler_efficiency = ASSUMPTIONS.value("boiler", "thermal_efficiency")

    for demand in _profiles().values():
        dispatch = dispatch_baseline(demand, ASSUMPTIONS)
        validate_baseline_dispatch(dispatch, ASSUMPTIONS)

        steam_gap = (
            dispatch["chp_steam_mw"] + dispatch["boiler_steam_mw"] - dispatch["steam_demand_mw"]
        ).abs()
        electricity_gap = (
            dispatch["chp_electricity_mw"]
            + dispatch["grid_import_mw"]
            - dispatch["grid_export_mw"]
            - dispatch["electricity_demand_mw"]
        ).abs()
        assert steam_gap.max() <= BALANCE_TOLERANCE_MW
        assert electricity_gap.max() <= BALANCE_TOLERANCE_MW
        assert (dispatch["chp_steam_mw"] >= -BALANCE_TOLERANCE_MW).all()
        assert (dispatch["boiler_steam_mw"] >= -BALANCE_TOLERANCE_MW).all()
        assert (dispatch["chp_electricity_mw"] >= -BALANCE_TOLERANCE_MW).all()
        for column in ("chp_fuel_mwh", "boiler_fuel_mwh", "total_fuel_mwh"):
            assert (dispatch[column] >= -BALANCE_TOLERANCE_MW).all()
        assert (dispatch["chp_steam_mw"] <= chp_max + BALANCE_TOLERANCE_MW).all()
        assert (dispatch["boiler_steam_mw"] <= boiler_max + BALANCE_TOLERANCE_MW).all()
        assert (dispatch["grid_import_mw"] <= import_capacity + BALANCE_TOLERANCE_MW).all()
        both = (dispatch["grid_import_mw"] > BALANCE_TOLERANCE_MW) & (
            dispatch["grid_export_mw"] > BALANCE_TOLERANCE_MW
        )
        assert not both.any()

        producing = dispatch["chp_fuel_mwh"] > BALANCE_TOLERANCE_MW
        useful = dispatch["chp_steam_mw"] + dispatch["chp_electricity_mw"]
        chp_ratio = useful.loc[producing] / dispatch.loc[producing, "chp_fuel_mwh"]
        assert (chp_ratio - chp_efficiency).abs().max() <= 1e-9

        boiler_on = dispatch["boiler_steam_mw"] > BALANCE_TOLERANCE_MW
        boiler_ratio = (
            dispatch.loc[boiler_on, "boiler_steam_mw"]
            / dispatch.loc[boiler_on, "boiler_fuel_mwh"]
        )
        assert (boiler_ratio - boiler_efficiency).abs().max() <= 1e-9

        fuel_gap = (
            dispatch["fossil_gas_fuel_mwh"]
            + dispatch["biomethane_fuel_mwh"]
            + dispatch["coal_fuel_mwh"]
            - dispatch["total_fuel_mwh"]
        ).abs()
        assert fuel_gap.max() <= BALANCE_TOLERANCE_MW
        assert dispatch["chp_electricity_mw"].sum() == pytest.approx(
            dispatch["chp_steam_mw"].sum() * power_to_heat,
            rel=1e-9,
        )
        assert (
            dispatch["chp_steam_mw"] + dispatch["boiler_steam_mw"]
        ).sum() == pytest.approx(dispatch["steam_demand_mw"].sum(), rel=1e-9)
        assert not any(column in dispatch.columns for column in COST_COLUMNS)


def test_flat_chp_steam_share_matches_configuration_when_capacity_is_slack():
    dispatch = dispatch_baseline(_profiles()["flat"], ASSUMPTIONS)
    share = ASSUMPTIONS.value("chp", "baseline_steam_share")
    chp_max = ASSUMPTIONS.value("chp", "max_heat_output_mw")
    boiler_max = ASSUMPTIONS.value("boiler", "max_heat_output_mw")
    assert dispatch["chp_steam_mw"].max() < 0.99 * chp_max
    assert dispatch["boiler_steam_mw"].max() < 0.99 * boiler_max
    actual_share = dispatch["chp_steam_mw"].sum() / dispatch["steam_demand_mw"].sum()
    assert actual_share == pytest.approx(share, abs=1e-6)


def test_steam_demand_above_plant_capacity_raises():
    demand = _profiles()["flat"].iloc[:2].copy()
    chp_max = ASSUMPTIONS.value("chp", "max_heat_output_mw")
    boiler_max = ASSUMPTIONS.value("boiler", "max_heat_output_mw")
    demand["steam_demand_mw"] = chp_max + boiler_max + 1.0
    with pytest.raises(BaselineError, match="infeasible"):
        dispatch_baseline(demand, ASSUMPTIONS)
