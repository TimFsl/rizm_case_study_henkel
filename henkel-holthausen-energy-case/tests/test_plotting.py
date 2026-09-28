"""Tests for the QA plotting layer."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.plotting import (  # noqa: E402
    DEFAULT_WEEK_START,
    PlotInputError,
    generate_figures,
    load_hourly_csv,
    resolve_processed_csv,
    select_week,
)


def test_default_week_covers_seven_local_days():
    path = resolve_processed_csv(ROOT, "demand_profile_base.csv", ("demand_profile", "base"))
    week = select_week(
        load_hourly_csv(path, ("steam_demand_mw",), "Europe/Berlin"),
        DEFAULT_WEEK_START,
    )
    assert len(week) == 168
    assert week.index[0].isoformat().startswith("2026-01-06")
    assert week.index[-1].isoformat().startswith("2026-01-12T23:00:00")


def test_missing_processed_file_names_the_expected_csv(tmp_path: Path):
    (tmp_path / "data" / "processed").mkdir(parents=True)
    with pytest.raises(PlotInputError, match="demand_profile_flat.csv"):
        resolve_processed_csv(tmp_path, "demand_profile_flat.csv", ("demand_profile", "flat"))


def test_generate_figures_writes_the_qa_set(tmp_path: Path):
    figures = tmp_path / "figures"
    tables = tmp_path / "tables"
    paths = generate_figures(
        ROOT,
        DEFAULT_WEEK_START,
        "base",
        figures_dir=figures,
        tables_dir=tables,
    )
    assert {path.name for path in paths} == {
        "demand_steam_week.png",
        "demand_electricity_week.png",
        "electricity_price_week.png",
        "electricity_price_year.png",
        "baseline_dispatch_steam_week.png",
        "baseline_dispatch_electricity_week.png",
        "baseline_dispatch_dashboard_week.png",
        "plot_window_summary.csv",
    }
    summary = pd.read_csv(tables / "plot_window_summary.csv")
    assert set(summary["series"]) >= {
        "steam_demand_mw",
        "electricity_demand_mw",
        "day_ahead_price_eur_mwh",
        "grid_import_mw",
        "grid_export_mw",
    }
    assert summary["week_start"].str.startswith("2026-01-06").all()
