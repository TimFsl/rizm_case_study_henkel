#!/usr/bin/env python3
"""Parse the SMARD day-ahead file and map it onto the model-year hours."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.assumptions import AssumptionError, load_assumptions
from src.market_prices import (
    MarketPriceError,
    align_prices_to_model,
    find_smard_csv,
    parse_smard_day_ahead,
    price_qa_statistics,
    reference_average_warning,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        default=None,
        help="SMARD day-ahead CSV. If omitted, data/raw must contain exactly one.",
    )
    args = parser.parse_args()
    try:
        assumptions = load_assumptions()
        preferred = ROOT / "data" / "raw" / "smard_day_ahead_de_lu_2025.csv"
        if args.input is not None:
            source = args.input
        elif preferred.is_file():
            source = preferred
        else:
            source = find_smard_csv(ROOT / "data" / "raw")
        parsed = parse_smard_day_ahead(source)
        aligned = align_prices_to_model(parsed, assumptions)
    except (AssumptionError, MarketPriceError) as exc:
        print(f"Market price build failed: {exc}", file=sys.stderr)
        return 1

    output = ROOT / "data" / "processed" / "electricity_day_ahead_prices.csv"
    output.parent.mkdir(parents=True, exist_ok=True)
    aligned.reset_index().to_csv(output, index=False)

    stats = price_qa_statistics(aligned)
    print(f"Source file: {source.name}")
    print("Mapping: 2025 SMARD hours in time order -> 2026 model hours")
    print("These timestamps are a representative market year, not actual 2026 prices.")
    print(f"Hours:    {stats['hours']:.0f}")
    print(f"Mean:     {stats['mean']:.2f} EUR/MWh")
    print(f"Median:   {stats['median']:.2f} EUR/MWh")
    print(f"Min:      {stats['minimum']:.2f} EUR/MWh")
    print(f"Max:      {stats['maximum']:.2f} EUR/MWh")
    print(f"Negative-price hours: {stats['negative_price_hours']:.0f}")
    warning = reference_average_warning(stats["mean"])
    if warning:
        print(warning)
    else:
        print("Annual average is in the expected neighborhood of about 89 EUR/MWh.")
    print(f"Wrote {output.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
