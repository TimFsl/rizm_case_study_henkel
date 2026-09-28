"""Hourly price-responsive CHP and boiler dispatch.

The baseline remains a calibrated rule-based reference. This module only
replaces the fixed hourly CHP steam share with a linear cost-minimizing
choice. Steam demand, electricity demand, efficiencies, and prices stay
exactly as in the costed baseline.

The result is a screening dispatch value relative to that reference. It is
not an estimate of realized Henkel savings. Actual operating rules,
unit-level constraints, and export capability are unknown.

The first optimization intentionally represents perfect hourly dispatch
flexibility. Ramp rates, minimum loads, startup costs, and unit commitment
are excluded because unit-level current operating constraints are not public.
`add_intertemporal_constraints` is the place to add ramp inequalities later
without rewriting the balances or the objective.

Two modes:

- full_flex: theoretical dispatch-flexibility upper bound. No annual CHP target.
- redispatch_only: timing value at the baseline's annual CHP electricity total.

A tie-breaker of 1e-6 EUR/MWh on gross grid throughput removes simultaneous
import and export when the two prices are equal. It is numerical only and is
excluded from the reported screening energy cost.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from typing import Any

import pandas as pd
import pyomo.environ as pyo

from src.costs import (
    NetworkTariff,
    cost_baseline,
    fuel_price_breakdown,
    network_cost_breakdown,
    network_demand_charge_eur,
    network_energy_cost_eur,
)

FULL_FLEX = "full_flex"
REDISPATCH_ONLY = "redispatch_only"
OPTIMIZATION_MODES = (FULL_FLEX, REDISPATCH_ONLY)

# Numerical only. About 1 EUR per TWh of gross grid flow, far below reporting precision.
GRID_TIE_BREAKER_EUR_PER_MWH = 1e-6

BALANCE_TOLERANCE_MW = 1e-4
ANNUAL_CHP_TOLERANCE_MWH = 1e-2
HOURLY_COST_TOLERANCE_EUR = 1e-4
ANNUAL_OBJECTIVE_TOLERANCE_EUR = 1.0
BOUND_TOLERANCE_MW = 0.05
PRICE_RESPONSE_GAP_EUR_PER_MWH = 1.0
NEAR_FEASIBLE_MAX_FRACTION = 0.99

DISPATCH_VALUE_BASIS = (
    "site-level screening dispatch value normalized by Henkel production"
)

OUTPUT_COLUMNS = (
    "steam_demand_mw",
    "electricity_demand_mw",
    "day_ahead_price_eur_mwh",
    "electricity_import_price_eur_mwh",
    "electricity_export_price_eur_mwh",
    "blended_fuel_price_eur_mwh",
    "chp_steam_mw",
    "boiler_steam_mw",
    "chp_electricity_mw",
    "chp_fuel_mwh",
    "boiler_fuel_mwh",
    "total_fuel_mwh",
    "grid_import_mw",
    "grid_export_mw",
    "net_grid_import_mw",
    "fuel_cost_eur",
    "grid_import_cost_eur",
    "grid_export_revenue_eur",
    "total_variable_energy_cost_eur",
)


class OptimizationError(ValueError):
    """Raised when the dispatch model is infeasible or the solution fails QA."""


class OptimizationWarning(UserWarning):
    """Raised when prices would reward pure grid arbitrage."""


@dataclass
class OptimizedDispatch:
    """Reported dispatch and the solver quantities needed for QA."""

    frame: pd.DataFrame
    mode: str
    termination_condition: str
    solver_objective_eur: float
    tie_breaker_cost_eur: float
    break_even_eur_mwh: float
    network_tariff_name: str | None = None
    network_energy_cost_eur: float = 0.0
    network_demand_charge_eur: float = 0.0
    grid_peak_import_mw: float | None = None
    chp_ramp_fraction_per_hour: float | None = None
    chp_ramp_mw_per_hour: float | None = None
    export_capacity_mw: float | None = None
    eboiler_capacity_mw: float = 0.0
    eboiler_efficiency: float | None = None


def calculate_chp_break_even_price(assumptions: Any) -> float:
    """Day-ahead price at which one more MWh of CHP steam matches boiler steam.

    The power credit uses the same EUR/MWh for avoided import and export.
    That is the screening case in which the import adder and export discount
    are equal. The price is derived from current fuel and efficiency assumptions.
    """
    fuel_price = float(fuel_price_breakdown(assumptions)["blended_fuel_cost_eur_mwh"])
    boiler_efficiency = float(_value(assumptions, "boiler", "thermal_efficiency"))
    chp_efficiency = float(_value(assumptions, "chp", "total_utilization_efficiency"))
    power_to_heat = float(_value(assumptions, "chp", "power_to_heat_ratio"))
    if power_to_heat <= 0:
        raise OptimizationError("CHP power-to-heat ratio must be positive")
    boiler_steam_cost = fuel_price / boiler_efficiency
    chp_steam_cost_before_power = fuel_price * (1.0 + power_to_heat) / chp_efficiency
    return (chp_steam_cost_before_power - boiler_steam_cost) / power_to_heat


def solve_dispatch(
    demand: pd.DataFrame,
    prices: pd.DataFrame,
    assumptions: Any,
    mode: str,
    baseline_chp_electricity_mwh: float | None = None,
    network_tariff: NetworkTariff | None = None,
    chp_ramp_fraction_per_hour: float | None = None,
    export_capacity_mw: float | None = None,
    eboiler_capacity_mw: float = 0.0,
    eboiler_efficiency: float | None = None,
    fuel_price_eur_per_mwh: float | None = None,
) -> OptimizedDispatch:
    """Build, solve, and validate one linear dispatch problem.

    `prices` must carry the commodity import price and the export price.
    A network tariff, when supplied, adds the energy charge and the annual
    peak charge. It does not let the solver switch tariff regimes.
    """
    if mode not in OPTIMIZATION_MODES:
        known = ", ".join(OPTIMIZATION_MODES)
        raise OptimizationError(f"Unknown optimization mode {mode!r}. Known modes: {known}")
    _require_aligned_inputs(demand, prices)
    if mode == REDISPATCH_ONLY and baseline_chp_electricity_mwh is None:
        raise OptimizationError(
            "redispatch_only requires the baseline annual CHP electricity in MWh"
        )

    steam = demand["steam_demand_mw"].astype(float)
    electricity = demand["electricity_demand_mw"].astype(float)
    import_price = prices["electricity_import_price_eur_mwh"].astype(float)
    export_price = prices["electricity_export_price_eur_mwh"].astype(float)
    _warn_if_arbitrage_prices(import_price, export_price)
    _raise_if_steam_is_infeasible(steam, assumptions)

    if fuel_price_eur_per_mwh is None:
        fuel_price = float(fuel_price_breakdown(assumptions)["blended_fuel_cost_eur_mwh"])
    else:
        fuel_price = float(fuel_price_eur_per_mwh)
    if eboiler_efficiency is not None:
        eboiler_eta = float(eboiler_efficiency)
    elif float(eboiler_capacity_mw) > 0.0:
        eboiler_eta = float(
            _value(assumptions, "electric_boiler", "electrical_to_heat_efficiency")
        )
    else:
        eboiler_eta = 0.99
    energy_charge = 0.0 if network_tariff is None else network_tariff.energy_charge_eur_per_mwh
    demand_charge = 0.0 if network_tariff is None else network_tariff.demand_charge_eur_per_kw_a
    chp_max = float(_value(assumptions, "chp", "max_heat_output_mw"))
    ramp_mw = _chp_ramp_mw_per_hour(chp_ramp_fraction_per_hour, chp_max)
    model = build_dispatch_model(
        steam=steam,
        electricity=electricity,
        import_price=import_price,
        export_price=export_price,
        fuel_price=fuel_price,
        assumptions=assumptions,
        mode=mode,
        baseline_chp_electricity_mwh=baseline_chp_electricity_mwh,
        network_energy_charge_eur_per_mwh=energy_charge,
        network_demand_charge_eur_per_kw_a=demand_charge,
        chp_ramp_mw_per_hour=ramp_mw,
        export_capacity_mw=_export_capacity_mw(export_capacity_mw),
        eboiler_capacity_mw=float(eboiler_capacity_mw),
        eboiler_efficiency=eboiler_eta,
    )
    return finalize_dispatch(
        model,
        demand=demand,
        prices=prices,
        assumptions=assumptions,
        mode=mode,
        baseline_chp_electricity_mwh=baseline_chp_electricity_mwh,
        network_tariff=network_tariff,
        chp_ramp_fraction_per_hour=chp_ramp_fraction_per_hour,
        ramp_mw=ramp_mw,
        export_capacity_mw=export_capacity_mw,
        eboiler_capacity_mw=float(eboiler_capacity_mw),
        eboiler_efficiency=eboiler_eta,
        fuel_price=fuel_price,
    )


def finalize_dispatch(
    model: pyo.ConcreteModel,
    demand: pd.DataFrame,
    prices: pd.DataFrame,
    assumptions: Any,
    mode: str,
    baseline_chp_electricity_mwh: float | None,
    network_tariff: NetworkTariff | None,
    chp_ramp_fraction_per_hour: float | None,
    ramp_mw: float | None,
    export_capacity_mw: float | None,
    eboiler_capacity_mw: float,
    eboiler_efficiency: float,
    fuel_price: float,
) -> OptimizedDispatch:
    """Solve one already-built model at a fuel price and return the screened result.

    The fuel price is a mutable parameter, so the same model can be re-solved
    for another carbon year without rebuilding the constraints.
    """
    model.fuel_price.set_value(float(fuel_price))
    steam = demand["steam_demand_mw"].astype(float)
    electricity = demand["electricity_demand_mw"].astype(float)
    import_price = prices["electricity_import_price_eur_mwh"].astype(float)
    export_price = prices["electricity_export_price_eur_mwh"].astype(float)
    termination, solver_objective = _solve(model)
    physical, tie_breaker_cost, peak_import_mw = extract_dispatch(
        model,
        steam,
        electricity,
        assumptions,
        import_price,
        export_price,
    )
    costed = cost_baseline(
        physical,
        prices,
        assumptions,
        blended_fuel_price_eur_mwh=fuel_price,
    )
    frame = _assemble_output(physical, costed, network_tariff)
    network_energy = 0.0
    network_demand = 0.0
    if network_tariff is not None:
        network_energy = network_energy_cost_eur(
            frame["grid_import_mw"], network_tariff.energy_charge_eur_per_mwh
        )
        if peak_import_mw is None:
            # A zero demand charge does not create a peak variable. The hourly
            # maximum is still reported, and the annual demand charge is zero.
            peak_import_mw = float(frame["grid_import_mw"].max())
            network_demand = 0.0
        else:
            network_demand = network_demand_charge_eur(
                peak_import_mw, network_tariff.demand_charge_eur_per_kw_a
            )
    break_even = calculate_chp_break_even_price(assumptions)
    result = OptimizedDispatch(
        frame=frame,
        mode=mode,
        termination_condition=termination,
        solver_objective_eur=solver_objective,
        tie_breaker_cost_eur=tie_breaker_cost,
        break_even_eur_mwh=break_even,
        network_tariff_name=None if network_tariff is None else network_tariff.name,
        network_energy_cost_eur=network_energy,
        network_demand_charge_eur=network_demand,
        grid_peak_import_mw=peak_import_mw,
        chp_ramp_fraction_per_hour=None
        if chp_ramp_fraction_per_hour is None
        else float(chp_ramp_fraction_per_hour),
        chp_ramp_mw_per_hour=ramp_mw,
        export_capacity_mw=_export_capacity_mw(export_capacity_mw),
        eboiler_capacity_mw=float(eboiler_capacity_mw),
        eboiler_efficiency=None if float(eboiler_capacity_mw) <= 0.0 else float(eboiler_efficiency),
    )
    # The simple day-ahead break-even test is for the unconstrained energy-only model.
    # A demand charge, a ramp, an export cap, or an e-boiler couples the hours.
    validate_optimized_dispatch(
        result,
        assumptions,
        baseline_chp_electricity_mwh=baseline_chp_electricity_mwh,
        check_price_response=(
            mode == FULL_FLEX
            and network_tariff is None
            and ramp_mw is None
            and result.export_capacity_mw is None
            and float(eboiler_capacity_mw) <= 0.0
        ),
    )
    return result


def build_dispatch_model(
    steam: pd.Series,
    electricity: pd.Series,
    import_price: pd.Series,
    export_price: pd.Series,
    fuel_price: float,
    assumptions: Any,
    mode: str,
    baseline_chp_electricity_mwh: float | None = None,
    network_energy_charge_eur_per_mwh: float = 0.0,
    network_demand_charge_eur_per_kw_a: float = 0.0,
    chp_ramp_mw_per_hour: float | None = None,
    export_capacity_mw: float | None = None,
    eboiler_capacity_mw: float = 0.0,
    eboiler_efficiency: float = 0.99,
) -> pyo.ConcreteModel:
    """Linear CHP/boiler/grid model. Each row is one hour, so MW sums are MWh.

    A positive demand charge adds one continuous annual peak variable.
    The tariff regime itself is not a decision.
    """
    chp_max = float(_value(assumptions, "chp", "max_heat_output_mw"))
    boiler_max = float(_value(assumptions, "boiler", "max_heat_output_mw"))
    power_to_heat = float(_value(assumptions, "chp", "power_to_heat_ratio"))
    chp_efficiency = float(_value(assumptions, "chp", "total_utilization_efficiency"))
    boiler_efficiency = float(_value(assumptions, "boiler", "thermal_efficiency"))
    import_capacity = float(_value(assumptions, "grid", "import_capacity_mw"))

    steam_mw = steam.to_numpy(dtype=float)
    electricity_mw = electricity.to_numpy(dtype=float)
    import_eur = import_price.to_numpy(dtype=float)
    export_eur = export_price.to_numpy(dtype=float)
    chp_fuel_per_mw_steam = (1.0 + power_to_heat) / chp_efficiency
    boiler_fuel_per_mw_steam = 1.0 / boiler_efficiency
    eboiler_mw = float(eboiler_capacity_mw)
    eboiler_eta = float(eboiler_efficiency)
    if eboiler_mw < 0 or eboiler_eta <= 0:
        raise OptimizationError("E-boiler capacity and efficiency must be non-negative, and efficiency positive")

    model = pyo.ConcreteModel(name=f"holthausen_dispatch_{mode}")
    model.T = pyo.RangeSet(0, len(steam_mw) - 1)
    model.fuel_price = pyo.Param(initialize=float(fuel_price), mutable=True)
    model.q_chp = pyo.Var(model.T, domain=pyo.NonNegativeReals)
    model.q_boiler = pyo.Var(model.T, domain=pyo.NonNegativeReals)
    model.grid_import = pyo.Var(model.T, domain=pyo.NonNegativeReals)
    model.grid_export = pyo.Var(model.T, domain=pyo.NonNegativeReals)
    if eboiler_mw > 0.0:
        model.q_eboiler = pyo.Var(model.T, domain=pyo.NonNegativeReals)
        model.eboiler_efficiency = eboiler_eta

    def steam_balance(m: pyo.ConcreteModel, t: int) -> pyo.Expression:
        steam = m.q_chp[t] + m.q_boiler[t]
        if eboiler_mw > 0.0:
            steam = steam + m.q_eboiler[t]
        return steam == steam_mw[t]

    def electricity_balance(m: pyo.ConcreteModel, t: int) -> pyo.Expression:
        chp_electricity = m.q_chp[t] * power_to_heat
        eboiler_electricity = 0.0
        if eboiler_mw > 0.0:
            eboiler_electricity = m.q_eboiler[t] / eboiler_eta
        return (
            chp_electricity + m.grid_import[t]
            == electricity_mw[t] + eboiler_electricity + m.grid_export[t]
        )

    def chp_capacity(m: pyo.ConcreteModel, t: int) -> pyo.Expression:
        return m.q_chp[t] <= chp_max

    def boiler_capacity(m: pyo.ConcreteModel, t: int) -> pyo.Expression:
        return m.q_boiler[t] <= boiler_max

    def import_capacity_limit(m: pyo.ConcreteModel, t: int) -> pyo.Expression:
        return m.grid_import[t] <= import_capacity

    model.steam_balance = pyo.Constraint(model.T, rule=steam_balance)
    model.electricity_balance = pyo.Constraint(model.T, rule=electricity_balance)
    model.chp_capacity = pyo.Constraint(model.T, rule=chp_capacity)
    model.boiler_capacity = pyo.Constraint(model.T, rule=boiler_capacity)
    model.import_capacity = pyo.Constraint(model.T, rule=import_capacity_limit)
    if eboiler_mw > 0.0:
        def eboiler_capacity_limit(m: pyo.ConcreteModel, t: int) -> pyo.Expression:
            return m.q_eboiler[t] <= eboiler_mw

        model.eboiler_capacity = pyo.Constraint(model.T, rule=eboiler_capacity_limit)
    if export_capacity_mw is not None:
        export_limit = float(export_capacity_mw)

        def export_capacity_limit(m: pyo.ConcreteModel, t: int) -> pyo.Expression:
            return m.grid_export[t] <= export_limit

        model.export_capacity = pyo.Constraint(model.T, rule=export_capacity_limit)
    add_intertemporal_constraints(model, chp_ramp_mw_per_hour)

    energy_charge = float(network_energy_charge_eur_per_mwh)
    demand_charge = float(network_demand_charge_eur_per_kw_a)
    if demand_charge > 0.0:
        model.grid_peak_import = pyo.Var(domain=pyo.NonNegativeReals)

        def peak_covers_hour(m: pyo.ConcreteModel, t: int) -> pyo.Expression:
            return m.grid_peak_import >= m.grid_import[t]

        model.peak_import_limit = pyo.Constraint(model.T, rule=peak_covers_hour)

    if mode == REDISPATCH_ONLY:
        target = float(baseline_chp_electricity_mwh)
        model.annual_chp_electricity = pyo.Constraint(
            expr=pyo.quicksum(model.q_chp[t] * power_to_heat for t in model.T) == target
        )

    def objective(m: pyo.ConcreteModel) -> pyo.Expression:
        # Tie-breaker cost is not part of the reported screening energy cost.
        # Network energy is EUR/MWh times imported MW over a one-hour step.
        hourly = pyo.quicksum(
            m.q_chp[t] * chp_fuel_per_mw_steam * m.fuel_price
            + m.q_boiler[t] * boiler_fuel_per_mw_steam * m.fuel_price
            + m.grid_import[t]
            * (import_eur[t] + energy_charge + GRID_TIE_BREAKER_EUR_PER_MWH)
            - m.grid_export[t] * export_eur[t]
            + m.grid_export[t] * GRID_TIE_BREAKER_EUR_PER_MWH
            for t in m.T
        )
        if demand_charge > 0.0:
            return hourly + m.grid_peak_import * 1000.0 * demand_charge
        return hourly

    model.objective = pyo.Objective(rule=objective, sense=pyo.minimize)
    model.power_to_heat = power_to_heat
    return model


def add_intertemporal_constraints(
    model: pyo.ConcreteModel,
    chp_ramp_mw_per_hour: float | None = None,
) -> None:
    """Limit the hour-to-hour change in aggregated CHP heat.

    The first hour is unconstrained. No previous-year plant state is invented.
    The aggregated boiler has no ramp constraint: at this hourly resolution it
    is the fast steam-balancing asset. Unit-level boiler ramp and start limits
    are unknown. That is not a claim of infinite physical boiler flexibility.
    No startup binary and no minimum CHP output are added.
    """
    if chp_ramp_mw_per_hour is None:
        return
    limit = float(chp_ramp_mw_per_hour)

    def ramp_up(m: pyo.ConcreteModel, t: int) -> pyo.Expression:
        if t == m.T.first():
            return pyo.Constraint.Skip
        return m.q_chp[t] - m.q_chp[t - 1] <= limit

    def ramp_down(m: pyo.ConcreteModel, t: int) -> pyo.Expression:
        if t == m.T.first():
            return pyo.Constraint.Skip
        return m.q_chp[t - 1] - m.q_chp[t] <= limit

    model.chp_ramp_up = pyo.Constraint(model.T, rule=ramp_up)
    model.chp_ramp_down = pyo.Constraint(model.T, rule=ramp_down)
    model.chp_ramp_mw_per_hour = limit


def extract_dispatch(
    model: pyo.ConcreteModel,
    steam: pd.Series,
    electricity: pd.Series,
    assumptions: Any,
    import_price: pd.Series,
    export_price: pd.Series,
) -> tuple[pd.DataFrame, float, float | None]:
    """Read the solver solution and apply the fuel definitions used in the baseline."""
    index = steam.index
    q_chp = _series_from_var(model.q_chp, index)
    q_boiler = _series_from_var(model.q_boiler, index)
    grid_import = _series_from_var(model.grid_import, index)
    grid_export = _series_from_var(model.grid_export, index)
    tie_breaker_cost = float(
        GRID_TIE_BREAKER_EUR_PER_MWH * (grid_import + grid_export).sum()
    )
    grid_import, grid_export = _net_simultaneous_grid_flows(
        grid_import,
        grid_export,
        import_price,
        export_price,
    )

    power_to_heat = float(_value(assumptions, "chp", "power_to_heat_ratio"))
    chp_efficiency = float(_value(assumptions, "chp", "total_utilization_efficiency"))
    boiler_efficiency = float(_value(assumptions, "boiler", "thermal_efficiency"))
    chp_electricity = q_chp * power_to_heat
    chp_fuel = (q_chp + chp_electricity) / chp_efficiency
    boiler_fuel = q_boiler / boiler_efficiency

    physical = pd.DataFrame(
        {
            "steam_demand_mw": steam.astype(float),
            "electricity_demand_mw": electricity.astype(float),
            "chp_steam_mw": q_chp,
            "boiler_steam_mw": q_boiler,
            "chp_electricity_mw": chp_electricity,
            "chp_fuel_mwh": chp_fuel,
            "boiler_fuel_mwh": boiler_fuel,
            "grid_import_mw": grid_import,
            "grid_export_mw": grid_export,
        },
        index=index,
    )
    if hasattr(model, "q_eboiler"):
        eboiler_steam = _series_from_var(model.q_eboiler, index)
        physical["eboiler_steam_mw"] = eboiler_steam
        physical["eboiler_electricity_mw"] = eboiler_steam / float(model.eboiler_efficiency)
    physical.index.name = index.name or "timestamp"
    peak_import_mw = None
    if hasattr(model, "grid_peak_import"):
        peak_import_mw = float(pyo.value(model.grid_peak_import))
    return physical, tie_breaker_cost, peak_import_mw


def validate_optimized_dispatch(
    result: OptimizedDispatch,
    assumptions: Any,
    baseline_chp_electricity_mwh: float | None = None,
    baseline_variable_cost_eur: float | None = None,
    baseline_screening_cost_eur: float | None = None,
    check_price_response: bool = False,
) -> None:
    """Raise OptimizationError when a reported dispatch violates the screening QA rules."""
    frame = result.frame
    if result.termination_condition != str(pyo.TerminationCondition.optimal):
        raise OptimizationError(
            f"Solver termination was {result.termination_condition}, not optimal"
        )
    if frame.isna().any().any():
        raise OptimizationError("Optimized dispatch contains NaN")

    chp_max = float(_value(assumptions, "chp", "max_heat_output_mw"))
    boiler_max = float(_value(assumptions, "boiler", "max_heat_output_mw"))
    import_capacity = float(_value(assumptions, "grid", "import_capacity_mw"))
    power_to_heat = float(_value(assumptions, "chp", "power_to_heat_ratio"))
    chp_efficiency = float(_value(assumptions, "chp", "total_utilization_efficiency"))
    boiler_efficiency = float(_value(assumptions, "boiler", "thermal_efficiency"))

    eboiler_steam = (
        frame["eboiler_steam_mw"] if "eboiler_steam_mw" in frame.columns else 0.0
    )
    eboiler_electricity = (
        frame["eboiler_electricity_mw"] if "eboiler_electricity_mw" in frame.columns else 0.0
    )
    _close(
        frame["chp_steam_mw"] + frame["boiler_steam_mw"] + eboiler_steam,
        frame["steam_demand_mw"],
        "CHP steam plus boiler steam plus e-boiler steam must equal steam demand",
    )
    _close(
        frame["chp_electricity_mw"]
        + frame["grid_import_mw"]
        - frame["grid_export_mw"]
        - eboiler_electricity,
        frame["electricity_demand_mw"],
        "CHP electricity plus grid import must equal site demand plus e-boiler electricity plus export",
    )
    _close(
        frame["chp_steam_mw"] * power_to_heat,
        frame["chp_electricity_mw"],
        "CHP electricity must equal steam times the power-to-heat ratio",
    )
    _close(
        (frame["chp_steam_mw"] + frame["chp_electricity_mw"]) / chp_efficiency,
        frame["chp_fuel_mwh"],
        "CHP fuel must equal useful CHP output divided by total utilization efficiency",
    )
    _close(
        frame["boiler_steam_mw"] / boiler_efficiency,
        frame["boiler_fuel_mwh"],
        "Boiler fuel must equal boiler steam divided by boiler efficiency",
    )
    _close(
        frame["chp_fuel_mwh"] + frame["boiler_fuel_mwh"],
        frame["total_fuel_mwh"],
        "Total fuel must equal CHP fuel plus boiler fuel",
    )
    _close(
        frame["grid_import_mw"] - frame["grid_export_mw"],
        frame["net_grid_import_mw"],
        "Net grid import must equal import minus export",
    )

    for column in (
        "chp_steam_mw",
        "boiler_steam_mw",
        "chp_electricity_mw",
        "chp_fuel_mwh",
        "boiler_fuel_mwh",
        "total_fuel_mwh",
        "grid_import_mw",
        "grid_export_mw",
    ):
        if (frame[column] < -BALANCE_TOLERANCE_MW).any():
            raise OptimizationError(f"{column} contains a negative flow")
    if (frame["chp_steam_mw"] > chp_max + BALANCE_TOLERANCE_MW).any():
        raise OptimizationError("CHP steam exceeds configured heat capacity")
    if (frame["boiler_steam_mw"] > boiler_max + BALANCE_TOLERANCE_MW).any():
        raise OptimizationError("Boiler steam exceeds configured heat capacity")
    if (frame["grid_import_mw"] > import_capacity + BALANCE_TOLERANCE_MW).any():
        raise OptimizationError("Grid import exceeds configured import capacity")
    if result.eboiler_capacity_mw > 0.0:
        if "eboiler_steam_mw" not in frame.columns:
            raise OptimizationError("E-boiler dispatch is missing from the reported frame")
        capacity = float(result.eboiler_capacity_mw)
        if (frame["eboiler_steam_mw"] > capacity + BALANCE_TOLERANCE_MW).any():
            raise OptimizationError("E-boiler steam exceeds installed thermal capacity")
        if (frame["eboiler_steam_mw"] < -BALANCE_TOLERANCE_MW).any():
            raise OptimizationError("E-boiler steam is negative")
        eta = result.eboiler_efficiency
        if eta is None or eta <= 0.0:
            raise OptimizationError("E-boiler efficiency is missing")
        _close(
            frame["eboiler_steam_mw"] / float(eta),
            frame["eboiler_electricity_mw"],
            "E-boiler electricity must equal steam divided by electrical-to-heat efficiency",
        )
    simultaneous = (frame["grid_import_mw"] > BALANCE_TOLERANCE_MW) & (
        frame["grid_export_mw"] > BALANCE_TOLERANCE_MW
    )
    if simultaneous.any():
        raise OptimizationError("Grid import and export are simultaneously positive")

    reconstructed = (
        frame["fuel_cost_eur"]
        + frame["grid_import_cost_eur"]
        - frame["grid_export_revenue_eur"]
    )
    _close(
        reconstructed,
        frame["total_variable_energy_cost_eur"],
        "Reported variable cost must equal fuel cost plus import cost minus export revenue",
        HOURLY_COST_TOLERANCE_EUR,
    )
    reported_cost = float(frame["total_variable_energy_cost_eur"].sum())
    screening_cost = (
        reported_cost + result.network_energy_cost_eur + result.network_demand_charge_eur
    )
    objective_without_tie_breaker = result.solver_objective_eur - result.tie_breaker_cost_eur
    if abs(objective_without_tie_breaker - screening_cost) > ANNUAL_OBJECTIVE_TOLERANCE_EUR:
        raise OptimizationError(
            "Reported screening cost does not match the solver objective "
            f"excluding the {GRID_TIE_BREAKER_EUR_PER_MWH:g} EUR/MWh grid tie-breaker: "
            f"solver {objective_without_tie_breaker:.6f} EUR, "
            f"reported {screening_cost:.6f} EUR"
        )
    if result.grid_peak_import_mw is not None:
        peak = float(result.grid_peak_import_mw)
        hourly_peak = float(frame["grid_import_mw"].max())
        if (frame["grid_import_mw"] > peak + BALANCE_TOLERANCE_MW).any():
            raise OptimizationError("grid_peak_import_mw is below an hourly import")
        if abs(peak - hourly_peak) > BALANCE_TOLERANCE_MW:
            raise OptimizationError(
                "Solved grid peak does not equal the maximum hourly import: "
                f"peak variable {peak:.6f} MW, hourly maximum {hourly_peak:.6f} MW"
            )
    if result.chp_ramp_mw_per_hour is not None:
        delta = frame["chp_steam_mw"].diff().iloc[1:]
        limit = float(result.chp_ramp_mw_per_hour)
        if (delta.abs() > limit + BALANCE_TOLERANCE_MW).any():
            worst = float(delta.abs().max())
            raise OptimizationError(
                "CHP heat changes faster than the screening ramp: "
                f"maximum step {worst:.4f} MW, limit {limit:.4f} MW per hour"
            )
    if result.export_capacity_mw is not None:
        export_limit = float(result.export_capacity_mw)
        if (frame["grid_export_mw"] > export_limit + BALANCE_TOLERANCE_MW).any():
            worst = float(frame["grid_export_mw"].max())
            raise OptimizationError(
                "Grid export exceeds the screening export capacity: "
                f"maximum {worst:.4f} MW, limit {export_limit:.4f} MW"
            )
    if baseline_screening_cost_eur is not None and screening_cost > (
        float(baseline_screening_cost_eur) + ANNUAL_OBJECTIVE_TOLERANCE_EUR
    ):
        raise OptimizationError(
            "Optimized screening cost exceeds the calibrated reference cost"
        )
    elif (
        result.network_tariff_name is None
        and baseline_variable_cost_eur is not None
        and reported_cost
        > float(baseline_variable_cost_eur) + ANNUAL_OBJECTIVE_TOLERANCE_EUR
    ):
        raise OptimizationError(
            "Optimized screening cost exceeds the calibrated reference cost"
        )
    if result.mode == REDISPATCH_ONLY:
        if baseline_chp_electricity_mwh is None:
            raise OptimizationError("redispatch_only validation is missing the CHP target")
        optimized_mwh = float(frame["chp_electricity_mw"].sum())
        if abs(optimized_mwh - float(baseline_chp_electricity_mwh)) > ANNUAL_CHP_TOLERANCE_MWH:
            raise OptimizationError(
                "redispatch_only CHP electricity differs from the baseline total: "
                f"optimized {optimized_mwh:.6f} MWh, "
                f"baseline {float(baseline_chp_electricity_mwh):.6f} MWh"
            )
    if check_price_response:
        _raise_if_price_response_misses_the_bound(frame, assumptions, result.break_even_eur_mwh)


def summarize_optimization(
    scenario: str,
    result: OptimizedDispatch,
    baseline: pd.DataFrame,
    baseline_variable_cost_eur: float,
    assumptions: Any,
) -> dict[str, float | str]:
    """Annual comparison of one optimized dispatch with its rule-based reference."""
    frame = result.frame
    optimized_cost = float(frame["total_variable_energy_cost_eur"].sum())
    dispatch_value = float(baseline_variable_cost_eur) - optimized_cost
    production_tonnes = float(_value(assumptions, "site", "annual_henkel_production_tonnes"))
    chp_max = float(_value(assumptions, "chp", "max_heat_output_mw"))
    boiler_max = float(_value(assumptions, "boiler", "max_heat_output_mw"))
    q_max = pd.concat(
        [frame["steam_demand_mw"], pd.Series(chp_max, index=frame.index)],
        axis=1,
    ).min(axis=1)
    q_min = (frame["steam_demand_mw"] - boiler_max).clip(lower=0.0)
    day_ahead = frame["day_ahead_price_eur_mwh"]
    break_even = result.break_even_eur_mwh
    baseline_cost_m = float(baseline_variable_cost_eur) / 1_000_000.0
    optimized_cost_m = optimized_cost / 1_000_000.0
    return {
        "scenario": scenario,
        "optimization_mode": result.mode,
        "baseline_variable_cost_m_eur": baseline_cost_m,
        "optimized_variable_cost_m_eur": optimized_cost_m,
        "dispatch_value_m_eur": dispatch_value / 1_000_000.0,
        "dispatch_value_eur_per_t_henkel_production": dispatch_value / production_tonnes,
        "dispatch_value_percent_of_baseline": 100.0 * dispatch_value / float(baseline_variable_cost_eur),
        "baseline_chp_steam_gwh": _gwh(baseline["chp_steam_mw"]),
        "optimized_chp_steam_gwh": _gwh(frame["chp_steam_mw"]),
        "baseline_boiler_steam_gwh": _gwh(baseline["boiler_steam_mw"]),
        "optimized_boiler_steam_gwh": _gwh(frame["boiler_steam_mw"]),
        "baseline_chp_electricity_gwh": _gwh(baseline["chp_electricity_mw"]),
        "optimized_chp_electricity_gwh": _gwh(frame["chp_electricity_mw"]),
        "baseline_total_fuel_gwh": _gwh(baseline["total_fuel_mwh"]),
        "optimized_total_fuel_gwh": _gwh(frame["total_fuel_mwh"]),
        "baseline_grid_import_gwh": _gwh(baseline["grid_import_mw"]),
        "optimized_grid_import_gwh": _gwh(frame["grid_import_mw"]),
        "baseline_grid_export_gwh": _gwh(baseline["grid_export_mw"]),
        "optimized_grid_export_gwh": _gwh(frame["grid_export_mw"]),
        "baseline_net_grid_import_gwh": _gwh(baseline["net_grid_import_mw"]),
        "optimized_net_grid_import_gwh": _gwh(frame["net_grid_import_mw"]),
        "optimized_peak_grid_import_mw": float(frame["grid_import_mw"].max()),
        "optimized_peak_grid_export_mw": float(frame["grid_export_mw"].max()),
        "optimized_peak_chp_electricity_mw": float(frame["chp_electricity_mw"].max()),
        "hours_at_chp_max_or_near_max": float(
            (frame["chp_steam_mw"] >= NEAR_FEASIBLE_MAX_FRACTION * q_max - BOUND_TOLERANCE_MW).sum()
        ),
        "hours_at_minimum_feasible_chp": float(
            (frame["chp_steam_mw"] <= q_min + BOUND_TOLERANCE_MW).sum()
        ),
        "electricity_break_even_eur_mwh": break_even,
        "hours_price_below_break_even": float((day_ahead < break_even).sum()),
        "hours_price_above_break_even": float((day_ahead > break_even).sum()),
        "dispatch_value_basis": DISPATCH_VALUE_BASIS,
    }


def summarize_network_tariff(
    scenario: str,
    result: OptimizedDispatch,
    baseline: pd.DataFrame,
    baseline_variable_cost_eur: float,
    tariff: NetworkTariff,
    assumptions: Any,
) -> dict[str, float | str | bool | None]:
    """Dispatch value after the same tariff is applied to baseline and optimum.

    The baseline variable cost must be the commodity-and-fuel cost only.
    Network charges are added here and are not already inside that cost.
    """
    baseline_network = network_cost_breakdown(baseline["grid_import_mw"], tariff)
    optimized_network = network_cost_breakdown(result.frame["grid_import_mw"], tariff)
    if result.grid_peak_import_mw is None:
        raise OptimizationError("A tariff summary requires grid_peak_import_mw")
    if abs(float(result.grid_peak_import_mw) - float(optimized_network["peak_mw"])) > BALANCE_TOLERANCE_MW:
        raise OptimizationError("Tariff summary peak does not match the solved peak variable")
    if abs(result.network_energy_cost_eur - float(optimized_network["energy_eur"])) > ANNUAL_OBJECTIVE_TOLERANCE_EUR:
        raise OptimizationError("Tariff summary energy charge does not match the solved charge")
    if abs(result.network_demand_charge_eur - float(optimized_network["demand_eur"])) > ANNUAL_OBJECTIVE_TOLERANCE_EUR:
        raise OptimizationError("Tariff summary demand charge does not match the solved charge")

    baseline_screening = float(baseline_variable_cost_eur) + float(baseline_network["total_eur"])
    optimized_variable = float(result.frame["total_variable_energy_cost_eur"].sum())
    optimized_screening = optimized_variable + float(optimized_network["total_eur"])
    dispatch_value = baseline_screening - optimized_screening
    production_tonnes = float(_value(assumptions, "site", "annual_henkel_production_tonnes"))
    frame = result.frame
    return {
        "scenario": scenario,
        "optimization_mode": result.mode,
        "tariff_scenario": tariff.name,
        "network_energy_charge_eur_mwh": tariff.energy_charge_eur_per_mwh,
        "network_demand_charge_eur_kw_a": tariff.demand_charge_eur_per_kw_a,
        "baseline_grid_import_gwh": float(baseline_network["import_mwh"]) / 1000.0,
        "optimized_grid_import_gwh": float(optimized_network["import_mwh"]) / 1000.0,
        "baseline_peak_grid_import_mw": float(baseline_network["peak_mw"]),
        "optimized_peak_grid_import_mw": float(optimized_network["peak_mw"]),
        "baseline_utilization_hours": baseline_network["utilization_hours"],
        "optimized_utilization_hours": optimized_network["utilization_hours"],
        "tariff_regime_consistent": bool(optimized_network["consistent"]),
        "baseline_network_energy_cost_m_eur": float(baseline_network["energy_eur"]) / 1_000_000.0,
        "baseline_network_demand_charge_m_eur": float(baseline_network["demand_eur"]) / 1_000_000.0,
        "baseline_total_network_cost_m_eur": float(baseline_network["total_eur"]) / 1_000_000.0,
        "optimized_network_energy_cost_m_eur": float(optimized_network["energy_eur"]) / 1_000_000.0,
        "optimized_network_demand_charge_m_eur": float(optimized_network["demand_eur"]) / 1_000_000.0,
        "optimized_total_network_cost_m_eur": float(optimized_network["total_eur"]) / 1_000_000.0,
        "baseline_total_screening_cost_m_eur": baseline_screening / 1_000_000.0,
        "optimized_total_screening_cost_m_eur": optimized_screening / 1_000_000.0,
        "dispatch_value_m_eur": dispatch_value / 1_000_000.0,
        "dispatch_value_eur_per_t_henkel_production": dispatch_value / production_tonnes,
        "dispatch_value_percent": 100.0 * dispatch_value / baseline_screening,
        "optimized_chp_electricity_gwh": _gwh(frame["chp_electricity_mw"]),
        "optimized_chp_steam_gwh": _gwh(frame["chp_steam_mw"]),
        "optimized_boiler_steam_gwh": _gwh(frame["boiler_steam_mw"]),
        "optimized_total_fuel_gwh": _gwh(frame["total_fuel_mwh"]),
        "optimized_grid_export_gwh": _gwh(frame["grid_export_mw"]),
        "optimized_peak_grid_export_mw": float(frame["grid_export_mw"].max()),
    }


def add_intertemporal_constraints_note() -> str:
    """Text used by the unconstrained energy-only runner."""
    return (
        "The stored energy-only dispatch does not apply a CHP ramp. "
        "The final base-profile sensitivity limits the hour-to-hour CHP heat change. "
        "Minimum loads, startup costs, part-load efficiency curves, and unit commitment "
        "remain excluded because those unit-level data are not public."
    )


def _export_capacity_mw(export_capacity_mw: float | None) -> float | None:
    """None leaves export unconstrained. A number is the hourly MW limit."""
    if export_capacity_mw is None:
        return None
    limit = float(export_capacity_mw)
    if limit <= 0.0:
        raise OptimizationError(
            f"Export capacity must be positive when constrained; got {limit:g} MW"
        )
    return limit


def _chp_ramp_mw_per_hour(fraction: float | None, chp_max_mw: float) -> float | None:
    if fraction is None:
        return None
    rate = float(fraction)
    if not 0.0 < rate <= 1.0:
        raise OptimizationError(
            "CHP ramp fraction must be greater than 0 and at most 1 "
            f"of heat capacity per hour; got {rate:g}"
        )
    return rate * float(chp_max_mw)


def chp_ramp_diagnostics(
    chp_steam_mw: pd.Series,
    ramp_mw_per_hour: float,
    tolerance_mw: float = BOUND_TOLERANCE_MW,
) -> dict[str, float]:
    """Maximum step and the hours in which the up or down ramp limit is active."""
    delta = chp_steam_mw.astype(float).diff().iloc[1:]
    limit = float(ramp_mw_per_hour)
    return {
        "max_observed_chp_ramp_mw": float(delta.abs().max()) if len(delta) else 0.0,
        "number_of_hours_chp_ramp_up_binding": float((delta >= limit - tolerance_mw).sum()),
        "number_of_hours_chp_ramp_down_binding": float(((-delta) >= limit - tolerance_mw).sum()),
    }


def chp_bound_hour_counts(frame: pd.DataFrame, assumptions: Any) -> dict[str, float]:
    """Hours at the feasible CHP heat bounds, given steam, boiler, and import limits."""
    chp_max = float(_value(assumptions, "chp", "max_heat_output_mw"))
    boiler_max = float(_value(assumptions, "boiler", "max_heat_output_mw"))
    import_capacity = float(_value(assumptions, "grid", "import_capacity_mw"))
    power_to_heat = float(_value(assumptions, "chp", "power_to_heat_ratio"))
    q_max = pd.concat(
        [frame["steam_demand_mw"], pd.Series(chp_max, index=frame.index)],
        axis=1,
    ).min(axis=1)
    q_min_steam = (frame["steam_demand_mw"] - boiler_max).clip(lower=0.0)
    q_min_import = (
        (frame["electricity_demand_mw"] - import_capacity) / power_to_heat
    ).clip(lower=0.0)
    q_min = pd.concat([q_min_steam, q_min_import], axis=1).max(axis=1)
    q_min = pd.concat([q_min, q_max], axis=1).min(axis=1)
    return {
        "number_of_hours_chp_at_or_near_max": float(
            (frame["chp_steam_mw"] >= NEAR_FEASIBLE_MAX_FRACTION * q_max - BOUND_TOLERANCE_MW).sum()
        ),
        "number_of_hours_chp_at_or_near_minimum_feasible": float(
            (frame["chp_steam_mw"] <= q_min + BOUND_TOLERANCE_MW).sum()
        ),
    }


PRIMARY_SCREENING_MODE = REDISPATCH_ONLY
PRIMARY_SCREENING_RAMP_FRACTION = 0.50
UPPER_BOUND_MODE = FULL_FLEX


def assign_screening_roles(rows: list[dict]) -> None:
    """Mark the primary and upper-bound rows from solved consistency, not a forced tariff.

    When the solve under the baseline regime is itself consistent, that row is selected.
    Otherwise the other internally consistent solve is selected.
    """
    for row in rows:
        row["screening_role"] = ""
    _assign_role(rows, PRIMARY_SCREENING_MODE, PRIMARY_SCREENING_RAMP_FRACTION, "primary_screening")
    _assign_role(rows, UPPER_BOUND_MODE, PRIMARY_SCREENING_RAMP_FRACTION, "upper_bound")


def _assign_role(rows: list[dict], mode: str, ramp_fraction: float, role: str) -> None:
    candidates = [
        row
        for row in rows
        if row.get("optimization_mode") == mode
        and abs(float(row.get("chp_ramp_fraction_per_hour", -1)) - ramp_fraction) < 1e-9
        and bool(row.get("optimized_tariff_consistent"))
    ]
    if not candidates:
        return
    baseline_regime = candidates[0].get("baseline_tariff_regime")
    same_regime = [
        row for row in candidates if row.get("optimized_tariff_scenario") == baseline_regime
    ]
    chosen = same_regime[0] if same_regime else candidates[0]
    chosen["screening_role"] = role


_HIGHS_SOLVER = None


def _solve(model: pyo.ConcreteModel) -> tuple[str, float]:
    global _HIGHS_SOLVER
    if _HIGHS_SOLVER is None:
        _HIGHS_SOLVER = pyo.SolverFactory("appsi_highs")
    solver = _HIGHS_SOLVER
    if not solver.available(exception_flag=False):
        raise OptimizationError(
            "HiGHS is not available through Pyomo. Install pyomo and highspy."
        )
    results = solver.solve(model, tee=False)
    termination = results.solver.termination_condition
    if termination != pyo.TerminationCondition.optimal:
        raise OptimizationError(f"HiGHS did not return an optimal solution: {termination}")
    return str(termination), float(pyo.value(model.objective))


def _assemble_output(
    physical: pd.DataFrame,
    costed: pd.DataFrame,
    network_tariff: NetworkTariff | None = None,
) -> pd.DataFrame:
    frame = physical.copy()
    for column in (
        "day_ahead_price_eur_mwh",
        "electricity_import_price_eur_mwh",
        "electricity_export_price_eur_mwh",
        "blended_fuel_price_eur_mwh",
        "grid_import_cost_eur",
        "grid_export_revenue_eur",
        "total_variable_energy_cost_eur",
    ):
        frame[column] = costed[column]
    frame["total_fuel_mwh"] = frame["chp_fuel_mwh"] + frame["boiler_fuel_mwh"]
    frame["net_grid_import_mw"] = frame["grid_import_mw"] - frame["grid_export_mw"]
    frame["fuel_cost_eur"] = costed["chp_fuel_cost_eur"] + costed["boiler_fuel_cost_eur"]
    columns = list(OUTPUT_COLUMNS)
    if network_tariff is not None:
        # One-hour steps: imported MW times EUR/MWh is the hourly energy charge.
        # The annual demand charge stays in the summary, not in these rows.
        frame["network_energy_charge_eur_per_mwh"] = network_tariff.energy_charge_eur_per_mwh
        frame["network_energy_cost_eur"] = (
            frame["grid_import_mw"] * network_tariff.energy_charge_eur_per_mwh
        )
        columns.extend(
            ["network_energy_charge_eur_per_mwh", "network_energy_cost_eur"]
        )
    if "eboiler_steam_mw" in frame.columns:
        columns.extend(["eboiler_steam_mw", "eboiler_electricity_mw"])
    return frame.loc[:, columns]


def _raise_if_price_response_misses_the_bound(
    frame: pd.DataFrame,
    assumptions: Any,
    break_even: float,
) -> None:
    """Away from the break-even price, full_flex should sit on a feasible CHP bound."""
    chp_max = float(_value(assumptions, "chp", "max_heat_output_mw"))
    boiler_max = float(_value(assumptions, "boiler", "max_heat_output_mw"))
    import_capacity = float(_value(assumptions, "grid", "import_capacity_mw"))
    power_to_heat = float(_value(assumptions, "chp", "power_to_heat_ratio"))
    q_max = pd.concat(
        [frame["steam_demand_mw"], pd.Series(chp_max, index=frame.index)],
        axis=1,
    ).min(axis=1)
    q_min_steam = (frame["steam_demand_mw"] - boiler_max).clip(lower=0.0)
    q_min_import = (
        (frame["electricity_demand_mw"] - import_capacity) / power_to_heat
    ).clip(lower=0.0)
    q_min = pd.concat([q_min_steam, q_min_import], axis=1).max(axis=1)
    gap = frame["day_ahead_price_eur_mwh"] - break_even
    low = gap < -PRICE_RESPONSE_GAP_EUR_PER_MWH
    high = gap > PRICE_RESPONSE_GAP_EUR_PER_MWH
    low_miss = low & (frame["chp_steam_mw"] - q_min).abs() > BOUND_TOLERANCE_MW
    high_miss = high & (frame["chp_steam_mw"] - q_max).abs() > BOUND_TOLERANCE_MW
    if low_miss.any() or high_miss.any():
        timestamp = frame.index[low_miss | high_miss][0]
        raise OptimizationError(
            "full_flex CHP output is away from the feasible price-response bound at "
            f"{timestamp}. Break-even is {break_even:.3f} EUR/MWh. "
            "Hours within "
            f"{PRICE_RESPONSE_GAP_EUR_PER_MWH:.0f} EUR/MWh of the break-even are not tested, "
            "because several CHP shares can be economically equivalent there."
        )


def _net_simultaneous_grid_flows(
    grid_import: pd.Series,
    grid_export: pd.Series,
    import_price: pd.Series,
    export_price: pd.Series,
) -> tuple[pd.Series, pd.Series]:
    """Drop costless circulation. Do not remove flows when export outpays import."""
    if (import_price + 1e-9 < export_price).any():
        return grid_import, grid_export
    net = grid_import - grid_export
    return net.clip(lower=0.0), (-net).clip(lower=0.0)


def _warn_if_arbitrage_prices(import_price: pd.Series, export_price: pd.Series) -> None:
    if (import_price + 1e-9 < export_price).any():
        warnings.warn(
            "Electricity import price is below the export price in at least one hour. "
            "The screening model is not intended to perform pure grid arbitrage.",
            OptimizationWarning,
            stacklevel=2,
        )


def _raise_if_steam_is_infeasible(steam: pd.Series, assumptions: Any) -> None:
    chp_max = float(_value(assumptions, "chp", "max_heat_output_mw"))
    boiler_max = float(_value(assumptions, "boiler", "max_heat_output_mw"))
    total_capacity = chp_max + boiler_max
    infeasible = steam > total_capacity + BALANCE_TOLERANCE_MW
    if not infeasible.any():
        return
    timestamp = steam.index[infeasible][0]
    demand = float(steam.loc[timestamp])
    raise OptimizationError(
        "Steam demand is infeasible at "
        f"{timestamp}: {demand:.3f} MW exceeds CHP capacity {chp_max:.3f} MW "
        f"plus boiler capacity {boiler_max:.3f} MW "
        f"(total {total_capacity:.3f} MW). Unmet steam demand is not allowed."
    )


def _require_aligned_inputs(demand: pd.DataFrame, prices: pd.DataFrame) -> None:
    for column in ("steam_demand_mw", "electricity_demand_mw"):
        if column not in demand.columns:
            raise OptimizationError(f"Demand is missing {column}")
    for column in (
        "day_ahead_price_eur_mwh",
        "electricity_import_price_eur_mwh",
        "electricity_export_price_eur_mwh",
    ):
        if column not in prices.columns:
            raise OptimizationError(f"Prices are missing {column}")
    if demand.empty:
        raise OptimizationError("Demand series is empty")
    if not demand.index.equals(prices.index):
        raise OptimizationError("Demand hours and price hours do not match one-to-one")
    if demand[["steam_demand_mw", "electricity_demand_mw"]].isna().any().any():
        raise OptimizationError("Demand contains NaN")
    if prices.isna().any().any():
        raise OptimizationError("Price series contains NaN")


def _series_from_var(variable: pyo.Var, index: pd.Index) -> pd.Series:
    values = []
    for position in range(len(index)):
        value = variable[position].value
        if value is None:
            raise OptimizationError("HiGHS did not return a complete dispatch solution")
        values.append(0.0 if abs(value) <= 1e-10 else float(value))
    return pd.Series(values, index=index, dtype=float)


def _close(
    actual: pd.Series,
    expected: Any,
    message: str,
    tolerance: float = BALANCE_TOLERANCE_MW,
) -> None:
    gap = float((actual - expected).abs().max())
    if gap > tolerance:
        raise OptimizationError(f"{message}; largest gap is {gap:.6g}")


def _gwh(series: pd.Series) -> float:
    return float(series.sum()) / 1000.0


def _value(assumptions: Any, section: str, key: str):
    if hasattr(assumptions, "value"):
        return assumptions.value(section, key)
    return assumptions[section][key]["value"]
