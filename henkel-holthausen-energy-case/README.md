# Henkel Düsseldorf-Holthausen energy screening model

Pre-onsite screening model for the Holthausen industrial site. It is not a digital twin. Results are site-level screening values divided by 455,000 t/a. They are not realized Henkel savings.

## Start here

1. `CASE_STUDY.md` — final narrative submission (may not exist yet)
2. `docs/reporting_pack.md` — frozen factual results
3. `docs/source_register.md` — source provenance
4. `docs/final_case_results.md` — model result note

Authoritative tables:

- `outputs/tables/dispatch_export_sensitivity.csv` — Business Case 1. The primary row is `primary_screening`: 1.986404437 M EUR/a, 4.365724038 EUR/t.
- `outputs/tables/electric_boiler_current_vs_forward.csv` — Business Case 2 current-cost NPV.
- `outputs/tables/electric_boiler_forward_npv.csv` — Business Case 2 forward fossil-cost NPV, including 30 MW on the MID path only.

`outputs/tables/dispatch_final_sensitivity.csv` and `outputs/tables/electric_boiler_sizing.csv` are intermediate screens. The first does not apply the 10 MW export cap. The second is the earlier redispatch-only investment screen, not the frozen electrode-boiler result.

The electricity-price shapes are historical observations, while the fuel/carbon paths are forward-looking screening scenarios. This is a scenario investment model, not a forecast of future hourly electricity prices. Business Case 1 uses the 2025 Düsseldorf high-voltage proxy in `config/assumptions.yaml`. Business Case 2 holds the 2026 tariff constant in real terms (TAR26).

Longer model notes remain in `AGENTS.md`.

## Reproduce

Frozen tables are the submission results. These commands regenerate the screens; they do not change the written assumptions.

```bash
python scripts/run_export_sensitivity.py
python scripts/run_electric_boiler_forward_cost.py
python scripts/plot_final_figures.py
pytest
```

`run_electric_boiler_forward_cost.py` rewrites the 5, 10, and 20 MW forward rows. The stored 30 MW MID rows are from a separate frozen check and are not produced by that command. Do not treat a fresh 5/10/20 file as complete if the 30 MW rows are missing.

`run_final_sensitivity.py` is the earlier ramp screen, not the frozen Business Case 1 primary.

## Repository structure

- `src/` — assumptions loader, profiles, baseline, costs, Pyomo dispatch, investment metrics, and plots
- `scripts/` — reproducible runs and the final-figure script
- `data/` — SMARD prices and processed profiles
- `outputs/` — tables and figures
- `docs/` — results, reporting pack, and source register
- `tests/` — pytest suite
- `config/assumptions.yaml` — numerical assumptions

## Toolchain

Python, pandas, NumPy, PyYAML, Pyomo, HiGHS, matplotlib, pytest, and SMARD day-ahead files. Cursor was used to implement and document the model. Hourly optimization is solved by HiGHS.
