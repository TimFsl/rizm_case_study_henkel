# Henkel Düsseldorf-Holthausen Energy Screening

This repository contains a pre-onsite energy screening model for the Düsseldorf-Holthausen industrial site.

The model is deliberately simplified and should not be interpreted as a digital twin. Public technical information for the site is mainly available at industrial-site level, while the requested economic metric is EUR/t of Henkel production. Therefore, the reported EUR/t values are site-level screening values normalized by an estimated Henkel production volume of 455,000 t/a. They are not realized or attributable Henkel savings.

## Start here

1. `CASE_STUDY.md` contains the final case-study report.
2. `docs/reporting_pack.md` contains the frozen factual results used in the report.
3. `docs/source_register.md` documents public sources, assumptions and source gaps.
4. `docs/final_case_results.md` summarizes the final model results.
5. `config/assumptions.yaml` contains the numerical model assumptions.

## Business cases

### Business Case 1: Operational dispatch optimization

The first case evaluates whether the existing CHP, boiler and grid system could be operated differently across hours while keeping annual CHP production fixed.

The primary screening case uses:

- the base synthetic demand profile;
- `redispatch_only`;
- a 10 MW export limit;
- a 50% of CHP heat capacity per hour ramp limit;
- 2025 DE/LU Day-Ahead electricity prices;
- the 2025 Düsseldorf high-voltage tariff proxy.

Primary result:

- annual screening value: EUR 1.986 million/a;
- normalized value: EUR 4.366/t.

The authoritative table is:

`outputs/tables/dispatch_export_sensitivity.csv`

The unconstrained-export result of approximately EUR 7.18/t is a sensitivity case, not the primary result.

### Business Case 2: Electrode boiler investment

The second case evaluates an electrode boiler as an additional flexible steam asset.

The investment starts in 2026 and therefore uses the separate 2026 Düsseldorf high-voltage tariff assumptions. Historical 2023, 2024 and 2025 electricity-price shapes are used as empirical market shapes. Forward fossil-gas and carbon-cost pathways are screening scenarios, not forecasts of future hourly electricity prices.

The main Forward Mid result among the tested capacities is:

- 20 MW electrode boiler;
- NPV: EUR 1.460 million;
- average annual gross operating savings: EUR 0.870 million/a;
- gross operating savings: EUR 1.912/t;
- annualized investment value: EUR 0.422/t.

This is the highest NPV among the tested capacities. It is not a recommended Henkel investment size.

Authoritative tables are:

- `outputs/tables/electric_boiler_current_vs_forward.csv`
- `outputs/tables/electric_boiler_forward_cost_anchors.csv`
- `outputs/tables/electric_boiler_forward_npv.csv`
- `outputs/tables/electric_boiler_report_current_cost.csv`
- `outputs/tables/electric_boiler_report_forward_mid.csv`

## Important modeling assumptions

The physical model represents the wider Holthausen industrial energy system rather than a clean Henkel-only boundary.

Main working assumptions include:

- annual steam demand: approximately 1,040 GWh;
- annual electricity demand: approximately 290 GWh;
- Henkel production denominator: 455,000 t/a;
- CHP power-to-heat ratio: 0.50;
- CHP total efficiency: 86%;
- boiler efficiency: 90%;
- grid import capacity reference: 64 MW;
- normal-operation fuel mix: 68% fossil natural gas and 32% biomethane, with coal set to 0%.

The fuel split is a screening assumption, not a verified current Henkel fuel mix. Assumption confidence and rationale are documented in `config/assumptions.yaml` and `docs/source_register.md`.

## Reproduce the main results

Install the dependencies:

`pip install -r requirements.txt`

Run the test suite:

`pytest`

Recreate the Business Case 1 export sensitivity:

`python scripts/run_export_sensitivity.py`

Recreate the Business Case 2 forward-cost screen:

`python scripts/run_electric_boiler_forward_cost.py`

Create the final figures:

`python scripts/plot_final_figures.py`

Additional scripts used for the final report are available under `scripts/`.

The frozen CSV files in `outputs/tables/` are the authoritative submission results. Some scripts regenerate only subsets of these results, so a newly generated file should not automatically be treated as the complete frozen reporting set.