"""QA figures from processed demand, price, and baseline-dispatch files.

This module only reads existing CSV outputs. It does not rebuild profiles,
dispatch the plant, or change costs.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd

DEFAULT_WEEK_START = "2026-01-06"
DEFAULT_DISPATCH_SCENARIO = "base"
SCENARIOS = ("flat", "base", "variable")
FIGURE_DPI = 150

DEMAND_COLORS = {
    "flat": "#4d4d4d",
    "base": "#1f77b4",
    "variable": "#ff7f0e",
}
STEAM_DISPATCH_COLORS = {
    "steam_demand_mw": "#222222",
    "chp_steam_mw": "#1f77b4",
    "boiler_steam_mw": "#d62728",
}
ELECTRICITY_DISPATCH_COLORS = {
    "electricity_demand_mw": "#222222",
    "chp_electricity_mw": "#1f77b4",
    "grid_import_mw": "#2ca02c",
    "grid_export_mw": "#ff7f0e",
}
SERIES_LABELS = {
    "steam_demand_mw": "Steam demand",
    "chp_steam_mw": "CHP steam",
    "boiler_steam_mw": "Boiler steam",
    "electricity_demand_mw": "Electricity demand",
    "chp_electricity_mw": "CHP electricity",
    "grid_import_mw": "Grid import",
    "grid_export_mw": "Grid export",
    "day_ahead_price_eur_mwh": "Day-ahead price",
}


class PlotInputError(ValueError):
    """Raised when a processed file needed for plotting is missing or unusable."""


def generate_figures(
    root: Path | str,
    week_start: str = DEFAULT_WEEK_START,
    dispatch_scenario: str = DEFAULT_DISPATCH_SCENARIO,
    figures_dir: Path | str | None = None,
    tables_dir: Path | str | None = None,
) -> list[Path]:
    """Write the QA figures and the optional week-window summary."""
    root = Path(root)
    if dispatch_scenario not in SCENARIOS:
        known = ", ".join(SCENARIOS)
        raise PlotInputError(
            f"Unknown dispatch scenario {dispatch_scenario!r}. Known scenarios: {known}"
        )
    figure_directory = Path(figures_dir) if figures_dir is not None else root / "outputs" / "figures"
    table_directory = Path(tables_dir) if tables_dir is not None else root / "outputs" / "tables"
    figure_directory.mkdir(parents=True, exist_ok=True)
    table_directory.mkdir(parents=True, exist_ok=True)
    timezone = _model_timezone(root)

    demands = {
        scenario: load_hourly_csv(
            resolve_processed_csv(
                root,
                f"demand_profile_{scenario}.csv",
                ("demand_profile", scenario),
            ),
            ("steam_demand_mw", "electricity_demand_mw"),
            timezone,
        )
        for scenario in SCENARIOS
    }
    dispatch = load_hourly_csv(
        resolve_processed_csv(
            root,
            f"baseline_dispatch_{dispatch_scenario}.csv",
            ("baseline_dispatch", dispatch_scenario),
        ),
        (
            "steam_demand_mw",
            "electricity_demand_mw",
            "chp_steam_mw",
            "boiler_steam_mw",
            "chp_electricity_mw",
            "grid_import_mw",
            "grid_export_mw",
        ),
        timezone,
    )
    prices = load_hourly_csv(
        resolve_processed_csv(
            root,
            "electricity_day_ahead_prices.csv",
            ("day_ahead",),
        ),
        ("day_ahead_price_eur_mwh",),
        timezone,
    )

    demand_weeks = {
        scenario: select_week(frame, week_start) for scenario, frame in demands.items()
    }
    dispatch_week = select_week(dispatch, week_start)
    price_week = select_week(prices, week_start)
    _require_same_index(demand_weeks["base"], dispatch_week, "base demand", "baseline dispatch")
    _require_same_index(dispatch_week, price_week, "baseline dispatch", "day-ahead prices")
    week_title = _week_title(dispatch_week)

    saved = [
        _plot_demand_overlay(
            demand_weeks,
            "steam_demand_mw",
            "Steam demand",
            "MW steam",
            f"Synthetic steam demand, {week_title}",
            figure_directory / "demand_steam_week.png",
        ),
        _plot_demand_overlay(
            demand_weeks,
            "electricity_demand_mw",
            "Electricity demand",
            "MW electricity",
            f"Synthetic electricity demand, {week_title}",
            figure_directory / "demand_electricity_week.png",
        ),
        _plot_price_week(
            price_week,
            f"Day-ahead electricity price, {week_title}",
            figure_directory / "electricity_price_week.png",
        ),
        _plot_price_year(
            prices,
            figure_directory / "electricity_price_year.png",
        ),
        _plot_dispatch_series(
            dispatch_week,
            ("steam_demand_mw", "chp_steam_mw", "boiler_steam_mw"),
            STEAM_DISPATCH_COLORS,
            "MW steam",
            f"Baseline dispatch, steam, {dispatch_scenario}, {week_title}",
            figure_directory / "baseline_dispatch_steam_week.png",
        ),
        _plot_dispatch_series(
            dispatch_week,
            (
                "electricity_demand_mw",
                "chp_electricity_mw",
                "grid_import_mw",
                "grid_export_mw",
            ),
            ELECTRICITY_DISPATCH_COLORS,
            "MW electricity",
            f"Baseline dispatch, electricity, {dispatch_scenario}, {week_title}",
            figure_directory / "baseline_dispatch_electricity_week.png",
        ),
        _plot_dashboard(
            dispatch_week,
            price_week,
            dispatch_scenario,
            week_title,
            figure_directory / "baseline_dispatch_dashboard_week.png",
        ),
        _write_window_summary(
            demand_weeks,
            price_week,
            dispatch_week,
            dispatch_scenario,
            table_directory / "plot_window_summary.csv",
        ),
    ]
    return saved


def _model_timezone(root: Path) -> str:
    from src.assumptions import AssumptionError, load_assumptions

    try:
        timezone = load_assumptions(Path(root) / "config" / "assumptions.yaml").value(
            "model", "timezone"
        )
    except AssumptionError as exc:
        raise PlotInputError(f"Could not read the model timezone: {exc}") from exc
    if not isinstance(timezone, str) or not timezone.strip():
        raise PlotInputError("model.timezone must be a non-empty string")
    return timezone


def resolve_processed_csv(root: Path, canonical_name: str, hints: tuple[str, ...]) -> Path:
    """Return the canonical processed file, or one unambiguous close match."""
    folder = Path(root) / "data" / "processed"
    canonical = folder / canonical_name
    if canonical.is_file():
        return canonical
    if not folder.is_dir():
        raise PlotInputError(f"Processed data folder not found: {folder}")
    matches = []
    for candidate in sorted(folder.glob("*.csv")):
        name = candidate.name.lower()
        if all(hint.lower() in name for hint in hints):
            matches.append(candidate)
    if len(matches) == 1:
        return matches[0]
    available = ", ".join(path.name for path in sorted(folder.glob("*.csv"))) or "(none)"
    raise PlotInputError(
        f"Could not find {canonical_name} in {folder}. Available CSV files: {available}"
    )


def load_hourly_csv(
    path: Path,
    required_columns: tuple[str, ...],
    timezone: str,
) -> pd.DataFrame:
    """Load one processed hourly file onto the model timezone.

    The stored timestamps carry both CET and CEST offsets. Parsing them in
    UTC and converting to the model timezone keeps one consistent local clock.
    """
    if not path.is_file():
        raise PlotInputError(f"Missing processed input: {path}")
    frame = pd.read_csv(path)
    if "timestamp" not in frame.columns:
        raise PlotInputError(f"{path.name} has no timestamp column")
    missing = [column for column in required_columns if column not in frame.columns]
    if missing:
        raise PlotInputError(
            f"{path.name} is missing columns: {', '.join(missing)}"
        )
    raw = frame["timestamp"].astype(str)
    if not raw.str.contains(r"(?:[+-]\d{2}:\d{2}|Z)$", regex=True).all():
        raise PlotInputError(f"{path.name} timestamps must include a timezone offset")
    timestamps = pd.to_datetime(raw, utc=True).dt.tz_convert(timezone)
    loaded = frame.drop(columns=["timestamp"]).copy()
    loaded.index = pd.DatetimeIndex(timestamps)
    loaded.index.name = "timestamp"
    return loaded.sort_index()


def select_week(frame: pd.DataFrame, week_start: str) -> pd.DataFrame:
    """Return the half-open local week starting at week_start."""
    if frame.empty:
        raise PlotInputError("Cannot select a week from an empty series")
    timezone = frame.index.tz
    if timezone is None:
        raise PlotInputError("Series index must be timezone-aware")
    start = pd.Timestamp(week_start)
    if start.tzinfo is None:
        start = start.tz_localize(timezone)
    else:
        start = start.tz_convert(timezone)
    end = start + pd.Timedelta(days=7)
    week = frame.loc[(frame.index >= start) & (frame.index < end)]
    if week.empty:
        raise PlotInputError(
            f"No rows from {start.isoformat()} to {end.isoformat()}. "
            f"Series runs from {frame.index[0].isoformat()} "
            f"to {frame.index[-1].isoformat()}."
        )
    return week


def _plot_demand_overlay(
    weeks: dict[str, pd.DataFrame],
    column: str,
    legend_prefix: str,
    ylabel: str,
    title: str,
    path: Path,
) -> Path:
    figure, axis = plt.subplots(figsize=(10, 4))
    for scenario in SCENARIOS:
        plotted = _wall_clock(weeks[scenario])
        axis.plot(
            plotted.index,
            plotted[column],
            color=DEMAND_COLORS[scenario],
            linewidth=1.4,
            label=f"{legend_prefix}, {scenario}",
        )
    _format_week_axis(axis, ylabel, legend=False)
    axis.set_title(title)
    axis.legend(
        frameon=False,
        loc="upper center",
        bbox_to_anchor=(0.5, -0.28),
        ncol=3,
    )
    _save(figure, path)
    return path


def _plot_price_week(week: pd.DataFrame, title: str, path: Path) -> Path:
    plotted = _wall_clock(week)
    figure, axis = plt.subplots(figsize=(10, 4))
    axis.plot(
        plotted.index,
        plotted["day_ahead_price_eur_mwh"],
        color="#1f77b4",
        linewidth=1.2,
        label="Day-ahead price",
    )
    axis.axhline(0.0, color="#666666", linewidth=0.8, linestyle="--", label="0 EUR/MWh")
    _format_week_axis(axis, "EUR/MWh")
    axis.set_title(title)
    _save(figure, path)
    return path


def _plot_price_year(prices: pd.DataFrame, path: Path) -> Path:
    plotted = _wall_clock(prices)
    source_year = None
    if "source_market_year" in plotted.columns and plotted["source_market_year"].nunique() == 1:
        source_year = int(plotted["source_market_year"].iloc[0])
    start = plotted.index[0]
    end = plotted.index[-1]
    if source_year is None:
        title = f"Day-ahead electricity price, {start:%Y} screening hours"
    else:
        title = (
            f"Day-ahead electricity price, {source_year} prices "
            f"on {start:%Y} screening hours"
        )
    figure, axis = plt.subplots(figsize=(10, 3.6))
    axis.plot(
        plotted.index,
        plotted["day_ahead_price_eur_mwh"],
        color="#1f77b4",
        linewidth=0.4,
        label="Day-ahead price",
    )
    axis.axhline(0.0, color="#666666", linewidth=0.8, linestyle="--", label="0 EUR/MWh")
    axis.set_title(title)
    axis.set_ylabel("EUR/MWh")
    axis.set_xlabel("Local time")
    axis.xaxis.set_major_locator(mdates.MonthLocator())
    axis.xaxis.set_major_formatter(mdates.DateFormatter("%b"))
    axis.legend(frameon=False)
    axis.margins(x=0)
    _save(figure, path)
    return path


def _plot_dispatch_series(
    week: pd.DataFrame,
    columns: tuple[str, ...],
    colors: dict[str, str],
    ylabel: str,
    title: str,
    path: Path,
) -> Path:
    plotted = _wall_clock(week)
    figure, axis = plt.subplots(figsize=(10, 4))
    for column in columns:
        axis.plot(
            plotted.index,
            plotted[column],
            color=colors[column],
            linewidth=1.3,
            label=SERIES_LABELS[column],
        )
    _format_week_axis(axis, ylabel)
    axis.set_title(title)
    _save(figure, path)
    return path


def _plot_dashboard(
    dispatch_week: pd.DataFrame,
    price_week: pd.DataFrame,
    scenario: str,
    week_title: str,
    path: Path,
) -> Path:
    dispatch = _wall_clock(dispatch_week)
    price = _wall_clock(price_week)
    figure, axes = plt.subplots(3, 1, figsize=(10, 8), sharex=True)
    steam_columns = ("steam_demand_mw", "chp_steam_mw", "boiler_steam_mw")
    electricity_columns = (
        "electricity_demand_mw",
        "chp_electricity_mw",
        "grid_import_mw",
        "grid_export_mw",
    )
    for column in steam_columns:
        axes[0].plot(
            dispatch.index,
            dispatch[column],
            color=STEAM_DISPATCH_COLORS[column],
            linewidth=1.2,
            label=SERIES_LABELS[column],
        )
    for column in electricity_columns:
        axes[1].plot(
            dispatch.index,
            dispatch[column],
            color=ELECTRICITY_DISPATCH_COLORS[column],
            linewidth=1.2,
            label=SERIES_LABELS[column],
        )
    axes[2].plot(
        price.index,
        price["day_ahead_price_eur_mwh"],
        color="#1f77b4",
        linewidth=1.2,
        label="Day-ahead price",
    )
    axes[2].axhline(0.0, color="#666666", linewidth=0.8, linestyle="--", label="0 EUR/MWh")
    axes[0].set_ylabel("MW steam")
    axes[1].set_ylabel("MW electricity")
    axes[2].set_ylabel("EUR/MWh")
    axes[0].set_title("Steam balance")
    axes[1].set_title("Electricity balance")
    axes[2].set_title("Day-ahead price")
    for axis in axes:
        axis.legend(frameon=False, fontsize=8)
        axis.margins(x=0)
    _format_week_axis(axes[2], "EUR/MWh", legend=False)
    axes[2].set_ylabel("EUR/MWh")
    figure.suptitle(f"Baseline dispatch reference, {scenario}, {week_title}")
    figure.tight_layout(rect=(0, 0, 1, 0.97))
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(path, dpi=FIGURE_DPI, bbox_inches="tight")
    plt.close(figure)
    return path


def _write_window_summary(
    demand_weeks: dict[str, pd.DataFrame],
    price_week: pd.DataFrame,
    dispatch_week: pd.DataFrame,
    dispatch_scenario: str,
    path: Path,
) -> Path:
    start = dispatch_week.index[0]
    end = dispatch_week.index[-1]
    rows = []
    for scenario, week in demand_weeks.items():
        for column in ("steam_demand_mw", "electricity_demand_mw"):
            rows.append(_summary_row(start, end, column, scenario, week[column]))
    rows.append(
        _summary_row(
            start,
            end,
            "day_ahead_price_eur_mwh",
            "price",
            price_week["day_ahead_price_eur_mwh"],
        )
    )
    rows.append(
        _summary_row(
            start,
            end,
            "grid_import_mw",
            dispatch_scenario,
            dispatch_week["grid_import_mw"],
        )
    )
    rows.append(
        _summary_row(
            start,
            end,
            "grid_export_mw",
            dispatch_scenario,
            dispatch_week["grid_export_mw"],
        )
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def _summary_row(
    start: pd.Timestamp,
    end: pd.Timestamp,
    series: str,
    scenario: str,
    values: pd.Series,
) -> dict[str, object]:
    return {
        "week_start": start.isoformat(),
        "week_end": end.isoformat(),
        "series": series,
        "scenario": scenario,
        "minimum": float(values.min()),
        "mean": float(values.mean()),
        "maximum": float(values.max()),
    }


def _require_same_index(
    left: pd.DataFrame,
    right: pd.DataFrame,
    left_name: str,
    right_name: str,
) -> None:
    if not left.index.equals(right.index):
        raise PlotInputError(
            f"The selected week does not match between {left_name} and {right_name}"
        )


def _week_title(week: pd.DataFrame) -> str:
    start = week.index[0]
    end = week.index[-1]
    return f"{start:%d %b %Y} to {end:%d %b %Y}"


def _wall_clock(frame: pd.DataFrame) -> pd.DataFrame:
    """Drop timezone info while keeping the local clock time for axis labels."""
    plotted = frame.copy()
    plotted.index = plotted.index.tz_localize(None)
    return plotted


def _format_week_axis(axis: plt.Axes, ylabel: str, legend: bool = True) -> None:
    axis.set_ylabel(ylabel)
    axis.set_xlabel("Local time")
    axis.xaxis.set_major_locator(mdates.DayLocator())
    axis.xaxis.set_major_formatter(mdates.DateFormatter("%a %d %b"))
    if legend:
        axis.legend(frameon=False)
    axis.margins(x=0)


def _save(figure: plt.Figure, path: Path) -> None:
    figure.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(path, dpi=FIGURE_DPI, bbox_inches="tight")
    plt.close(figure)


def plot_optimized_dispatch_dashboard(
    dispatch: pd.DataFrame,
    week_start: str,
    path: Path,
    scenario: str = "base",
    mode: str = "full_flex",
) -> Path:
    """Three-panel full-flex dashboard on the same week axis as the baseline figure."""
    week = select_week(dispatch, week_start)
    plotted = _wall_clock(week)
    figure, axes = plt.subplots(3, 1, figsize=(10, 8), sharex=True)
    for column, color in (
        ("steam_demand_mw", STEAM_DISPATCH_COLORS["steam_demand_mw"]),
        ("chp_steam_mw", STEAM_DISPATCH_COLORS["chp_steam_mw"]),
        ("boiler_steam_mw", STEAM_DISPATCH_COLORS["boiler_steam_mw"]),
    ):
        axes[0].plot(plotted.index, plotted[column], color=color, linewidth=1.2, label=SERIES_LABELS[column])
    for column, color in (
        ("electricity_demand_mw", ELECTRICITY_DISPATCH_COLORS["electricity_demand_mw"]),
        ("chp_electricity_mw", ELECTRICITY_DISPATCH_COLORS["chp_electricity_mw"]),
        ("grid_import_mw", ELECTRICITY_DISPATCH_COLORS["grid_import_mw"]),
        ("grid_export_mw", ELECTRICITY_DISPATCH_COLORS["grid_export_mw"]),
    ):
        axes[1].plot(plotted.index, plotted[column], color=color, linewidth=1.2, label=SERIES_LABELS[column])
    axes[2].plot(
        plotted.index,
        plotted["day_ahead_price_eur_mwh"],
        color="#1f77b4",
        linewidth=1.2,
        label="Day-ahead price",
    )
    axes[2].axhline(0.0, color="#666666", linewidth=0.8, linestyle="--", label="0 EUR/MWh")
    axes[0].set_ylabel("MW steam")
    axes[1].set_ylabel("MW electricity")
    axes[0].set_title("Steam balance")
    axes[1].set_title("Electricity balance")
    axes[2].set_title("Day-ahead price")
    for axis in axes:
        axis.legend(frameon=False, fontsize=8)
        axis.margins(x=0)
    _format_week_axis(axes[2], "EUR/MWh", legend=False)
    figure.suptitle(
        f"Optimized dispatch, {scenario}, {mode}, {_week_title(week)}"
    )
    figure.tight_layout(rect=(0, 0, 1, 0.97))
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(path, dpi=FIGURE_DPI, bbox_inches="tight")
    plt.close(figure)
    return path


def plot_baseline_vs_optimized_week(
    baseline: pd.DataFrame,
    optimized: pd.DataFrame,
    break_even_eur_mwh: float,
    week_start: str,
    path: Path,
) -> Path:
    """Show whether optimized CHP and imports move with the day-ahead price."""
    baseline_week = _wall_clock(select_week(baseline, week_start))
    optimized_week = _wall_clock(select_week(optimized, week_start))
    figure, axes = plt.subplots(3, 1, figsize=(10, 8), sharex=True)
    axes[0].plot(
        optimized_week.index,
        optimized_week["steam_demand_mw"],
        color="#222222",
        linewidth=1.2,
        label="Steam demand",
    )
    axes[0].plot(
        baseline_week.index,
        baseline_week["chp_steam_mw"],
        color="#4d4d4d",
        linewidth=1.2,
        linestyle="--",
        label="Baseline CHP steam",
    )
    axes[0].plot(
        optimized_week.index,
        optimized_week["chp_steam_mw"],
        color="#1f77b4",
        linewidth=1.2,
        label="Optimized CHP steam",
    )
    axes[1].plot(
        baseline_week.index,
        baseline_week["grid_import_mw"],
        color="#4d4d4d",
        linewidth=1.2,
        linestyle="--",
        label="Baseline grid import",
    )
    axes[1].plot(
        optimized_week.index,
        optimized_week["grid_import_mw"],
        color="#2ca02c",
        linewidth=1.2,
        label="Optimized grid import",
    )
    axes[1].plot(
        baseline_week.index,
        baseline_week["grid_export_mw"],
        color="#aaaaaa",
        linewidth=1.1,
        linestyle="--",
        label="Baseline grid export",
    )
    axes[1].plot(
        optimized_week.index,
        optimized_week["grid_export_mw"],
        color="#ff7f0e",
        linewidth=1.2,
        label="Optimized grid export",
    )
    axes[2].plot(
        optimized_week.index,
        optimized_week["day_ahead_price_eur_mwh"],
        color="#1f77b4",
        linewidth=1.2,
        label="Day-ahead price",
    )
    axes[2].axhline(
        break_even_eur_mwh,
        color="#666666",
        linewidth=0.9,
        linestyle="--",
        label=f"CHP break-even {break_even_eur_mwh:.1f} EUR/MWh",
    )
    axes[0].set_ylabel("MW steam")
    axes[1].set_ylabel("MW electricity")
    axes[0].set_title("CHP steam")
    axes[1].set_title("Grid import and export")
    axes[2].set_title("Day-ahead price")
    for axis in axes:
        axis.legend(frameon=False, fontsize=8)
        axis.margins(x=0)
    _format_week_axis(axes[2], "EUR/MWh", legend=False)
    figure.suptitle(f"Baseline vs full_flex, base, {_week_title(select_week(optimized, week_start))}")
    figure.tight_layout(rect=(0, 0, 1, 0.97))
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(path, dpi=FIGURE_DPI, bbox_inches="tight")
    plt.close(figure)
    return path


def plot_grid_import_peak_week(
    baseline: pd.DataFrame,
    optimized: pd.DataFrame,
    optimized_peak_import_mw: float,
    week_start: str,
    path: Path,
) -> Path:
    """Baseline and optimized grid import, with the optimized annual peak marked."""
    baseline_week = _wall_clock(select_week(baseline, week_start))
    optimized_week = _wall_clock(select_week(optimized, week_start))
    figure, axis = plt.subplots(figsize=(10, 4))
    axis.plot(
        baseline_week.index,
        baseline_week["grid_import_mw"],
        color="#4d4d4d",
        linewidth=1.2,
        linestyle="--",
        label="Baseline grid import",
    )
    axis.plot(
        optimized_week.index,
        optimized_week["grid_import_mw"],
        color="#2ca02c",
        linewidth=1.2,
        label="Optimized grid import",
    )
    axis.axhline(
        optimized_peak_import_mw,
        color="#d62728",
        linewidth=1.0,
        linestyle=":",
        label=f"Optimized annual peak {optimized_peak_import_mw:.1f} MW",
    )
    axis.set_title(
        f"Grid import vs optimized annual peak, {_week_title(select_week(optimized, week_start))}"
    )
    axis.legend(frameon=False, fontsize=8)
    axis.margins(x=0)
    _format_week_axis(axis, "MW", legend=False)
    figure.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(path, dpi=FIGURE_DPI, bbox_inches="tight")
    plt.close(figure)
    return path


def plot_final_dispatch_week(
    dispatch: pd.DataFrame,
    week_start: str,
    path: Path,
    ramp_mw_per_hour: float | None = None,
    title: str | None = None,
) -> Path:
    """Primary-case week: steam, electricity balance, and the day-ahead price."""
    week = _wall_clock(select_week(dispatch, week_start))
    figure, axes = plt.subplots(3, 1, figsize=(10, 8), sharex=True)
    axes[0].plot(week.index, week["steam_demand_mw"], color="#222222", linewidth=1.2, label="Steam demand")
    axes[0].plot(week.index, week["chp_steam_mw"], color="#1f77b4", linewidth=1.2, label="CHP steam")
    axes[0].plot(week.index, week["boiler_steam_mw"], color="#d62728", linewidth=1.2, label="Boiler steam")
    if ramp_mw_per_hour is not None:
        delta = dispatch["chp_steam_mw"].astype(float).diff()
        binding = delta.abs() >= float(ramp_mw_per_hour) - 0.05
        if bool(binding.fillna(False).any()):
            binding_week = _wall_clock(dispatch.loc[binding.fillna(False)])
            binding_week = binding_week.loc[
                (binding_week.index >= week.index[0]) & (binding_week.index <= week.index[-1])
            ]
            if not binding_week.empty:
                axes[0].scatter(
                    binding_week.index,
                    binding_week["chp_steam_mw"],
                    s=12,
                    color="#222222",
                    zorder=3,
                    label="CHP ramp binding",
                )
    axes[1].plot(
        week.index, week["electricity_demand_mw"], color="#222222", linewidth=1.2, label="Electricity demand"
    )
    axes[1].plot(week.index, week["chp_electricity_mw"], color="#1f77b4", linewidth=1.2, label="CHP electricity")
    axes[1].plot(week.index, week["grid_import_mw"], color="#2ca02c", linewidth=1.2, label="Grid import")
    axes[1].plot(week.index, week["grid_export_mw"], color="#ff7f0e", linewidth=1.2, label="Grid export")
    axes[2].plot(
        week.index, week["day_ahead_price_eur_mwh"], color="#1f77b4", linewidth=1.2, label="Day-ahead price"
    )
    axes[0].set_title("Steam")
    axes[1].set_title("Electricity")
    axes[2].set_title("Day-ahead price")
    axes[0].set_ylabel("MW steam")
    axes[1].set_ylabel("MW electricity")
    for axis in axes:
        axis.legend(frameon=False, fontsize=8)
        axis.margins(x=0)
    _format_week_axis(axes[2], "EUR/MWh", legend=False)
    figure.suptitle(
        title or f"Primary screening dispatch, {_week_title(select_week(dispatch, week_start))}"
    )
    figure.tight_layout(rect=(0, 0, 1, 0.97))
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(path, dpi=FIGURE_DPI, bbox_inches="tight")
    plt.close(figure)
    return path


def plot_supply_stack_week(
    dispatch: pd.DataFrame,
    week_start: str,
    path: Path,
    title: str,
) -> Path:
    """Two-panel supply stack: steam above, electricity below.

    Steam areas are CHP and boiler. Positive electricity areas are supply
    used on site, so their stack meets the demand line. Grid export is drawn
    separately below zero.
    """
    required = (
        "steam_demand_mw",
        "chp_steam_mw",
        "boiler_steam_mw",
        "electricity_demand_mw",
        "chp_electricity_mw",
        "grid_import_mw",
        "grid_export_mw",
    )
    missing = [column for column in required if column not in dispatch.columns]
    if missing:
        raise PlotInputError(f"Supply-stack dispatch is missing columns: {', '.join(missing)}")
    week = _wall_clock(select_week(dispatch, week_start))
    chp_to_site = week["chp_electricity_mw"] - week["grid_export_mw"]
    import_to_site = week["grid_import_mw"]
    on_site = chp_to_site + import_to_site
    gap = (on_site - week["electricity_demand_mw"]).abs().max()
    if float(gap) > 0.05 or bool((chp_to_site < -0.05).any()):
        raise PlotInputError(
            "On-site electricity stack does not meet demand. "
            f"Largest gap is {float(gap):.3f} MW."
        )
    figure, axes = plt.subplots(2, 1, figsize=(10, 6.4), sharex=True)
    steam, electricity = axes
    steam.fill_between(
        week.index, 0.0, week["chp_steam_mw"], color="#08519c", label="CHP steam", linewidth=0
    )
    steam.fill_between(
        week.index,
        week["chp_steam_mw"],
        week["chp_steam_mw"] + week["boiler_steam_mw"],
        color="#9ecae1",
        label="Boiler steam",
        linewidth=0,
    )
    steam.plot(
        week.index,
        week["steam_demand_mw"],
        color="#222222",
        linewidth=1.4,
        label="Steam demand",
        zorder=3,
    )
    electricity.fill_between(
        week.index, 0.0, chp_to_site, color="#08519c", label="CHP to site", linewidth=0
    )
    electricity.fill_between(
        week.index,
        chp_to_site,
        on_site,
        color="#74c476",
        label="Grid import",
        linewidth=0,
    )
    electricity.fill_between(
        week.index,
        0.0,
        -week["grid_export_mw"],
        color="#fd8d3c",
        label="Grid export",
        linewidth=0,
    )
    electricity.plot(
        week.index,
        week["electricity_demand_mw"],
        color="#222222",
        linewidth=1.4,
        label="Electricity demand",
        zorder=3,
    )
    electricity.axhline(0.0, color="#666666", linewidth=0.8)
    steam.set_title("Steam")
    electricity.set_title("Electricity")
    steam.set_ylabel("MW steam")
    legend = {"frameon": False, "fontsize": 8, "loc": "upper left", "bbox_to_anchor": (1.02, 1.0)}
    steam.legend(**legend)
    electricity.legend(**legend)
    for axis in axes:
        axis.margins(x=0)
    _format_week_axis(electricity, "MW electricity", legend=False)
    figure.suptitle(title)
    figure.tight_layout(rect=(0, 0, 1, 0.96))
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(path, dpi=FIGURE_DPI, bbox_inches="tight")
    plt.close(figure)
    return path


def _save(figure: plt.Figure, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.tight_layout()
    figure.savefig(path, dpi=FIGURE_DPI, bbox_inches="tight")
    plt.close(figure)
    return path


def plot_electric_boiler_npv(sizing: pd.DataFrame, k_star: float, path: Path) -> Path:
    figure, axis = plt.subplots(figsize=(8, 4.5))
    axis.plot(sizing["eboiler_capacity_mwth"], sizing["npv_m_eur"], marker="o", color="#1f77b4")
    axis.axhline(0.0, color="#666666", linewidth=0.8)
    axis.axvline(k_star, color="#d62728", linewidth=1.0, label=f"K* = {k_star:g} MW")
    axis.set_xlabel("E-boiler capacity, MW thermal")
    axis.set_ylabel("NPV, M EUR")
    axis.set_title("Electrode boiler NPV by screened size")
    axis.legend(frameon=False)
    return _save(figure, path)


def plot_electric_boiler_operating_value(sizing: pd.DataFrame, path: Path) -> Path:
    figure, axis = plt.subplots(figsize=(8, 4.5))
    axis.plot(
        sizing["eboiler_capacity_mwth"],
        sizing["average_gross_operating_benefit_m_eur_a"],
        marker="o",
        color="#1f77b4",
        label="Average gross operating benefit",
    )
    axis.plot(
        sizing["eboiler_capacity_mwth"],
        sizing["average_net_cashflow_m_eur_a"],
        marker="o",
        color="#d62728",
        label="Average net cashflow after fixed O&M",
    )
    axis.set_xlabel("E-boiler capacity, MW thermal")
    axis.set_ylabel("M EUR/a")
    axis.set_title("Operating value by screened size")
    axis.legend(frameon=False)
    return _save(figure, path)


def plot_electric_boiler_full_load_hours(sizing: pd.DataFrame, path: Path) -> Path:
    positive = sizing[sizing["eboiler_capacity_mwth"] > 0]
    figure, axis = plt.subplots(figsize=(8, 4.5))
    axis.plot(
        positive["eboiler_capacity_mwth"],
        positive["eboiler_full_load_hours"],
        marker="o",
        color="#1f77b4",
    )
    axis.set_xlabel("E-boiler capacity, MW thermal")
    axis.set_ylabel("Full-load hours")
    axis.set_title("E-boiler full-load hours by screened size")
    return _save(figure, path)


def plot_electric_boiler_market_years(
    market: pd.DataFrame,
    k_star: float,
    path: Path,
) -> Path:
    subset = market[market["eboiler_capacity_mwth"] == k_star]
    grouped = subset.groupby("price_shape_year")["gross_operating_benefit_eur"].mean() / 1e6
    figure, axis = plt.subplots(figsize=(8, 4.5))
    axis.bar([str(year) for year in grouped.index], grouped.to_numpy(), color="#1f77b4")
    axis.set_xlabel("Observed day-ahead year")
    axis.set_ylabel("Gross operating benefit, M EUR/a")
    axis.set_title(f"Market-year operating benefit at {k_star:g} MW, mean over the project life")
    return _save(figure, path)


def plot_electric_boiler_dispatch_week(
    dispatch: pd.DataFrame,
    path: Path,
    week_start: str = "2025-01-06",
    title: str = "E-boiler dispatch, 2025 price shape, early January",
) -> Path:
    week = select_week(dispatch, week_start)
    figure, axes = plt.subplots(3, 1, figsize=(10, 8), sharex=True)
    axes[0].plot(week.index, week["steam_demand_mw"], color="#222222", linewidth=1.2, label="Steam demand")
    axes[0].plot(week.index, week["chp_steam_mw"], color="#1f77b4", linewidth=1.2, label="CHP steam")
    axes[0].plot(week.index, week["boiler_steam_mw"], color="#d62728", linewidth=1.2, label="Gas-boiler steam")
    axes[0].plot(
        week.index, week["eboiler_steam_mw"], color="#9467bd", linewidth=1.2, label="E-boiler steam"
    )
    axes[1].plot(
        week.index,
        week["electricity_demand_mw"],
        color="#222222",
        linewidth=1.2,
        label="Site electricity demand",
    )
    axes[1].plot(week.index, week["chp_electricity_mw"], color="#1f77b4", linewidth=1.2, label="CHP electricity")
    axes[1].plot(
        week.index,
        week["eboiler_electricity_mw"],
        color="#9467bd",
        linewidth=1.2,
        label="E-boiler electricity",
    )
    axes[1].plot(week.index, week["grid_import_mw"], color="#2ca02c", linewidth=1.2, label="Grid import")
    axes[1].plot(week.index, week["grid_export_mw"], color="#ff7f0e", linewidth=1.2, label="Grid export")
    axes[2].plot(
        week.index, week["day_ahead_price_eur_mwh"], color="#1f77b4", linewidth=1.2, label="Day-ahead price"
    )
    axes[0].set_ylabel("MW steam")
    axes[1].set_ylabel("MW electricity")
    axes[2].set_ylabel("EUR/MWh")
    for axis in axes:
        axis.legend(frameon=False, fontsize=8)
        axis.margins(x=0)
    figure.suptitle(title)
    return _save(figure, path)


def plot_electric_boiler_cashflow(cashflows: pd.DataFrame, k_star: float, path: Path) -> Path:
    subset = cashflows[cashflows["capacity_mwth"] == k_star].sort_values("year")
    labels = ["CAPEX" if int(year) == 0 else str(int(year)) for year in subset["year"]]
    figure, axis = plt.subplots(figsize=(9, 4.5))
    axis.plot(labels, subset["cumulative_cashflow_eur"] / 1e6, marker="o", label="Undiscounted")
    axis.plot(
        labels,
        subset["cumulative_discounted_cashflow_eur"] / 1e6,
        marker="o",
        label="Discounted",
    )
    axis.axhline(0.0, color="#666666", linewidth=0.8)
    axis.set_ylabel("Cumulative cashflow, M EUR")
    axis.set_title(f"Cumulative cashflow at {k_star:g} MW")
    axis.tick_params(axis="x", labelrotation=45)
    axis.legend(frameon=False)
    return _save(figure, path)


def plot_npv_redispatch_vs_full_flex(comparison: pd.DataFrame, path: Path) -> Path:
    figure, axis = plt.subplots(figsize=(8, 4.5))
    axis.plot(
        comparison["capacity_mwth"],
        comparison["redispatch_only_npv_m_eur"],
        marker="o",
        color="#ff7f0e",
        label="Redispatch only",
    )
    axis.plot(
        comparison["capacity_mwth"],
        comparison["full_flex_npv_m_eur"],
        marker="o",
        color="#1f77b4",
        label="Full flex",
    )
    axis.axhline(0.0, color="#666666", linewidth=0.8)
    axis.set_xlabel("E-boiler capacity, MW thermal")
    axis.set_ylabel("NPV, M EUR")
    axis.set_title("E-boiler NPV: fixed annual CHP versus full flexibility")
    axis.legend(frameon=False)
    return _save(figure, path)


def plot_eboiler_npv_scenarios(frame: pd.DataFrame, path: Path) -> Path:
    figure, axis = plt.subplots(figsize=(8, 4.5))
    colors = {
        "current": "#7f7f7f",
        "low": "#2ca02c",
        "mid": "#1f77b4",
        "high": "#d62728",
    }
    for scenario, subset in frame.groupby("scenario", sort=False):
        ordered = subset.sort_values("capacity_mw")
        axis.plot(
            ordered["capacity_mw"],
            ordered["npv_m_eur"],
            marker="o",
            color=colors.get(str(scenario), "#333333"),
            label=str(scenario),
        )
    axis.axhline(0.0, color="#666666", linewidth=0.8)
    axis.set_xlabel("E-boiler capacity, MW thermal")
    axis.set_ylabel("NPV, M EUR")
    axis.set_title("E-boiler NPV under fuel-cost scenarios")
    axis.legend(frameon=False)
    return _save(figure, path)


def plot_eboiler_benefit_path(frame: pd.DataFrame, path: Path, title: str) -> Path:
    figure, axis = plt.subplots(figsize=(8, 4.5))
    axis.plot(frame["year"], frame["gross_benefit_m_eur"], marker="o", label="Gross benefit")
    axis.plot(frame["year"], frame["net_cashflow_m_eur"], marker="o", label="Net cashflow")
    axis.axhline(0.0, color="#666666", linewidth=0.8)
    axis.set_xlabel("Year")
    axis.set_ylabel("M EUR/a")
    axis.set_title(title)
    axis.legend(frameon=False)
    return _save(figure, path)

