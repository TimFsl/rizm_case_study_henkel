#!/usr/bin/env python3
"""Write QA figures from the processed demand, price, and dispatch files."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.plotting import (  # noqa: E402
    DEFAULT_DISPATCH_SCENARIO,
    DEFAULT_WEEK_START,
    SCENARIOS,
    PlotInputError,
    generate_figures,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Plot synthetic demand, day-ahead prices, and baseline dispatch."
    )
    parser.add_argument(
        "--week-start",
        default=DEFAULT_WEEK_START,
        help=f"First local hour of the plotted week (default: {DEFAULT_WEEK_START})",
    )
    parser.add_argument(
        "--dispatch-scenario",
        default=DEFAULT_DISPATCH_SCENARIO,
        choices=SCENARIOS,
        help=f"Baseline dispatch scenario (default: {DEFAULT_DISPATCH_SCENARIO})",
    )
    args = parser.parse_args()
    try:
        paths = generate_figures(ROOT, args.week_start, args.dispatch_scenario)
    except PlotInputError as exc:
        print(f"Figure generation failed: {exc}", file=sys.stderr)
        return 1
    print(f"Week starting {args.week_start}, dispatch scenario {args.dispatch_scenario}")
    for path in paths:
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
