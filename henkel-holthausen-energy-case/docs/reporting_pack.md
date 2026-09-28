# Final reporting pack

Factual sheet for the submission. It does not replace the narrative case study. EUR/t figures are site-level screening values divided by 455,000 t/a. They are not realized Henkel savings.

The electricity-price shapes are historical observations, while the fuel/carbon paths are forward-looking screening scenarios. This is a scenario investment model, not a forecast of future hourly electricity prices.

## 1. Site working model

- Boundary: one aggregated site system. Henkel and third parties are not split. Modeling assumption.
- Steam demand: 1,040 GWh/a. Derived estimate from historical steam production, not a 2026 measurement.
- Electricity demand: 290 GWh/a. Derived estimate from the steam total and a 78/22 steam/electricity screening split.
- CHP: one block, 110 MW heat, power-to-heat ratio 0.50, total utilization efficiency 0.86. Modeling assumptions, checked against historical generation magnitude.
- Gas boiler: one block, 100 MW heat, thermal efficiency 0.90. Modeling assumption.
- Grid import capacity: 64 MW. Public StoREN scenario connection, not a verified present contract.
- Export assumption: 10 MW in the primary case. Modeling assumption. Public information shows export is possible; the contract was not found. Sensitivity 5 / 20 MW and unconstrained.
- Fuel mix: coal phased out in 2024. Normal operation is fossil natural gas plus biomethane. Backup light fuel oil is excluded from the normal dispatch. Screening split: 68% fossil natural gas, 32% biomethane, 0% coal, from a late-2023 statement of about 80% gas in total, of which about 40% was biogenic. Modeling assumption, not a verified 2026 site-specific fuel split.
- Normalization: 455,000 t/a Henkel production. Derived denominator only. It does not allocate site energy.
- Profiles: flat, base, and variable synthetic shapes with the same annual totals. Modeling assumption. The base profile is the primary case. They are not measured Holthausen operation.

Source IDs used around the investment and market layer: MKT01, GAS01, CO201, CO202, TAR26, EB01, EB02, EB04, FIN01, HEN01. BIO01 is `TODO_VERIFY` and is not the source of the forward fuel paths.

## 2. Business Case 1 — frozen result

Decision question: what is the annual site-level screening value of moving the existing CHP/boiler/grid dispatch with electricity price, relative to the calibrated rule-based dispatch?

Logic: each hour still meets steam and electricity demand. Redispatch keeps annual CHP electricity at the calibrated total and chooses the hourly CHP/boiler split. Fuel cost, import cost, and export revenue form the objective. A 50%/h CHP ramp and a 10 MW export cap apply. The Düsseldorf high-voltage tariff is the 2025 proxy in `config/assumptions.yaml`. The reported value uses the tariff regime the solve actually matches. There is no CAPEX.

Primary result, from `outputs/tables/dispatch_export_sensitivity.csv`, row `primary_screening`:

- 1.986404437 M EUR/a
- 4.365724038 EUR/t

Export sensitivity, redispatch only, same high-utilization regime as that primary row:

| Export capacity | M EUR/a | EUR/t |
|---|---:|---:|
| 5 MW | 1.280993399 | 2.815370108 |
| 10 MW | 1.986404437 | 4.365724038 |
| 20 MW | 3.163438106 | 6.952611222 |
| Unconstrained | 3.268634747 | 7.183812631 |

Physical reading: annual CHP electricity stays near 270.4 GWh. The value is timing of that existing production, not a new asset. A wider export limit raises the screening value because more high-price CHP generation can leave the site.

What could make it wrong: the contracted export limit, the billed tariff and peak, 15-minute dispatch, and unit constraints that the hourly aggregate model does not have.

`outputs/tables/dispatch_final_sensitivity.csv` is an earlier ramp screen. Its `primary_screening` label is about 7.184 EUR/t and does not apply the 10 MW export cap. It is not the frozen primary.

## 3. Business Case 2 — frozen result

Decision question: what electrode-boiler capacity, if any, has a positive screening NPV once the existing plant can re-optimize?

Current-cost screening: full flexibility with no boiler versus full flexibility with the boiler. Electricity shapes are the observed 2023, 2024, and 2025 day-ahead years (MKT01). Fuel and EUA stay at the 2026 stack, pre-carbon gas 40 EUR/MWh and EUA 80 EUR/t. The 2026 Düsseldorf high-voltage tariff (TAR26) is held constant in real terms. The mean benefit is held flat for 15 years. CAPEX is 200,000 EUR/MW, fixed operating cost is 1.7% of CAPEX, the real discount rate is 10%, and life is 15 years.

| Capacity | Mean gross benefit | NPV | Simple payback | Discounted payback |
|---|---:|---:|---:|---|
| 5 MW | 154,642 EUR/a | +0.046914 M EUR | 7.3 years | 13.6 years |
| 10 MW | 294,509 EUR/a | −0.018547 M EUR | 7.7 years | not reached |
| 20 MW | 513,931 EUR/a | −0.608210 M EUR | 9.0 years | not reached |

Under current cost conditions only small flexible power-to-heat capacity is around economic break-even. No current-cost result was calculated at 30 MW.

Forward fossil-cost scenario: the same hourly shapes and the same 2026 tariff. Only the fuel stack changes. Hourly solves are at 2026, 2030, and 2040. Years between those anchors are linear. LOW and HIGH are sensitivities. MID is the primary forward path. Thirty megawatts was solved on MID only.

| Path | 2026 gas / EUA | 2030 gas / EUA | 2040 gas / EUA |
|---|---|---|---|
| Low | 40 EUR/MWh, 80 EUR/t | 30 EUR/MWh, 80 EUR/t | 30 EUR/MWh, 80 EUR/t |
| Mid | 40 EUR/MWh, 80 EUR/t | 40 EUR/MWh, 110 EUR/t | 40 EUR/MWh, 150 EUR/t |
| High | 40 EUR/MWh, 80 EUR/t | 50 EUR/MWh, 140 EUR/t | 50 EUR/MWh, 300 EUR/t |

Effective fossil cost is pre-carbon gas plus 0.2016 tCO2/MWh times the EUA price. Biomethane is pre-carbon gas plus the existing 15 EUR/MWh premium, with no EUA.

| Capacity | Low NPV | Mid NPV | High NPV | Mid IRR | Mid simple payback | Mid discounted payback | Mid EAV EUR/t |
|---|---:|---:|---:|---:|---:|---:|---:|
| 5 MW | −0.289 M EUR | +0.779 M EUR | +3.132 M EUR | 19.4% | 5.7 years | 8.0 years | +0.225 |
| 10 MW | −0.651 M EUR | +1.211 M EUR | +6.080 M EUR | 17.8% | 6.0 years | 8.6 years | +0.350 |
| 20 MW | −1.710 M EUR | +1.460 M EUR | +10.614 M EUR | 15.0% | 6.7 years | 10.1 years | +0.422 |
| 30 MW | not run | +1.034 M EUR | not run | 12.4% | 7.5 years | 12.2 years | +0.299 |

20 MW is the screening NPV optimum among the tested capacities. It is not a recommended Henkel investment.

Equivalent Annual Value (EAV) converts project NPV into an equivalent constant annual economic value over the investment lifetime:

EAV = NPV / PV annuity factor

EAV EUR/t = EAV / 455,000 t/a

Gross operating savings in EUR/t ignore investment CAPEX. Annualized investment value (EAV) in EUR/t includes CAPEX, fixed O&M, the full annual cash-flow path, and discounting. EAV EUR/t is the primary EUR/t metric for comparing the electrode-boiler investment with other business cases.

Report-facing tables, calculated from the frozen benefits without a new dispatch solve:

- `outputs/tables/electric_boiler_report_current_cost.csv`
- `outputs/tables/electric_boiler_report_forward_mid.csv`
- `outputs/figures/final_bc2_dcf_comparison.png` — 20 MW current-cost cash flow beside the MID forward cash flow

On the MID path at 20 MW, gross benefit is 513,931 EUR in 2026, 735,277 EUR in 2030, and 1,203,610 EUR in 2040. Installed size is 16.8% of the about 119 MW average steam load. The 15-year average steam share is 3.4% of 1,040 GWh. It is a flexible power-to-heat slice, not site-wide electrification. In 2026 and 2030 it mainly displaces CHP steam. By 2040 the displacement is a mixture of CHP and gas-boiler steam. Peak import rises from about 25 MW to about 52 MW.

`outputs/tables/electric_boiler_sizing.csv` is the earlier redispatch-only screen. Every positive size there has a negative NPV. That file is not the current-cost or forward result.

## 4. Shortlist / cases not developed

No further case was implemented.

- Spray-dryer exhaust heat recovery. This repository has no model and no source-register entry for a Düsseldorf recoverable-heat quantity. It is an undeveloped concept only.
- High-temperature heat pump / low-grade waste-heat upgrading. Listed in `AGENTS.md` as a later case. It was not modeled. The commissioned district-heating stream must not be treated as free waste heat.
- No other business case in the repository was taken past that shortlist.

## 5. Candidate onsite validation

This list does not choose the first data request.

| Missing item | Assumption it replaces | Case | Decision impact |
|---|---|---|---|
| Contracted export capacity | 10 MW export cap | BC1, and BC2 full-flex dispatch | High |
| Billed network tariff and billed peak | 2025 HV proxy in BC1; 2026 HV tariff held flat in BC2 | Both | High |
| Steam header and pressure level for an electrode boiler | Single steam bus; boiler can connect | BC2 | High |
| Vendor-integrated CAPEX | 200,000 EUR/MW literature allowance | BC2 | High |
| Unit CHP limits: minimum load, startup, real ramp | Hourly aggregate, 50%/h ramp, no commitment | BC1 | Medium |
| Measured hourly steam and electricity profiles | Synthetic base profile | Both | Medium |
| Current fuel mix and certified biomethane share | 68/32 screening mix | BC2 fuel cost | Medium |
| Henkel versus third-party allocation | Site total divided by 455,000 t | How EUR/t is read | Medium |

## 6. Toolchain factual inventory

- Python, pandas, NumPy, and PyYAML: data handling and assumptions.
- Pyomo with HiGHS (`highspy`): hourly linear optimization.
- matplotlib: figures.
- SMARD day-ahead files: observed 2023, 2024, and 2025 prices (MKT01).
- pytest: tests.
- Cursor: research and reasoning assistance, code implementation, plotting scripts, and documentation. The optimizer is HiGHS, not the assistant.

ChatGPT is not a dependency of this repository and is not invoked by the scripts.

## 7. Final figure index

- `outputs/figures/final_bc1_baseline_week_stack.png` — baseline supply stack for the representative week. Steam demand is the line. CHP and boiler steam are stacked areas. Above zero, electricity is supply used on site and meets the demand line. Grid export is below zero.
- `outputs/figures/final_bc1_redispatch_week_stack.png` — same layout for the frozen primary redispatch case: base demand, redispatch only, 10 MW export, 50%/h CHP ramp, existing high-voltage tariff. The plotted year matches the frozen 1.986404437 M EUR/a value. It does not replace the result table.
- `outputs/figures/final_bc1_fullflex_week_stack.png` — optional same-week stack with the annual CHP total left free. Not the primary chart.
- `outputs/figures/final_bc1_export_sensitivity.png` — existing bar chart of the BC1 export sensitivity. The report table is the source for the numbers.
- `outputs/figures/final_bc2_npv_by_size.png` — current-cost NPV at 5, 10, and 20 MW beside forward MID NPV at 5, 10, 20, and 30 MW. 20 MW is marked as the best screened MID capacity, not a recommendation. No current-cost point is shown at 30 MW.
- `outputs/figures/final_bc2_dcf_comparison.png` — why the same 20 MW investment is unattractive when the 2026 operating benefit is held constant, and attractive on the MID forward path. Bars are annual net cash flow. The line is cumulative discounted cash flow.

`outputs/figures/final_energy_system_schematic.png` and `outputs/figures/final_bc1_dispatch_week.png` are earlier figures. They are not used in the case-study narrative.
