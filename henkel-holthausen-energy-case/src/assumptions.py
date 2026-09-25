"""Load and validate config/assumptions.yaml.

Null values are allowed only for parameters listed in TBD_PARAMETERS.
"""

from __future__ import annotations

import math
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterator
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

REQUIRED_SECTIONS = (
    "metadata",
    "site",
    "demand",
    "historical_reference",
    "storen_reference",
    "grid",
    "fuel_mix",
    "chp",
    "boiler",
    "plant_structure_scenarios",
    "market",
    "emissions",
    "model",
    "demand_profile_scenarios",
    "economics",
)

REQUIRED_PARAMETERS = (
    ("site", "model_scope"),
    ("site", "company_allocation"),
    ("site", "steam_pressure_levels_real"),
    ("site", "steam_pressure_levels_modeled"),
    ("site", "annual_henkel_production_tonnes"),
    ("demand", "annual_steam_heat_gwh"),
    ("demand", "annual_electricity_gwh"),
    ("historical_reference", "steam_production_2012"),
    ("historical_reference", "electricity_generation_2012"),
    ("historical_reference", "steam_load_min_2012"),
    ("historical_reference", "steam_load_max_2012"),
    ("historical_reference", "electricity_generation_2016"),
    ("historical_reference", "total_energy_utilization_2016"),
    ("storen_reference", "reference_year"),
    ("storen_reference", "steam_energy_share_2018"),
    ("storen_reference", "electricity_energy_share_2018"),
    ("storen_reference", "steam_peak_2030_mw"),
    ("storen_reference", "electricity_peak_2030_mw"),
    ("grid", "import_capacity_mw"),
    ("grid", "export_capacity_mw"),
    ("fuel_mix", "coal_share"),
    ("fuel_mix", "fossil_gas_share"),
    ("fuel_mix", "biomethane_share"),
    ("chp", "total_utilization_efficiency"),
    ("chp", "power_to_heat_ratio"),
    ("chp", "max_heat_output_mw"),
    ("chp", "baseline_steam_share"),
    ("chp", "min_load_fraction"),
    ("chp", "ramp_rate_constraint"),
    ("chp", "startup_costs_enabled"),
    ("boiler", "thermal_efficiency"),
    ("boiler", "max_heat_output_mw"),
    ("market", "representative_market_year"),
    ("market", "electricity_import_price_method"),
    ("market", "electricity_export_price_method"),
    ("market", "electricity_import_adder_eur_per_mwh"),
    ("market", "electricity_export_discount_eur_per_mwh"),
    ("market", "natural_gas_commodity_eur_per_mwh"),
    ("market", "gas_variable_adder_eur_per_mwh"),
    ("market", "biomethane_premium_eur_per_mwh"),
    ("market", "fossil_gas_price_method"),
    ("market", "biomethane_price_method"),
    ("emissions", "carbon_price_eur_per_tco2"),
    ("emissions", "fossil_gas_emission_factor_tco2_per_mwh_fuel"),
    ("emissions", "biomethane_emission_factor_tco2_per_mwh_fuel"),
    ("model", "time_resolution"),
    ("model", "hours_per_year"),
    ("model", "model_year"),
    ("model", "timezone"),
    ("model", "steam_demand_profile"),
    ("model", "electricity_demand_profile"),
    ("model", "demand_profile_method"),
    ("model", "district_heating_enabled"),
    ("model", "steam_storage_enabled"),
    ("model", "third_party_split_enabled"),
    ("economics", "value_scope"),
    ("economics", "henkel_value_allocation_factor"),
)

REQUIRED_SCENARIOS = ("boiler_heavy", "balanced", "chp_heavy")
PROFILE_SCENARIO_NAMES = ("flat", "base", "variable")
PROFILE_CARRIERS = ("steam", "electricity")
PROFILE_WEEKDAYS = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)
PROFILE_FACTOR_GROUPS = (
    ("hourly_factors", tuple(range(24))),
    ("weekday_factors", PROFILE_WEEKDAYS),
    ("monthly_factors", tuple(range(1, 13))),
)
SCENARIO_CAPACITY_KEYS = (
    "chp_max_heat_mw",
    "boiler_max_heat_mw",
    "total_heat_capacity_mw",
)

# Parameters that may stay null until site or market data are available.
# CHP power-to-heat ratio and CHP/boiler heat capacities are screening
# assumptions, so they are no longer allowed to be null.
TBD_PARAMETERS = frozenset(
    {
        ("grid", "export_capacity_mw"),
        ("economics", "henkel_value_allocation_factor"),
    }
)

EFFICIENCY_PARAMETERS = (
    ("chp", "total_utilization_efficiency"),
    ("boiler", "thermal_efficiency"),
)

# Screening plant parameters. Positive when set. The power-to-heat ratio
# is not an efficiency and may be above 1.
POSITIVE_PARAMETERS = (
    ("chp", "power_to_heat_ratio"),
    ("chp", "max_heat_output_mw"),
    ("boiler", "max_heat_output_mw"),
)

BOOLEAN_PARAMETERS = (
    ("chp", "ramp_rate_constraint"),
    ("chp", "startup_costs_enabled"),
    ("model", "district_heating_enabled"),
    ("model", "steam_storage_enabled"),
    ("model", "third_party_split_enabled"),
)

FUEL_SHARE_PARAMETERS = (
    ("fuel_mix", "coal_share"),
    ("fuel_mix", "fossil_gas_share"),
    ("fuel_mix", "biomethane_share"),
)

ALLOWED_TYPES = frozenset(
    {"public", "derived", "assumed", "modeling_choice", "TBD"}
)
ALLOWED_CONFIDENCE = frozenset({"high", "medium", "low"})

METADATA_FIELDS = (
    "model_name",
    "model_version",
    "site_name",
    "description",
)


class AssumptionError(ValueError):
    """Raised when assumptions are missing, incomplete, or inconsistent."""


def default_assumptions_path() -> Path:
    return Path(__file__).resolve().parents[1] / "config" / "assumptions.yaml"


def load_assumptions(path: Path | str | None = None) -> "Assumptions":
    return Assumptions.load(path)


def validate_assumptions(data: dict) -> None:
    """Raise AssumptionError if data violates the v0.1 assumption rules."""
    if not isinstance(data, dict):
        raise AssumptionError("Assumptions must be a mapping")

    missing = [name for name in REQUIRED_SECTIONS if name not in data]
    if missing:
        raise AssumptionError(
            "Missing required sections: " + ", ".join(missing)
        )

    for name in REQUIRED_SECTIONS:
        if not isinstance(data[name], dict):
            raise AssumptionError(f"Section {name} must be a mapping")

    _validate_metadata(data)
    _validate_parameters(data)
    _validate_fuel_mix(data)
    _validate_efficiencies(data)
    _validate_positive_parameters(data)
    _validate_baseline_steam_share(data)
    _validate_min_load(data)
    _validate_demand(data)
    _validate_grid(data)
    _validate_steam_levels(data)
    _validate_hours(data)
    _validate_model_clock(data)
    _validate_demand_profile_scenarios(data)
    _validate_historical_reference(data)
    _validate_storen_reference(data)
    _validate_plant_scenarios(data)
    _validate_economics(data)
    _validate_market_prices(data)
    _validate_booleans(data)


class Assumptions:
    """Validated view of the screening assumptions."""

    def __init__(self, data: dict, source: Path | None = None):
        self._data = data
        self.source = source

    @classmethod
    def load(cls, path: Path | str | None = None) -> "Assumptions":
        source = Path(path) if path is not None else default_assumptions_path()
        if not source.is_file():
            raise AssumptionError(f"Assumptions file not found: {source}")
        with source.open(encoding="utf-8") as handle:
            loaded = yaml.safe_load(handle)
        validate_assumptions(loaded)
        return cls(loaded, source=source)

    def section(self, name: str) -> dict:
        if name not in self._data:
            raise AssumptionError(f"Unknown section: {name}")
        section = self._data[name]
        if not isinstance(section, dict):
            raise AssumptionError(f"Section {name} must be a mapping")
        return section

    def parameter(self, section: str, key: str) -> dict:
        return _parameter(self._data, section, key)

    def value(self, section: str, key: str) -> Any:
        return self.parameter(section, key)["value"]

    def is_tbd(self, section: str, key: str) -> bool:
        """True when the parameter is an explicit null TBD."""
        param = self.parameter(section, key)
        return param.get("value") is None and param.get("type") == "TBD"

    def critical_tbd(self) -> list[str]:
        """Critical parameters that are still null, in file order."""
        names = []
        for section, key, param in _iter_parameters(self._data):
            if param.get("critical") is True and param.get("value") is None:
                names.append(f"{section}.{key}")
        return names

    def as_dict(self) -> dict:
        return deepcopy(self._data)


def _parameter(data: dict, section: str, key: str) -> dict:
    name = f"{section}.{key}"
    if section not in data or not isinstance(data[section], dict):
        raise AssumptionError(f"Missing section: {section}")
    if key not in data[section]:
        raise AssumptionError(f"Missing parameter: {name}")
    param = data[section][key]
    if not isinstance(param, dict) or "value" not in param:
        raise AssumptionError(f"{name} must be a mapping with a value field")
    return param


def _iter_parameters(data: dict) -> Iterator[tuple[str, str, dict]]:
    for section, content in data.items():
        if section == "metadata" or not isinstance(content, dict):
            continue
        for key, param in content.items():
            if isinstance(param, dict) and "value" in param:
                yield section, key, param


def _validate_metadata(data: dict) -> None:
    metadata = data["metadata"]
    for key in METADATA_FIELDS:
        value = metadata.get(key)
        if not isinstance(value, str) or not value.strip():
            raise AssumptionError(f"metadata.{key} must be a non-empty string")


def _validate_parameters(data: dict) -> None:
    for section, key in REQUIRED_PARAMETERS:
        _parameter(data, section, key)
    for section, key, param in _iter_parameters(data):
        _check_parameter_fields(section, key, param)
        _check_sensitivity(section, key, param)


def _check_parameter_fields(section: str, key: str, param: dict) -> None:
    name = f"{section}.{key}"
    if "type" not in param:
        raise AssumptionError(f"{name} must include type")
    parameter_type = param["type"]
    if parameter_type not in ALLOWED_TYPES:
        allowed = ", ".join(sorted(ALLOWED_TYPES))
        raise AssumptionError(
            f"{name} type must be one of {allowed}; got {parameter_type!r}"
        )
    if "confidence" in param and param["confidence"] not in ALLOWED_CONFIDENCE:
        allowed = ", ".join(sorted(ALLOWED_CONFIDENCE))
        raise AssumptionError(
            f"{name} confidence must be one of {allowed}; "
            f"got {param['confidence']!r}"
        )
    if "critical" in param and not isinstance(param["critical"], bool):
        raise AssumptionError(f"{name} critical must be true or false")

    value = param["value"]
    if value is None:
        if parameter_type != "TBD":
            raise AssumptionError(
                f"{name} is null but type is {parameter_type!r}; "
                "null values require type TBD"
            )
        if (section, key) not in TBD_PARAMETERS:
            raise AssumptionError(
                f"{name} is null but is not an allowed TBD parameter"
            )
        return
    if parameter_type == "TBD":
        raise AssumptionError(
            f"{name} has type TBD but a value is set; "
            "change type when the value is known"
        )


def _check_sensitivity(section: str, key: str, param: dict) -> None:
    if "sensitivity" not in param:
        return
    name = f"{section}.{key}"
    values = param["sensitivity"]
    if not isinstance(values, list) or len(values) == 0:
        raise AssumptionError(f"{name} sensitivity must be a non-empty list")
    for item in values:
        number = _require_number(item, f"{name} sensitivity")
        if (section, key) in EFFICIENCY_PARAMETERS and not 0.0 < number <= 1.0:
            raise AssumptionError(
                f"{name} sensitivity values must be greater than 0 and at most 1; "
                f"got {item}"
            )
        if (section, key) in POSITIVE_PARAMETERS and number <= 0:
            raise AssumptionError(
                f"{name} sensitivity values must be positive; got {item}"
            )


def _validate_fuel_mix(data: dict) -> None:
    shares = {}
    for section, key in FUEL_SHARE_PARAMETERS:
        shares[key] = _require_unit_interval(
            _parameter(data, section, key)["value"],
            f"{section}.{key}",
        )
    total = sum(shares.values())
    if not math.isclose(total, 1.0, rel_tol=0.0, abs_tol=1e-6):
        raise AssumptionError(
            "fuel_mix shares must sum to 1.0; "
            f"got coal={_format_number(shares['coal_share'])}, "
            f"fossil_gas={_format_number(shares['fossil_gas_share'])}, "
            f"biomethane={_format_number(shares['biomethane_share'])} "
            f"(sum={_format_number(total)})"
        )


def _validate_efficiencies(data: dict) -> None:
    for section, key in EFFICIENCY_PARAMETERS:
        _require_efficiency(
            _parameter(data, section, key)["value"],
            f"{section}.{key}",
        )


def _validate_positive_parameters(data: dict) -> None:
    for section, key in POSITIVE_PARAMETERS:
        value = _parameter(data, section, key)["value"]
        if value is None:
            continue
        _require_positive(value, f"{section}.{key}")


def _validate_min_load(data: dict) -> None:
    _require_unit_interval(
        _parameter(data, "chp", "min_load_fraction")["value"],
        "chp.min_load_fraction",
    )


def _validate_demand(data: dict) -> None:
    for key in ("annual_steam_heat_gwh", "annual_electricity_gwh"):
        _require_positive(
            _parameter(data, "demand", key)["value"],
            f"demand.{key}",
        )
    for key in ("steam_peak_mw_reference", "electricity_peak_mw_reference"):
        if key in data["demand"]:
            raise AssumptionError(
                f"demand.{key} duplicates storen_reference; "
                "store StoREN peaks only under storen_reference"
            )


def _validate_baseline_steam_share(data: dict) -> None:
    _require_unit_interval(
        _parameter(data, "chp", "baseline_steam_share")["value"],
        "chp.baseline_steam_share",
    )


def _validate_grid(data: dict) -> None:
    _require_positive(
        _parameter(data, "grid", "import_capacity_mw")["value"],
        "grid.import_capacity_mw",
    )


def _validate_steam_levels(data: dict) -> None:
    _require_int_at_least(
        _parameter(data, "site", "steam_pressure_levels_real")["value"],
        "site.steam_pressure_levels_real",
        minimum=1,
    )
    _require_int_at_least(
        _parameter(data, "site", "steam_pressure_levels_modeled")["value"],
        "site.steam_pressure_levels_modeled",
        minimum=1,
    )


def _validate_hours(data: dict) -> None:
    value = _parameter(data, "model", "hours_per_year")["value"]
    hours = _require_number(value, "model.hours_per_year")
    if hours != 8760:
        raise AssumptionError(
            "model.hours_per_year must equal 8760 for the current implementation; "
            f"got {value}"
        )


def _validate_model_clock(data: dict) -> None:
    _require_int_at_least(
        _parameter(data, "model", "model_year")["value"],
        "model.model_year",
        minimum=1,
    )
    timezone = _parameter(data, "model", "timezone")["value"]
    if not isinstance(timezone, str) or not timezone.strip():
        raise AssumptionError("model.timezone must be a non-empty string")
    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise AssumptionError(
            f"model.timezone is not a known timezone: {timezone!r}"
        ) from exc


def _validate_demand_profile_scenarios(data: dict) -> None:
    scenarios = data["demand_profile_scenarios"]
    missing = [name for name in PROFILE_SCENARIO_NAMES if name not in scenarios]
    if missing:
        raise AssumptionError(
            "Missing demand profile scenarios: " + ", ".join(missing)
        )
    for name, scenario in scenarios.items():
        if not isinstance(scenario, dict):
            raise AssumptionError(
                f"demand_profile_scenarios.{name} must be a mapping"
            )
        for carrier in PROFILE_CARRIERS:
            block = scenario.get(carrier)
            if not isinstance(block, dict):
                raise AssumptionError(
                    f"demand_profile_scenarios.{name} is missing {carrier} shape factors"
                )
            _validate_carrier_factors(name, carrier, block)


def _validate_carrier_factors(scenario: str, carrier: str, factors: dict) -> None:
    for group_name, expected in PROFILE_FACTOR_GROUPS:
        prefix = f"demand_profile_scenarios.{scenario}.{carrier}.{group_name}"
        mapping = factors.get(group_name)
        if not isinstance(mapping, dict):
            raise AssumptionError(f"{prefix} is missing")
        converted = {}
        for key, value in mapping.items():
            normalized = _normalize_factor_key(key, prefix)
            _require_positive(value, f"{prefix}.{normalized}")
            if normalized in converted:
                raise AssumptionError(f"{prefix} has a duplicate key {normalized}")
            converted[normalized] = value
        expected_keys = set(expected)
        got = set(converted)
        if got != expected_keys:
            missing = sorted(expected_keys - got, key=str)
            extra = sorted(got - expected_keys, key=str)
            raise AssumptionError(
                f"{prefix} must contain exactly {len(expected_keys)} factors; "
                f"missing {missing}; extra {extra}"
            )


def _normalize_factor_key(key: Any, prefix: str) -> int | str:
    if prefix.endswith("weekday_factors"):
        if not isinstance(key, str):
            raise AssumptionError(
                f"{prefix} keys must be weekday names; got {key!r}"
            )
        return key
    if isinstance(key, bool) or not isinstance(key, (int, float)) or int(key) != key:
        raise AssumptionError(f"{prefix} keys must be integers; got {key!r}")
    return int(key)


def _validate_historical_reference(data: dict) -> None:
    minimum = _require_number(
        _parameter(data, "historical_reference", "steam_load_min_2012")["value"],
        "historical_reference.steam_load_min_2012",
    )
    maximum = _require_number(
        _parameter(data, "historical_reference", "steam_load_max_2012")["value"],
        "historical_reference.steam_load_max_2012",
    )
    if not maximum > minimum:
        raise AssumptionError(
            "historical_reference.steam_load_max_2012 must exceed "
            "steam_load_min_2012; "
            f"got max={_format_number(maximum)} and min={_format_number(minimum)}"
        )
    for key in (
        "steam_production_2012",
        "electricity_generation_2012",
        "electricity_generation_2016",
    ):
        _require_positive(
            _parameter(data, "historical_reference", key)["value"],
            f"historical_reference.{key}",
        )
    _require_unit_interval(
        _parameter(
            data, "historical_reference", "total_energy_utilization_2016"
        )["value"],
        "historical_reference.total_energy_utilization_2016",
    )


def _validate_storen_reference(data: dict) -> None:
    steam = _require_unit_interval(
        _parameter(data, "storen_reference", "steam_energy_share_2018")["value"],
        "storen_reference.steam_energy_share_2018",
    )
    electricity = _require_unit_interval(
        _parameter(
            data, "storen_reference", "electricity_energy_share_2018"
        )["value"],
        "storen_reference.electricity_energy_share_2018",
    )
    total = steam + electricity
    if not math.isclose(total, 1.0, rel_tol=0.0, abs_tol=1e-6):
        raise AssumptionError(
            "storen_reference steam and electricity shares must sum to 1.0; "
            f"got steam={_format_number(steam)}, "
            f"electricity={_format_number(electricity)} "
            f"(sum={_format_number(total)})"
        )
    for key in ("steam_peak_2030_mw", "electricity_peak_2030_mw"):
        _require_positive(
            _parameter(data, "storen_reference", key)["value"],
            f"storen_reference.{key}",
        )


def _validate_plant_scenarios(data: dict) -> None:
    scenarios = data["plant_structure_scenarios"]
    missing = [name for name in REQUIRED_SCENARIOS if name not in scenarios]
    if missing:
        raise AssumptionError(
            "Missing plant structure scenarios: " + ", ".join(missing)
        )
    for name, scenario in scenarios.items():
        _validate_one_scenario(name, scenario)
    _validate_balanced_scenario_matches_base(data, scenarios["balanced"])


def _validate_one_scenario(name: str, scenario: Any) -> None:
    if not isinstance(scenario, dict):
        raise AssumptionError(
            f"plant_structure_scenarios.{name} must be a mapping"
        )
    values = {}
    for key in SCENARIO_CAPACITY_KEYS:
        if key not in scenario:
            raise AssumptionError(
                f"Missing plant_structure_scenarios.{name}.{key}"
            )
        values[key] = _require_positive(
            scenario[key],
            f"plant_structure_scenarios.{name}.{key}",
        )
    expected = values["chp_max_heat_mw"] + values["boiler_max_heat_mw"]
    if not math.isclose(
        values["total_heat_capacity_mw"],
        expected,
        rel_tol=0.0,
        abs_tol=1e-6,
    ):
        raise AssumptionError(
            f"plant_structure_scenarios.{name} total_heat_capacity_mw must "
            "equal chp_max_heat_mw + boiler_max_heat_mw; "
            f"got {_format_number(values['total_heat_capacity_mw'])} "
            f"and {_format_number(expected)}"
        )


def _validate_balanced_scenario_matches_base(data: dict, balanced: dict) -> None:
    pairs = (
        (
            "chp_max_heat_mw",
            "chp",
            "max_heat_output_mw",
        ),
        (
            "boiler_max_heat_mw",
            "boiler",
            "max_heat_output_mw",
        ),
    )
    for scenario_key, section, parameter in pairs:
        base = _require_number(
            _parameter(data, section, parameter)["value"],
            f"{section}.{parameter}",
        )
        scenario_value = _require_number(
            balanced[scenario_key],
            f"plant_structure_scenarios.balanced.{scenario_key}",
        )
        if not math.isclose(scenario_value, base, rel_tol=0.0, abs_tol=1e-6):
            raise AssumptionError(
                f"plant_structure_scenarios.balanced.{scenario_key} must match "
                f"{section}.{parameter}; "
                f"got {_format_number(scenario_value)} and {_format_number(base)}"
            )


def _validate_economics(data: dict) -> None:
    if "annual_henkel_production_tonnes" in data["economics"]:
        raise AssumptionError(
            "economics.annual_henkel_production_tonnes duplicates "
            "site.annual_henkel_production_tonnes; keep the site value "
            "as the single source of truth"
        )


def _validate_market_prices(data: dict) -> None:
    _require_int_at_least(
        _parameter(data, "market", "representative_market_year")["value"],
        "market.representative_market_year",
        minimum=1,
    )
    _require_positive(
        _parameter(data, "market", "natural_gas_commodity_eur_per_mwh")["value"],
        "market.natural_gas_commodity_eur_per_mwh",
    )
    _require_nonnegative(
        _parameter(data, "market", "gas_variable_adder_eur_per_mwh")["value"],
        "market.gas_variable_adder_eur_per_mwh",
    )
    _require_nonnegative(
        _parameter(data, "market", "biomethane_premium_eur_per_mwh")["value"],
        "market.biomethane_premium_eur_per_mwh",
    )
    _require_nonnegative(
        _parameter(data, "market", "electricity_import_adder_eur_per_mwh")["value"],
        "market.electricity_import_adder_eur_per_mwh",
    )
    _require_nonnegative(
        _parameter(data, "market", "electricity_export_discount_eur_per_mwh")["value"],
        "market.electricity_export_discount_eur_per_mwh",
    )
    _require_nonnegative(
        _parameter(data, "emissions", "carbon_price_eur_per_tco2")["value"],
        "emissions.carbon_price_eur_per_tco2",
    )
    _require_positive(
        _parameter(data, "emissions", "fossil_gas_emission_factor_tco2_per_mwh_fuel")["value"],
        "emissions.fossil_gas_emission_factor_tco2_per_mwh_fuel",
    )
    _require_nonnegative(
        _parameter(data, "emissions", "biomethane_emission_factor_tco2_per_mwh_fuel")["value"],
        "emissions.biomethane_emission_factor_tco2_per_mwh_fuel",
    )


def _require_nonnegative(value: Any, name: str) -> float:
    number = _require_number(value, name)
    if number < 0:
        raise AssumptionError(f"{name} must be greater than or equal to 0; got {value}")
    return number


def _validate_booleans(data: dict) -> None:
    for section, key in BOOLEAN_PARAMETERS:
        value = _parameter(data, section, key)["value"]
        if not isinstance(value, bool):
            raise AssumptionError(
                f"{section}.{key} must be true or false; got {value!r}"
            )


def _require_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AssumptionError(f"{name} must be a number; got {value!r}")
    number = float(value)
    if not math.isfinite(number):
        raise AssumptionError(f"{name} must be a finite number; got {value!r}")
    return number


def _require_positive(value: Any, name: str) -> float:
    number = _require_number(value, name)
    if number <= 0:
        raise AssumptionError(f"{name} must be positive; got {value}")
    return number


def _require_efficiency(value: Any, name: str) -> float:
    number = _require_number(value, name)
    if not 0.0 < number <= 1.0:
        raise AssumptionError(
            f"{name} must be greater than 0 and at most 1; got {value}"
        )
    return number


def _require_unit_interval(value: Any, name: str) -> float:
    number = _require_number(value, name)
    if not 0.0 <= number <= 1.0:
        raise AssumptionError(
            f"{name} must be between 0 and 1 inclusive; got {value}"
        )
    return number


def _require_int_at_least(value: Any, name: str, minimum: int) -> int:
    number = _require_number(value, name)
    if number != int(number):
        raise AssumptionError(f"{name} must be an integer; got {value}")
    integer = int(number)
    if integer < minimum:
        raise AssumptionError(f"{name} must be >= {minimum}; got {value}")
    return integer


def _format_number(value: float) -> str:
    return f"{value:.6g}"
