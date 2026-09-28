"""Ingest 2025 DE/LU day-ahead prices and map them onto the model year.

The physical model uses a synthetic 2026 hour index. The price series is
the 2025 SMARD sequence in time order, assigned one-to-one to those model
hours. The stored model timestamp is therefore not an actual 2026 price.

DST rule, applied when localizing SMARD civil times to Europe/Berlin:
the spring-forward hour is absent in the file and is not invented. On the
autumn fall-back, SMARD repeats the same wall-clock hour. The first copy
is localized as CEST and the second as CET, which keeps both prices.
Nothing is dropped, averaged, or silently shifted.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from src.profiles import build_hourly_index

# External reasonableness check only. The series is never rescaled to this.
REFERENCE_ANNUAL_AVERAGE_EUR_MWH = 89.0
REFERENCE_AVERAGE_TOLERANCE_EUR_MWH = 15.0

SOURCE_TIMEZONE = "Europe/Berlin"


class MarketPriceError(ValueError):
    """Raised when a day-ahead file cannot be parsed or aligned."""


def smard_csv_for_year(raw_dir: Path, year: int) -> Path:
    """Return the local SMARD DE/LU day-ahead file for one calendar year."""
    path = Path(raw_dir) / f"smard_day_ahead_de_lu_{int(year)}.csv"
    if not path.is_file():
        raise MarketPriceError(
            f"SMARD day-ahead file for {int(year)} was not found at {path}"
        )
    return path


def find_smard_csv(raw_dir: Path) -> Path:
    """Return the single SMARD day-ahead CSV in a directory."""
    matches = []
    if not raw_dir.is_dir():
        raise MarketPriceError(f"Raw data directory does not exist: {raw_dir}")
    for path in sorted(raw_dir.glob("*.csv")):
        header = _read_header(path)
        if _looks_like_smard_header(header):
            matches.append(path)
    if len(matches) == 0:
        raise MarketPriceError(
            f"No SMARD day-ahead CSV found in {raw_dir}. "
            "Place the Bundesnetzagentur / SMARD Germany/Luxembourg file there."
        )
    if len(matches) > 1:
        names = ", ".join(path.name for path in matches)
        raise MarketPriceError(
            f"Expected exactly one SMARD day-ahead CSV in {raw_dir}; found {names}"
        )
    return matches[0]


def parse_smard_day_ahead(path: Path) -> pd.DataFrame:
    """Parse one SMARD file into a timezone-aware  price series.

    The index is the localized start of each source hour.
    """
    header = _read_header(path)
    separator = ";" if ";" in header else ","
    frame = pd.read_csv(path, sep=separator, encoding="utf-8-sig", dtype=str)
    start_column = _start_column(frame.columns)
    price_column = _price_column(frame.columns)
    naive = _parse_local_timestamps(frame[start_column])
    localized = _localize_civil_times(naive, SOURCE_TIMEZONE)
    prices = frame[price_column].map(parse_decimal)
    parsed = pd.DataFrame(
        {"day_ahead_price_eur_mwh": prices.to_numpy(dtype=float)},
        index=pd.DatetimeIndex(localized, name="source_timestamp"),
    )
    parsed = parsed.sort_index()
    if parsed.index.has_duplicates:
        raise MarketPriceError(f"{path.name} contains duplicate localized timestamps")
    if parsed["day_ahead_price_eur_mwh"].isna().any():
        raise MarketPriceError(f"{path.name} contains prices that could not be parsed")
    return parsed


def align_prices_to_model(
    source_prices: pd.DataFrame,
    assumptions: Any,
) -> pd.DataFrame:
    """Assign the sorted source sequence onto the model-year hour index."""
    model_year = int(_value(assumptions, "model", "model_year"))
    timezone = str(_value(assumptions, "model", "timezone"))
    source_year = int(_value(assumptions, "market", "representative_market_year"))
    model_index = build_hourly_index(model_year, timezone)
    if len(source_prices) != len(model_index):
        raise MarketPriceError(
            "Day-ahead series has "
            f"{len(source_prices)} hours and the {model_year} model index has "
            f"{len(model_index)}. The representative-year mapping needs equal "
            "lengths, so no hours were dropped or duplicated."
        )
    adder = float(_value(assumptions, "market", "electricity_import_adder_eur_per_mwh"))
    discount = float(_value(assumptions, "market", "electricity_export_discount_eur_per_mwh"))
    day_ahead = source_prices["day_ahead_price_eur_mwh"].to_numpy(dtype=float)
    aligned = pd.DataFrame(
        {
            "source_timestamp": source_prices.index.to_numpy(),
            "source_market_year": source_year,
            "day_ahead_price_eur_mwh": day_ahead,
            "electricity_import_price_eur_mwh": day_ahead + adder,
            "electricity_export_price_eur_mwh": day_ahead - discount,
        },
        index=model_index,
    )
    aligned.index.name = "timestamp"
    return aligned


def price_qa_statistics(prices: pd.DataFrame) -> dict[str, float]:
    series = prices["day_ahead_price_eur_mwh"]
    return {
        "hours": float(len(series)),
        "mean": float(series.mean()),
        "median": float(series.median()),
        "minimum": float(series.min()),
        "maximum": float(series.max()),
        "negative_price_hours": float((series < 0).sum()),
    }


def reference_average_warning(mean_price: float) -> str | None:
    gap = abs(mean_price - REFERENCE_ANNUAL_AVERAGE_EUR_MWH)
    if gap <= REFERENCE_AVERAGE_TOLERANCE_EUR_MWH:
        return None
    return (
        "WARNING: parsed day-ahead average is "
        f"{mean_price:.2f} EUR/MWh, which is outside "
        f"{REFERENCE_ANNUAL_AVERAGE_EUR_MWH:.0f} ± "
        f"{REFERENCE_AVERAGE_TOLERANCE_EUR_MWH:.0f} EUR/MWh. "
        "Inspect the SMARD column and decimal parsing. The series was not rescaled."
    )


def parse_decimal(text: str) -> float:
    """Accept SMARD dots and German decimal commas. '-' is missing data."""
    value = str(text).strip().replace(" ", "")
    if value in {"", "-", "–", "—", "nan", "None"}:
        return float("nan")
    if "," in value and "." in value:
        if value.rfind(",") > value.rfind("."):
            value = value.replace(".", "").replace(",", ".")
        else:
            value = value.replace(",", "")
    elif "," in value:
        value = value.replace(",", ".")
    return float(value)


def _looks_like_smard_header(header: str) -> bool:
    lowered = header.lower()
    has_price = "€/mwh" in lowered or "eur/mwh" in lowered
    has_zone = "germany/luxembourg" in lowered or "deutschland/luxemburg" in lowered
    return has_price and has_zone


def _read_header(path: Path) -> str:
    with path.open(encoding="utf-8-sig") as handle:
        return handle.readline()


def _start_column(columns: pd.Index) -> str:
    for column in columns:
        lowered = str(column).lower()
        if "start" in lowered or "beginn" in lowered or lowered.startswith("datum von"):
            return str(column)
    raise MarketPriceError(
        "SMARD file has no start-time column. Columns: " + ", ".join(map(str, columns))
    )


def _price_column(columns: pd.Index) -> str:
    for column in columns:
        lowered = str(column).lower()
        if "neighbour" in lowered or "nachbar" in lowered:
            continue
        has_price = "€/mwh" in lowered or "eur/mwh" in lowered
        has_zone = "germany/luxembourg" in lowered or "deutschland/luxemburg" in lowered
        if has_price and has_zone:
            return str(column)
    raise MarketPriceError(
        "SMARD file has no Germany/Luxembourg day-ahead price column. Columns: "
        + ", ".join(map(str, columns))
    )


def _parse_local_timestamps(series: pd.Series) -> pd.Series:
    parsed = pd.to_datetime(series, format="%b %d, %Y %I:%M %p", errors="coerce")
    if parsed.isna().any():
        alternative = pd.to_datetime(series, dayfirst=True, errors="coerce")
        parsed = parsed.fillna(alternative)
    if parsed.isna().any():
        raise MarketPriceError("Some SMARD timestamps could not be parsed")
    return parsed


def _localize_civil_times(naive: pd.Series, timezone: str) -> pd.DatetimeIndex:
    """Localize civil times, keeping both copies of a repeated fall-back hour."""
    is_second_copy = naive.duplicated(keep="first")
    # True means the DST occurrence. The first SMARD copy is still on summer time.
    ambiguous = ~is_second_copy.to_numpy()
    try:
        localized = naive.dt.tz_localize(
            timezone,
            ambiguous=ambiguous,
            nonexistent="raise",
        )
    except Exception as exc:
        raise MarketPriceError(
            "Could not localize SMARD civil times without dropping an hour"
        ) from exc
    return pd.DatetimeIndex(localized)


def _value(assumptions: Any, section: str, key: str):
    if hasattr(assumptions, "value"):
        return assumptions.value(section, key)
    return assumptions[section][key]["value"]
