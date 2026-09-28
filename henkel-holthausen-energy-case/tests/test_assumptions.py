"""Tests for the v0.1 assumptions loader."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.assumptions import AssumptionError, load_assumptions, validate_assumptions


def test_valid_configuration_loads():
    assumptions = load_assumptions()
    assert assumptions.value("demand", "annual_steam_heat_gwh") == 1040
    assert assumptions.value("demand", "annual_electricity_gwh") == 290
    assert assumptions.value("historical_reference", "steam_production_2016") == 1_500_000
    assert assumptions.value("historical_reference", "fuel_input_plausibility_twh") == 1.55
    assert assumptions.value("grid", "import_capacity_mw") == 64
    assert assumptions.value("model", "hours_per_year") == 8760
    assert assumptions.value("chp", "total_utilization_efficiency") == 0.86
    assert assumptions.value("chp", "power_to_heat_ratio") == 0.50
    assert assumptions.value("chp", "max_heat_output_mw") == 110
    assert assumptions.value("chp", "baseline_steam_share") == 0.52
    assert assumptions.value("boiler", "max_heat_output_mw") == 100
    assert "steam_peak_mw_reference" not in assumptions.section("demand")
    assert "electricity_peak_mw_reference" not in assumptions.section("demand")
    assert assumptions.value("storen_reference", "steam_peak_2030_mw") == 168.4

    ratio = assumptions.parameter("chp", "power_to_heat_ratio")
    assert ratio["type"] == "assumed"
    assert ratio["confidence"] == "low"
    assert ratio["critical"] is True
    assert ratio["sensitivity"] == [0.30, 0.50, 0.70]
    assert assumptions.is_tbd("chp", "power_to_heat_ratio") is False


def test_demand_provenance_does_not_start_from_fuel_input():
    assumptions = load_assumptions()
    steam = assumptions.parameter("demand", "annual_steam_heat_gwh")["source_or_rationale"]
    electricity = assumptions.parameter("demand", "annual_electricity_gwh")[
        "source_or_rationale"
    ]
    fuel = assumptions.parameter(
        "historical_reference", "fuel_input_plausibility_twh"
    )["source_or_rationale"]
    assert "1.5 Mt/a" in steam
    assert "0.65-0.70" in steam
    assert "1.55" not in steam
    assert "0.86" not in steam
    assert "22/78" in electricity
    assert "290 GWh/a" in electricity
    assert "0.86" not in electricity
    assert "not used to derive current steam or electricity demand" in fuel


def test_invalid_fuel_shares_raise():
    data = load_assumptions().as_dict()
    data["fuel_mix"]["fossil_gas_share"]["value"] = 0.80
    with pytest.raises(AssumptionError, match="sum to 1"):
        validate_assumptions(data)


def test_efficiency_above_one_raises():
    data = load_assumptions().as_dict()
    data["boiler"]["thermal_efficiency"]["value"] = 1.01
    with pytest.raises(AssumptionError, match="thermal_efficiency"):
        validate_assumptions(data)


def test_power_to_heat_ratio_must_be_positive():
    data = load_assumptions().as_dict()
    data["chp"]["power_to_heat_ratio"]["value"] = 0
    with pytest.raises(AssumptionError, match="power_to_heat_ratio"):
        validate_assumptions(data)


def test_power_to_heat_ratio_may_exceed_one():
    data = load_assumptions().as_dict()
    data["chp"]["power_to_heat_ratio"]["value"] = 1.20
    validate_assumptions(data)


def test_negative_heat_capacity_raises():
    data = load_assumptions().as_dict()
    data["chp"]["max_heat_output_mw"]["value"] = -10
    with pytest.raises(AssumptionError, match="max_heat_output_mw"):
        validate_assumptions(data)


def test_baseline_steam_share_must_be_between_zero_and_one():
    data = load_assumptions().as_dict()
    data["chp"]["baseline_steam_share"]["value"] = 1.10
    with pytest.raises(AssumptionError, match="baseline_steam_share"):
        validate_assumptions(data)


def test_historical_steam_load_max_must_exceed_min():
    data = load_assumptions().as_dict()
    data["historical_reference"]["steam_load_max_2012"]["value"] = 100
    with pytest.raises(AssumptionError, match="steam_load_max_2012"):
        validate_assumptions(data)


def test_storen_energy_shares_must_sum_to_one():
    data = load_assumptions().as_dict()
    data["storen_reference"]["steam_energy_share_2018"]["value"] = 0.90
    with pytest.raises(AssumptionError, match="sum to 1"):
        validate_assumptions(data)


def test_plant_scenario_total_must_equal_chp_plus_boiler():
    data = load_assumptions().as_dict()
    data["plant_structure_scenarios"]["chp_heavy"]["total_heat_capacity_mw"] = 200
    with pytest.raises(AssumptionError, match="chp_heavy"):
        validate_assumptions(data)


def test_balanced_scenario_must_match_base_capacities():
    data = load_assumptions().as_dict()
    data["chp"]["max_heat_output_mw"]["value"] = 90
    with pytest.raises(AssumptionError, match="balanced.chp_max_heat_mw"):
        validate_assumptions(data)


def test_export_capacity_is_a_screening_assumption():
    assumptions = load_assumptions()
    export = assumptions.parameter("grid", "export_capacity_mw")
    assert export["value"] == 10.0
    assert export["type"] == "assumed"
    assert export["critical"] is True
    assert export["sensitivity"] == [5, 10, 20]
    assert assumptions.is_tbd("grid", "export_capacity_mw") is False


def test_tbd_parameters_are_allowed():
    assumptions = load_assumptions()
    assert assumptions.is_tbd("economics", "henkel_value_allocation_factor")
    assert assumptions.is_tbd("market", "electricity_import_adder_eur_per_mwh") is False
    assert assumptions.critical_tbd() == []

    data = assumptions.as_dict()
    data["demand"]["annual_steam_heat_gwh"]["value"] = None
    data["demand"]["annual_steam_heat_gwh"]["type"] = "TBD"
    with pytest.raises(AssumptionError, match="annual_steam_heat_gwh"):
        validate_assumptions(data)


def test_henkel_production_is_not_duplicated_under_economics():
    assumptions = load_assumptions()
    assert assumptions.value("site", "annual_henkel_production_tonnes") == 455000
    assert "annual_henkel_production_tonnes" not in assumptions.section("economics")

    data = assumptions.as_dict()
    data["economics"]["annual_henkel_production_tonnes"] = {
        "value": 455000,
        "unit": "t/a",
        "type": "derived",
        "confidence": "low",
    }
    with pytest.raises(AssumptionError, match="duplicates"):
        validate_assumptions(data)


def test_screening_plant_parameters_cannot_return_to_null():
    data = load_assumptions().as_dict()
    data["boiler"]["max_heat_output_mw"]["value"] = None
    data["boiler"]["max_heat_output_mw"]["type"] = "TBD"
    with pytest.raises(AssumptionError, match="max_heat_output_mw"):
        validate_assumptions(data)
