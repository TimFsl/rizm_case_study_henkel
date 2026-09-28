"""Calibrated rule-based reference dispatch.

Steam demand drives the operation. A fixed share of that steam is assigned
to the aggregated CHP, the boiler covers the residual, and the grid closes
the electricity balance. Fuel use is calculated from that dispatch. It is
not an input to demand. This is not a reconstruction of Henkel's control
logic, and it does not respond to market prices.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

BALANCE_TOLERANCE_MW = 1e-6
NEAR_CAPACITY_FRACTION = 0.99

DISPATCH_COLUMNS = (
    "steam_demand_mw",
    "electricity_demand_mw",
    "chp_steam_mw",
    "boiler_steam_mw",
    "chp_electricity_mw",
    "chp_fuel_mwh",
    "boiler_fuel_mwh",
    "total_fuel_mwh",
    "fossil_gas_fuel_mwh",
    "biomethane_fuel_mwh",
    "coal_fuel_mwh",
    "grid_import_mw",
    "grid_export_mw",
    "net_grid_import_mw",
)


class BaselineError(ValueError):
    """Raised when the reference dispatch is infeasible or inconsistent."""


def dispatch_baseline(demand_profile: pd.DataFrame, assumptions: Any) -> pd.DataFrame:
    """Dispatch CHP, boiler, fuel, and grid for one exogenous demand profile."""
    _require_demand_columns(demand_profile)
    steam_demand = demand_profile["steam_demand_mw"].astype(float)
    electricity_demand = demand_profile["electricity_demand_mw"].astype(float)

    chp_max = float(_value(assumptions, "chp", "max_heat_output_mw"))
    boiler_max = float(_value(assumptions, "boiler", "max_heat_output_mw"))
    steam_share = float(_value(assumptions, "chp", "baseline_steam_share"))
    power_to_heat = float(_value(assumptions, "chp", "power_to_heat_ratio"))
    chp_efficiency = float(_value(assumptions, "chp", "total_utilization_efficiency"))
    boiler_efficiency = float(_value(assumptions, "boiler", "thermal_efficiency"))
    fossil_share = float(_value(assumptions, "fuel_mix", "fossil_gas_share"))
    biomethane_share = float(_value(assumptions, "fuel_mix", "biomethane_share"))
    coal_share = float(_value(assumptions, "fuel_mix", "coal_share"))
    import_capacity = float(_value(assumptions, "grid", "import_capacity_mw"))

    _raise_if_steam_is_infeasible(steam_demand, chp_max, boiler_max)

    chp_target = steam_share * steam_demand
    chp_required = (steam_demand - boiler_max).clip(lower=0.0)
    chp_steam = pd.concat([chp_target, chp_required], axis=1).max(axis=1).clip(upper=chp_max)
    boiler_steam = steam_demand - chp_steam
    chp_electricity = chp_steam * power_to_heat

    # One fuel input covers both useful CHP products. Do not fuel them separately.
    chp_fuel = (chp_steam + chp_electricity) / chp_efficiency
    boiler_fuel = boiler_steam / boiler_efficiency
    total_fuel = chp_fuel + boiler_fuel

    net_grid = electricity_demand - chp_electricity
    grid_import = net_grid.clip(lower=0.0)
    grid_export = (-net_grid).clip(lower=0.0)
    _raise_if_import_is_infeasible(grid_import, import_capacity)

    dispatch = pd.DataFrame(
        {
            "steam_demand_mw": steam_demand,
            "electricity_demand_mw": electricity_demand,
            "chp_steam_mw": chp_steam,
            "boiler_steam_mw": boiler_steam,
            "chp_electricity_mw": chp_electricity,
            "chp_fuel_mwh": chp_fuel,
            "boiler_fuel_mwh": boiler_fuel,
            "total_fuel_mwh": total_fuel,
            "fossil_gas_fuel_mwh": total_fuel * fossil_share,
            "biomethane_fuel_mwh": total_fuel * biomethane_share,
            "coal_fuel_mwh": total_fuel * coal_share,
            "grid_import_mw": grid_import,
            "grid_export_mw": grid_export,
            "net_grid_import_mw": grid_import - grid_export,
        },
        index=demand_profile.index,
    )
    dispatch.index.name = demand_profile.index.name or "timestamp"
    validate_baseline_dispatch(dispatch, assumptions)
    return dispatch


def validate_baseline_dispatch(dispatch: pd.DataFrame, assumptions: Any) -> None:
    """Raise BaselineError if an hourly physical balance does not close."""
    chp_max = float(_value(assumptions, "chp", "max_heat_output_mw"))
    boiler_max = float(_value(assumptions, "boiler", "max_heat_output_mw"))
    chp_efficiency = float(_value(assumptions, "chp", "total_utilization_efficiency"))
    boiler_efficiency = float(_value(assumptions, "boiler", "thermal_efficiency"))
    import_capacity = float(_value(assumptions, "grid", "import_capacity_mw"))

    steam = dispatch["steam_demand_mw"]
    chp_steam = dispatch["chp_steam_mw"]
    boiler_steam = dispatch["boiler_steam_mw"]
    _close(
        chp_steam + boiler_steam,
        steam,
        "CHP steam plus boiler steam must equal steam demand",
    )
    _close(
        dispatch["chp_electricity_mw"] + dispatch["grid_import_mw"] - dispatch["grid_export_mw"],
        dispatch["electricity_demand_mw"],
        "CHP electricity plus grid import minus grid export must equal electricity demand",
    )
    _require_nonnegative(chp_steam, "chp_steam_mw")
    _require_nonnegative(boiler_steam, "boiler_steam_mw")
    _require_nonnegative(dispatch["chp_electricity_mw"], "chp_electricity_mw")
    for column in (
        "chp_fuel_mwh",
        "boiler_fuel_mwh",
        "total_fuel_mwh",
        "fossil_gas_fuel_mwh",
        "biomethane_fuel_mwh",
        "coal_fuel_mwh",
        "grid_import_mw",
        "grid_export_mw",
    ):
        _require_nonnegative(dispatch[column], column)

    if (chp_steam > chp_max + BALANCE_TOLERANCE_MW).any():
        raise BaselineError("CHP steam exceeds configured heat capacity")
    if (boiler_steam > boiler_max + BALANCE_TOLERANCE_MW).any():
        raise BaselineError("Boiler steam exceeds configured heat capacity")
    if (dispatch["grid_import_mw"] > import_capacity + BALANCE_TOLERANCE_MW).any():
        raise BaselineError("Grid import exceeds configured import capacity")

    both = (dispatch["grid_import_mw"] > BALANCE_TOLERANCE_MW) & (
        dispatch["grid_export_mw"] > BALANCE_TOLERANCE_MW
    )
    if both.any():
        raise BaselineError("Grid import and export are simultaneously positive")

    chp_fuel = dispatch["chp_fuel_mwh"]
    producing = chp_fuel > BALANCE_TOLERANCE_MW
    useful = chp_steam + dispatch["chp_electricity_mw"]
    _close(
        useful.loc[producing] / chp_fuel.loc[producing],
        chp_efficiency,
        "CHP useful output over fuel must equal total utilization efficiency",
    )
    boiler_fuel = dispatch["boiler_fuel_mwh"]
    boiler_on = boiler_steam > BALANCE_TOLERANCE_MW
    _close(
        boiler_steam.loc[boiler_on] / boiler_fuel.loc[boiler_on],
        boiler_efficiency,
        "Boiler steam over fuel must equal boiler efficiency",
    )
    _close(
        dispatch["fossil_gas_fuel_mwh"]
        + dispatch["biomethane_fuel_mwh"]
        + dispatch["coal_fuel_mwh"],
        dispatch["total_fuel_mwh"],
        "Fuel-mix components must sum to total fuel",
    )


def summarize_baseline(dispatch: pd.DataFrame, assumptions: Any) -> dict[str, float]:
    """Annual energy and peak metrics. Sums are unrounded."""
    chp_max = float(_value(assumptions, "chp", "max_heat_output_mw"))
    boiler_max = float(_value(assumptions, "boiler", "max_heat_output_mw"))
    steam_gwh = _gwh(dispatch["steam_demand_mw"])
    chp_steam_gwh = _gwh(dispatch["chp_steam_mw"])
    chp_electricity_gwh = _gwh(dispatch["chp_electricity_mw"])
    peak_chp = float(dispatch["chp_steam_mw"].max())
    peak_boiler = float(dispatch["boiler_steam_mw"].max())
    return {
        "steam_demand_gwh": steam_gwh,
        "chp_steam_gwh": chp_steam_gwh,
        "boiler_steam_gwh": _gwh(dispatch["boiler_steam_mw"]),
        "chp_steam_share": chp_steam_gwh / steam_gwh,
        "electricity_demand_gwh": _gwh(dispatch["electricity_demand_mw"]),
        "chp_electricity_gwh": chp_electricity_gwh,
        "chp_fuel_gwh": _gwh(dispatch["chp_fuel_mwh"]),
        "boiler_fuel_gwh": _gwh(dispatch["boiler_fuel_mwh"]),
        "total_fuel_gwh": _gwh(dispatch["total_fuel_mwh"]),
        "fossil_gas_gwh": _gwh(dispatch["fossil_gas_fuel_mwh"]),
        "biomethane_gwh": _gwh(dispatch["biomethane_fuel_mwh"]),
        "coal_gwh": _gwh(dispatch["coal_fuel_mwh"]),
        "grid_import_gwh": _gwh(dispatch["grid_import_mw"]),
        "grid_export_gwh": _gwh(dispatch["grid_export_mw"]),
        "net_grid_import_gwh": _gwh(dispatch["net_grid_import_mw"]),
        "peak_grid_import_mw": float(dispatch["grid_import_mw"].max()),
        "peak_grid_export_mw": float(dispatch["grid_export_mw"].max()),
        "grid_export_hours": float((dispatch["grid_export_mw"] > BALANCE_TOLERANCE_MW).sum()),
        "peak_steam_demand_mw": float(dispatch["steam_demand_mw"].max()),
        "peak_chp_steam_mw": peak_chp,
        "peak_boiler_steam_mw": peak_boiler,
        "chp_heat_capacity_utilization_peak": peak_chp / chp_max,
        "boiler_heat_capacity_utilization_peak": peak_boiler / boiler_max,
        "chp_hours_near_capacity": float(
            (dispatch["chp_steam_mw"] >= NEAR_CAPACITY_FRACTION * chp_max).sum()
        ),
        "boiler_hours_near_capacity": float(
            (dispatch["boiler_steam_mw"] >= NEAR_CAPACITY_FRACTION * boiler_max).sum()
        ),
        "chp_electricity_per_steam_demand": chp_electricity_gwh / steam_gwh,
    }


def unconstrained_annual_expectations(
    annual_steam_gwh: float,
    annual_electricity_gwh: float,
    assumptions: Any,
) -> dict[str, float]:
    """Annual results if the configured CHP steam share never hits a capacity limit."""
    steam_share = float(_value(assumptions, "chp", "baseline_steam_share"))
    power_to_heat = float(_value(assumptions, "chp", "power_to_heat_ratio"))
    chp_efficiency = float(_value(assumptions, "chp", "total_utilization_efficiency"))
    boiler_efficiency = float(_value(assumptions, "boiler", "thermal_efficiency"))
    chp_steam = annual_steam_gwh * steam_share
    boiler_steam = annual_steam_gwh - chp_steam
    chp_electricity = chp_steam * power_to_heat
    chp_fuel = (chp_steam + chp_electricity) / chp_efficiency
    boiler_fuel = boiler_steam / boiler_efficiency
    return {
        "chp_steam_gwh": chp_steam,
        "boiler_steam_gwh": boiler_steam,
        "chp_electricity_gwh": chp_electricity,
        "chp_fuel_gwh": chp_fuel,
        "boiler_fuel_gwh": boiler_fuel,
        "total_fuel_gwh": chp_fuel + boiler_fuel,
        "net_grid_import_gwh": annual_electricity_gwh - chp_electricity,
    }


def save_base_dispatch_plots(dispatch: pd.DataFrame, path_stem: Path) -> None:
    """Save one steam week and one electricity week for the base scenario."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    mondays = dispatch.index[dispatch.index.dayofweek == 0]
    start = mondays[0]
    week = dispatch.loc[
        (dispatch.index >= start) & (dispatch.index < start + pd.Timedelta(days=7))
    ]
    path_stem.parent.mkdir(parents=True, exist_ok=True)

    steam_figure, steam_axis = plt.subplots(figsize=(10, 4))
    week[["steam_demand_mw", "chp_steam_mw", "boiler_steam_mw"]].plot(ax=steam_axis)
    steam_axis.set_title(
        f"Base reference dispatch, steam, week of {start.date()}"
    )
    steam_axis.set_ylabel("MW")
    steam_axis.set_xlabel("Local time")
    steam_axis.legend(["Steam demand", "CHP steam", "Boiler steam"])
    steam_figure.tight_layout()
    steam_figure.savefig(path_stem.parent / "baseline_base_steam_week.png", dpi=120)
    plt.close(steam_figure)

    electricity_figure, electricity_axis = plt.subplots(figsize=(10, 4))
    week[
        [
            "electricity_demand_mw",
            "chp_electricity_mw",
            "grid_import_mw",
            "grid_export_mw",
        ]
    ].plot(ax=electricity_axis)
    electricity_axis.set_title(
        f"Base reference dispatch, electricity, week of {start.date()}"
    )
    electricity_axis.set_ylabel("MW")
    electricity_axis.set_xlabel("Local time")
    electricity_axis.legend(
        ["Site electricity demand", "CHP electricity", "Grid import", "Grid export"]
    )
    electricity_figure.tight_layout()
    electricity_figure.savefig(
        path_stem.parent / "baseline_base_electricity_week.png",
        dpi=120,
    )
    plt.close(electricity_figure)


def _raise_if_steam_is_infeasible(
    steam_demand: pd.Series,
    chp_max: float,
    boiler_max: float,
) -> None:
    total_capacity = chp_max + boiler_max
    infeasible = steam_demand > total_capacity + BALANCE_TOLERANCE_MW
    if not infeasible.any():
        return
    timestamp = steam_demand.index[infeasible][0]
    demand = float(steam_demand.loc[timestamp])
    raise BaselineError(
        "Steam demand is infeasible at "
        f"{timestamp}: {demand:.3f} MW exceeds CHP capacity {chp_max:.3f} MW "
        f"plus boiler capacity {boiler_max:.3f} MW "
        f"(total {total_capacity:.3f} MW). Unmet steam demand is not clipped."
    )


def _raise_if_import_is_infeasible(grid_import: pd.Series, import_capacity: float) -> None:
    infeasible = grid_import > import_capacity + BALANCE_TOLERANCE_MW
    if not infeasible.any():
        return
    timestamp = grid_import.index[infeasible][0]
    imported = float(grid_import.loc[timestamp])
    raise BaselineError(
        "Grid import is infeasible at "
        f"{timestamp}: {imported:.3f} MW exceeds import capacity "
        f"{import_capacity:.3f} MW. Grid import is not clipped."
    )


def _close(actual: Any, expected: Any, message: str) -> None:
    if isinstance(expected, (int, float)):
        difference = (actual - float(expected)).abs()
    else:
        difference = (actual - expected).abs()
    if difference.max() > BALANCE_TOLERANCE_MW:
        raise BaselineError(f"{message}; largest gap is {float(difference.max()):.3e}")


def _require_nonnegative(series: pd.Series, name: str) -> None:
    if (series < -BALANCE_TOLERANCE_MW).any():
        raise BaselineError(f"{name} contains a negative value")


def _require_demand_columns(demand_profile: pd.DataFrame) -> None:
    missing = [
        column
        for column in ("steam_demand_mw", "electricity_demand_mw")
        if column not in demand_profile.columns
    ]
    if missing:
        raise BaselineError(
            "Demand profile is missing columns: " + ", ".join(missing)
        )


def _gwh(series: pd.Series) -> float:
    return float(series.sum()) / 1000.0


def _value(assumptions: Any, section: str, key: str):
    if hasattr(assumptions, "value"):
        return assumptions.value(section, key)
    return assumptions[section][key]["value"]
