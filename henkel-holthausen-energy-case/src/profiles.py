"""Synthetic hourly steam and electricity demand profiles.

Known annual energy is given a transparent shape and then normalized back
to that annual total. The shapes are screening scenarios, not measured
Henkel load data.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import pandas as pd

WEEKDAY_BY_CODE = {
    0: "monday",
    1: "tuesday",
    2: "wednesday",
    3: "thursday",
    4: "friday",
    5: "saturday",
    6: "sunday",
}

# Sum of hourly MW * 1 h must match annual MWh inside this absolute tolerance.
ENERGY_TOLERANCE_MWH = 1e-6

PROFILE_COLUMNS = ("steam_demand_mw", "electricity_demand_mw")


class ProfileError(ValueError):
    """Raised when a demand profile cannot be built."""


def build_hourly_index(model_year: int, timezone: str) -> pd.DatetimeIndex:
    """Return the local hourly index for one non-leap screening year.

    Europe/Berlin drops one hour in spring and repeats one hour in autumn.
    Those two transitions cancel, so the calendar year still has 8,760 hours.
    """
    start = pd.Timestamp(year=int(model_year), month=1, day=1, tz=timezone)
    end = pd.Timestamp(year=int(model_year) + 1, month=1, day=1, tz=timezone)
    index = pd.date_range(start=start, end=end, freq="h", inclusive="left")
    if len(index) != 8760:
        raise ProfileError(
            f"Expected 8760 hourly timestamps for {model_year} in {timezone}; "
            f"got {len(index)}"
        )
    if not index.is_unique:
        raise ProfileError("Hourly index contains duplicate timestamps")
    if index.tz is None:
        raise ProfileError("Hourly index must be timezone-aware")
    return index


def build_raw_shape(
    index: pd.DatetimeIndex,
    hourly_factors: Mapping[Any, float],
    weekday_factors: Mapping[Any, float],
    monthly_factors: Mapping[Any, float],
) -> pd.Series:
    """Multiply hour, weekday, and month factors. The result is unnormalized."""
    hour_map = _number_key_map(hourly_factors, "hourly")
    weekday_map = {str(key).lower(): float(value) for key, value in weekday_factors.items()}
    month_map = _number_key_map(monthly_factors, "monthly")

    hour_factor = pd.Series(index.hour, index=index).map(hour_map)
    weekday_names = pd.Series(index.dayofweek, index=index).map(WEEKDAY_BY_CODE)
    weekday_factor = weekday_names.map(weekday_map)
    month_factor = pd.Series(index.month, index=index).map(month_map)
    _raise_if_unmapped(hour_factor, "hourly")
    _raise_if_unmapped(weekday_factor, "weekday")
    _raise_if_unmapped(month_factor, "monthly")
    return hour_factor * weekday_factor * month_factor


def normalize_to_annual_energy(
    raw_shape: pd.Series,
    annual_energy_gwh: float,
) -> pd.Series:
    """Scale an hourly shape so its sum equals the annual energy, in MW."""
    if annual_energy_gwh <= 0:
        raise ProfileError(
            f"Annual energy must be positive; got {annual_energy_gwh}"
        )
    shape_sum = float(raw_shape.sum())
    if shape_sum <= 0:
        raise ProfileError("Raw shape must sum to a positive number")
    annual_energy_mwh = float(annual_energy_gwh) * 1000.0
    # Timestep is 1 hour, so MWh per timestep is numerically equal to MW.
    return raw_shape * (annual_energy_mwh / shape_sum)


def build_demand_profile(assumptions: Any, scenario: str) -> pd.DataFrame:
    """Build exogenous hourly steam and electricity demand for one scenario."""
    scenarios = _section(assumptions, "demand_profile_scenarios")
    if scenario not in scenarios:
        known = ", ".join(str(name) for name in scenarios)
        raise ProfileError(
            f"Unknown demand profile scenario {scenario!r}. Known scenarios: {known}"
        )
    block = scenarios[scenario]
    model_year = _value(assumptions, "model", "model_year")
    timezone = _value(assumptions, "model", "timezone")
    index = build_hourly_index(int(model_year), str(timezone))
    expected_hours = int(_value(assumptions, "model", "hours_per_year"))
    if len(index) != expected_hours:
        raise ProfileError(
            f"Hourly index length {len(index)} does not match "
            f"model.hours_per_year {expected_hours}"
        )

    columns = {}
    annual_keys = {
        "steam": "annual_steam_heat_gwh",
        "electricity": "annual_electricity_gwh",
    }
    output_names = {
        "steam": "steam_demand_mw",
        "electricity": "electricity_demand_mw",
    }
    for carrier, annual_key in annual_keys.items():
        factors = block[carrier]
        raw_shape = build_raw_shape(
            index,
            factors["hourly_factors"],
            factors["weekday_factors"],
            factors["monthly_factors"],
        )
        annual_gwh = _value(assumptions, "demand", annual_key)
        columns[output_names[carrier]] = normalize_to_annual_energy(
            raw_shape, float(annual_gwh)
        )

    profile = pd.DataFrame(columns, index=index)
    profile.index.name = "timestamp"
    return profile


def summarize_profile(profile: pd.DataFrame) -> dict[str, float]:
    """Return annual energy and min/average/max statistics. Values are unrounded."""
    summary = {}
    for carrier, column in (
        ("steam", "steam_demand_mw"),
        ("electricity", "electricity_demand_mw"),
    ):
        series = profile[column]
        average = float(series.mean())
        minimum = float(series.min())
        maximum = float(series.max())
        summary[f"{carrier}_energy_gwh"] = float(series.sum()) / 1000.0
        summary[f"{carrier}_average_mw"] = average
        summary[f"{carrier}_min_mw"] = minimum
        summary[f"{carrier}_max_mw"] = maximum
        summary[f"{carrier}_min_to_average"] = minimum / average
        summary[f"{carrier}_max_to_average"] = maximum / average
    return summary


def save_normalized_week_plot(
    profile: pd.DataFrame,
    scenario: str,
    path: Path,
) -> None:
    """Save one QA week with both demands divided by their annual average."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    mondays = profile.index[profile.index.dayofweek == 0]
    start = mondays[0]
    week = profile.loc[(profile.index >= start) & (profile.index < start + pd.Timedelta(days=7))]
    frame = pd.DataFrame(
        {
            "steam": week["steam_demand_mw"] / profile["steam_demand_mw"].mean(),
            "electricity": week["electricity_demand_mw"] / profile["electricity_demand_mw"].mean(),
        },
        index=week.index,
    )
    figure, axis = plt.subplots(figsize=(10, 4))
    frame.plot(ax=axis)
    axis.set_title(
        f"{scenario} synthetic demand, week of {start.date()} (load / annual average)"
    )
    axis.set_ylabel("Load / annual average")
    axis.set_xlabel("Local time")
    axis.legend(["Steam", "Electricity"])
    figure.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(path, dpi=120)
    plt.close(figure)


def _number_key_map(mapping: Mapping[Any, float], label: str) -> dict[int, float]:
    converted = {}
    for key, value in mapping.items():
        try:
            converted[int(key)] = float(value)
        except (TypeError, ValueError) as exc:
            raise ProfileError(f"Invalid {label} factor key {key!r}") from exc
    return converted


def _raise_if_unmapped(series: pd.Series, label: str) -> None:
    if series.isna().any():
        raise ProfileError(f"Missing {label} shape factor for one or more timestamps")


def _section(assumptions: Any, name: str):
    if hasattr(assumptions, "section"):
        return assumptions.section(name)
    return assumptions[name]


def _value(assumptions: Any, section: str, key: str):
    if hasattr(assumptions, "value"):
        return assumptions.value(section, key)
    return assumptions[section][key]["value"]
