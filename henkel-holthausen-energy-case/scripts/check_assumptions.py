#!/usr/bin/env python3
"""Load assumptions.yaml, validate it, and print a short screening summary."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.assumptions import AssumptionError, load_assumptions


def main() -> int:
    try:
        assumptions = load_assumptions()
    except AssumptionError as exc:
        print(f"Assumptions check failed: {exc}", file=sys.stderr)
        return 1

    version = assumptions.section("metadata")["model_version"]
    print(f"Henkel Düsseldorf-Holthausen screening assumptions ({version})")
    print()
    _section("MODEL BASE CASE", _base_case_rows(assumptions))
    _section("HISTORICAL ANCHORS", _historical_rows(assumptions))
    _section("STOREN REFERENCE", _storen_rows(assumptions))
    _section("PLANT STRUCTURE SENSITIVITIES", _scenario_rows(assumptions))
    _section("CRITICAL TBD PARAMETERS", _tbd_rows(assumptions))
    return 0


def _base_case_rows(assumptions) -> list[tuple[str, str]]:
    coal = assumptions.value("fuel_mix", "coal_share")
    fossil_gas = assumptions.value("fuel_mix", "fossil_gas_share")
    biomethane = assumptions.value("fuel_mix", "biomethane_share")
    return [
        ("Annual steam/heat demand:", _gwh(assumptions.value("demand", "annual_steam_heat_gwh"))),
        ("Annual electricity demand:", _gwh(assumptions.value("demand", "annual_electricity_gwh"))),
        ("Grid import capacity:", _mw(assumptions.value("grid", "import_capacity_mw"))),
        ("Grid export capacity:", _mw(assumptions.value("grid", "export_capacity_mw"))),
        (
            "Fossil gas / biomethane split:",
            f"fossil gas {_pct(fossil_gas)} / biomethane {_pct(biomethane)} "
            f"(coal {_pct(coal)})",
        ),
        ("CHP total utilization:", f"{assumptions.value('chp', 'total_utilization_efficiency'):.2f}"),
        ("CHP power-to-heat ratio:", f"{assumptions.value('chp', 'power_to_heat_ratio'):.2f}"),
        ("CHP max heat capacity:", _thermal_mw(assumptions.value("chp", "max_heat_output_mw"))),
        ("Boiler efficiency:", f"{assumptions.value('boiler', 'thermal_efficiency'):.2f}"),
        ("Boiler max heat capacity:", _thermal_mw(assumptions.value("boiler", "max_heat_output_mw"))),
        ("Baseline CHP steam share:", f"{assumptions.value('chp', 'baseline_steam_share'):.2f}"),
    ]


def _historical_rows(assumptions) -> list[tuple[str, str]]:
    steam_min = assumptions.value("historical_reference", "steam_load_min_2012")
    steam_max = assumptions.value("historical_reference", "steam_load_max_2012")
    return [
        (
            "2012 steam production:",
            f"{_number(assumptions.value('historical_reference', 'steam_production_2012'))} t/a",
        ),
        (
            "2012 electricity generation:",
            _gwh(assumptions.value("historical_reference", "electricity_generation_2012")),
        ),
        (
            "2012 steam-load range:",
            f"{_number(steam_min)} to {_number(steam_max)} t/h",
        ),
        (
            "2016 electricity generation:",
            _gwh(assumptions.value("historical_reference", "electricity_generation_2016")),
        ),
        (
            "2016 steam production:",
            f"{_number(assumptions.value('historical_reference', 'steam_production_2016'))} t/a",
        ),
        (
            "2016 total utilization:",
            f"{assumptions.value('historical_reference', 'total_energy_utilization_2016'):.2f}",
        ),
        (
            "Fuel-input plausibility check:",
            f"{assumptions.value('historical_reference', 'fuel_input_plausibility_twh'):.2f} TWh/a "
            "(not a demand input)",
        ),
    ]


def _storen_rows(assumptions) -> list[tuple[str, str]]:
    return [
        (
            "2018 steam energy share:",
            _pct(assumptions.value("storen_reference", "steam_energy_share_2018")),
        ),
        (
            "2018 electricity energy share:",
            _pct(assumptions.value("storen_reference", "electricity_energy_share_2018")),
        ),
        (
            "2030 modeled steam peak:",
            _thermal_mw(assumptions.value("storen_reference", "steam_peak_2030_mw")),
        ),
        (
            "2030 modeled electricity peak:",
            _electric_mw(assumptions.value("storen_reference", "electricity_peak_2030_mw")),
        ),
    ]


def _scenario_rows(assumptions) -> list[tuple[str, str]]:
    scenarios = assumptions.section("plant_structure_scenarios")
    rows = []
    for name in ("boiler_heavy", "balanced", "chp_heavy"):
        scenario = scenarios[name]
        rows.append(
            (
                f"{name}:",
                "CHP "
                f"{_number(scenario['chp_max_heat_mw'])} MW_th, boiler "
                f"{_number(scenario['boiler_max_heat_mw'])} MW_th, total "
                f"{_number(scenario['total_heat_capacity_mw'])} MW_th",
            )
        )
    return rows


def _tbd_rows(assumptions) -> list[tuple[str, str]]:
    names = assumptions.critical_tbd()
    if not names:
        return [("None:", "")]
    return [(f"{name}:", "") for name in names]


def _section(title: str, rows: list[tuple[str, str]]) -> None:
    print(title)
    width = max(len(label) for label, _ in rows) + 2
    for label, value in rows:
        if value:
            print(f"{label:<{width}}{value}")
        else:
            print(f"- {label[:-1]}")
    print()


def _gwh(value: float) -> str:
    return f"{_number(value)} GWh/a"


def _mw(value: float | None) -> str:
    if value is None:
        return "TBD"
    return f"{_number(value)} MW"


def _thermal_mw(value: float | None) -> str:
    if value is None:
        return "TBD"
    return f"{_number(value)} MW_th"


def _electric_mw(value: float | None) -> str:
    if value is None:
        return "TBD"
    return f"{_number(value)} MW_el"


def _pct(value: float) -> str:
    percent = value * 100
    if _is_whole(percent):
        return f"{int(percent)}%"
    return f"{percent:.1f}%"


def _number(value: float) -> str:
    if _is_whole(value):
        return str(int(value))
    return f"{value:g}"


def _is_whole(value: float) -> bool:
    return float(value).is_integer()


if __name__ == "__main__":
    sys.exit(main())
