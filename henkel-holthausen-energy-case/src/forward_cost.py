"""Forward fuel-cost scenarios for the electrode-boiler investment screen.

Hourly electricity prices stay the observed 2023, 2024, and 2025 day-ahead
shapes. Future uncertainty is the effective cost of fuel-fired steam. The
2026 Düsseldorf high-voltage tariff is held constant in real terms. These
paths are screening scenarios, not forecasts, and not Business Case 1.
"""

from __future__ import annotations

from typing import Any

from src.costs import NetworkTariff, scenario_blended_fuel_price
from src.investment import interpolate_anchor_series

FORWARD_CAPACITIES_MW = (5.0, 10.0, 20.0)
FORWARD_ANCHOR_YEARS = (2026, 2030, 2040)
FORWARD_SCENARIOS = ("low", "mid", "high")
PRIMARY_FORWARD_SCENARIO = "mid"

# Official 2026 Netzgesellschaft Düsseldorf high-voltage charges.
# Held in real terms for this screen only. grid_tariffs in assumptions.yaml
# remains the 2025 Business Case 1 proxy.
DUESSELDORF_HV_2026 = {
    "hv_high_utilization": NetworkTariff("hv_high_utilization", 2500.0, 4.90, 75.40),
    "hv_low_utilization": NetworkTariff("hv_low_utilization", 2500.0, 30.40, 11.87),
}


def apply_2026_hv_tariff(library: Any) -> None:
    """Point one screening library at the 2026 tariff before any solve."""
    library.high_tariff = DUESSELDORF_HV_2026["hv_high_utilization"]
    library.low_tariff = DUESSELDORF_HV_2026["hv_low_utilization"]


def effective_fossil_gas_cost(
    precarbon_gas_eur_mwh: float,
    eua_eur_t: float,
    emission_factor_tco2_per_mwh: float,
) -> float:
    """Delivered fossil gas plus combustion EUA. Biomethane is not included."""
    return float(precarbon_gas_eur_mwh) + float(emission_factor_tco2_per_mwh) * float(eua_eur_t)


def biomethane_cost(precarbon_gas_eur_mwh: float, premium_eur_mwh: float) -> float:
    """Pre-carbon gas plus the existing biomethane premium. No EUA on this fuel."""
    return float(precarbon_gas_eur_mwh) + float(premium_eur_mwh)


def mean_shape_benefit(benefits: list[float]) -> float:
    """Equally weighted mean across observed electricity-price shapes."""
    if not benefits:
        raise ValueError("Anchor benefit needs at least one price shape")
    return sum(float(value) for value in benefits) / len(benefits)


def interpolated_annual_benefit(
    anchor_benefit_eur: dict[int, float],
    years: list[int],
) -> dict[int, float]:
    """Linear gross benefit between optimized anchor years. No extra dispatch."""
    return interpolate_anchor_series(anchor_benefit_eur, years)


def displaced_thermal_asset(chp_steam_change_gwh: float, gas_boiler_change_gwh: float) -> str:
    """Name the asset that loses steam. A negative change is an increase."""
    chp_drop = max(float(chp_steam_change_gwh), 0.0)
    boiler_drop = max(float(gas_boiler_change_gwh), 0.0)
    total = chp_drop + boiler_drop
    if total <= 1e-9:
        return "neither"
    if chp_drop >= 0.75 * total:
        return "CHP steam"
    if boiler_drop >= 0.75 * total:
        return "gas-boiler steam"
    return "mixture"


def load_forward_paths(assumptions: Any) -> dict[str, dict[int, dict[str, float]]]:
    """Read the low, mid, and high gas and EUA anchors. They are not forecasts."""
    block = assumptions.section("electric_boiler")["forward_fossil_cost_paths"]
    paths: dict[str, dict[int, dict[str, float]]] = {}
    for name in FORWARD_SCENARIOS:
        if name not in block:
            raise ValueError(f"Missing forward fossil-cost path {name!r}")
        years: dict[int, dict[str, float]] = {}
        for year, point in block[name].items():
            years[int(year)] = {
                "precarbon_gas_eur_mwh": float(point["precarbon_gas_eur_mwh"]),
                "eua_eur_t": float(point["eua_eur_t"]),
            }
        paths[name] = years
    return paths


def fuel_stack(
    assumptions: Any,
    precarbon_gas_eur_mwh: float,
    eua_eur_t: float,
) -> dict[str, float]:
    """Effective fossil cost, biomethane cost, and the blended dispatch price."""
    factor = float(assumptions.value("emissions", "fossil_gas_emission_factor_tco2_per_mwh_fuel"))
    premium = float(assumptions.value("market", "biomethane_premium_eur_per_mwh"))
    fossil = effective_fossil_gas_cost(precarbon_gas_eur_mwh, eua_eur_t, factor)
    biomethane = biomethane_cost(precarbon_gas_eur_mwh, premium)
    blended = scenario_blended_fuel_price(
        assumptions,
        eua_eur_t,
        fossil_pre_carbon_eur_per_mwh=precarbon_gas_eur_mwh,
        biomethane_eur_per_mwh=biomethane,
    )
    return {
        "precarbon_gas_eur_mwh": float(precarbon_gas_eur_mwh),
        "eua_eur_t": float(eua_eur_t),
        "effective_fossil_gas_eur_mwh": fossil,
        "biomethane_eur_mwh": biomethane,
        "blended_fuel_eur_mwh": blended,
        "emission_factor": factor,
    }
