# Henkel Düsseldorf-Holthausen — Final Screening Results

Site-level screening values below are normalized by 455,000 t/a. They are not realized Henkel savings and they do not allocate the site between Henkel and third parties.

## 1. Modeling boundary

The model is one aggregated site energy system: a CHP block, a gas boiler, the electricity grid, and one steam bus. Demand profiles are synthetic. Prices and the network tariff are public proxies (MKT01, TAR26 for the electrode-boiler screen). It is not a Henkel digital twin.

## 2. Business Case 1 — Price-responsive redispatch

Primary case, from `outputs/tables/dispatch_export_sensitivity.csv`, row `primary_screening`:

- base demand
- redispatch only, so annual CHP electricity stays at the calibrated total
- CHP ramp 50%/h
- 2025 Düsseldorf high-voltage tariff proxy, regime-consistent
- export capacity 10 MW
- no CAPEX

Primary screening dispatch value: **1.986 M EUR/a**, **4.366 EUR/t**.

The value comes from moving existing CHP production toward higher-price hours. It is not an investment result.

Redispatch value if the export limit changes, same high-utilization regime as the primary row:

| Export capacity | Value | EUR/t |
|---|---:|---:|
| 5 MW | 1.281 M EUR/a | 2.815 |
| 10 MW | 1.986 M EUR/a | 4.366 |
| 20 MW | 3.163 M EUR/a | 6.953 |
| Unconstrained | 3.269 M EUR/a | 7.184 |

Full flexibility with the same 10 MW export cap is a secondary upper bound: **3.470 M EUR/a**, **7.627 EUR/t**. It lets annual CHP generation change. It is not the primary Business Case 1 result.

Main unknowns: contracted export capacity, the billed network tariff, 15-minute dispatch, and unit constraints that this hourly model does not include.

## 3. Business Case 2 — Electrode boiler

Hourly full-flex dispatch produces an annual operating benefit. That benefit is then turned into a 15-year investment result at 200,000 EUR/MW, fixed operating cost of 1.7% of CAPEX, a 10% real discount rate, and no subsidy, tax, or salvage value (EB01, EB02, EB04, FIN01). The counterfactual is full flexibility with no electrode boiler. The 2026 Düsseldorf high-voltage tariff is held constant in real terms (TAR26).

The 2023, 2024, and 2025 day-ahead series are observed hourly market shapes (MKT01). They are reused at every anchor year. They are not a forecast of 2030 or 2040 electricity prices. LOW, MID, and HIGH change only the pre-carbon gas price and the EUA price (GAS01, CO201, CO202). Those anchors are rounded screening scenarios, not copied forecasts.

### Current-cost screen

2026 fuel and carbon stack, three price shapes, and that annual benefit held constant in real terms. Stored in `outputs/tables/electric_boiler_forward_npv.csv` (2026 benefit) and `outputs/tables/electric_boiler_current_vs_forward.csv`.

| Capacity | Gross benefit | NPV | Simple payback | Discounted payback |
|---|---:|---:|---:|---|
| 5 MW | 155 kEUR/a | +0.047 M EUR | 7.3 years | 13.6 years |
| 10 MW | 295 kEUR/a | −0.019 M EUR | 7.7 years | not reached |
| 20 MW | 514 kEUR/a | −0.61 M EUR | 9.0 years | not reached |

Under current cost conditions only small flexible power-to-heat capacity is around economic break-even. Five megawatts is about 4% of the roughly 119 MW average steam load.

### Forward fossil-cost screen

MID is the primary forward path. LOW and HIGH are sensitivities. Thirty megawatts was solved on MID only.

| Capacity | Low NPV | Mid NPV | High NPV | Mid IRR | Mid simple payback | Mid discounted payback | Mid EAV EUR/t |
|---|---:|---:|---:|---:|---:|---:|---:|
| 5 MW | −0.29 M EUR | +0.78 M EUR | +3.13 M EUR | 19.4% | 5.7 years | 8.0 years | +0.23 |
| 10 MW | −0.65 M EUR | +1.21 M EUR | +6.08 M EUR | 17.8% | 6.0 years | 8.6 years | +0.35 |
| 20 MW | −1.71 M EUR | +1.46 M EUR | +10.61 M EUR | 15.0% | 6.7 years | 10.1 years | +0.42 |
| 30 MW | not run | +1.03 M EUR | not run | 12.4% | 7.5 years | 12.2 years | +0.30 |

20 MW is the screening NPV optimum among the tested capacities.

On the MID path at 20 MW, gross operating benefit rises from **514 kEUR in 2026** to **735 kEUR in 2030** and **1.204 M EUR in 2040**. Installed capacity is about 17% of average steam load. Annual electrode-boiler steam is about 3.4% of the 1,040 GWh steam demand over the investment life. It is flexible power-to-heat during favourable hours, not site-wide steam electrification. In 2026 and 2030 it mainly displaces CHP steam. By 2040 the displacement is a mixture of CHP and gas-boiler steam. Peak import rises from about 25 MW to about 52 MW.

The additional 30 MW MID check has CAPEX of 6.0 M EUR and fixed operating cost of 0.102 M EUR/a. Anchor benefits are 674, 970, and 1,551 kEUR. Average steam share is about 5.0%. Peak import is about 62 MW. Its NPV is below the 20 MW NPV, so no larger size was tested.

## 4. Comparison of business cases

| | Business Case 1 | Business Case 2 |
|---|---|---|
| What it is | Operational redispatch of existing assets | Electrode-boiler investment |
| CAPEX | None | 200,000 EUR/MW |
| Primary value | 4.366 EUR/t per year | MID-path EAV 0.422 EUR/t at 20 MW |
| Other result | 1.986 M EUR/a | NPV +1.46 M EUR; simple payback 6.7 years |
| Timing | Near-term operating value | Strategic, over 15 years |

These are different economic profiles. One has no capital cost and depends on how the existing CHP is run. The other spends capital for a new power-to-heat asset whose value depends on the future fuel and carbon path.

## 5. Key limitations

- Third-party energy on the site is not allocated.
- Load profiles are synthetic.
- Four steam pressure levels are one steam bus.
- Actual CHP operating constraints are unknown.
- Contracted export capacity is unknown.
- The billed network tariff and the billed peak are unknown.
- Electrode-boiler pressure-header integration is unknown.
- CAPEX is a literature screening value, not a vendor quote.
- Fuel and carbon paths are scenarios (GAS01, CO201, CO202).
- Historical day-ahead shapes are not future power-price forecasts (MKT01).

BIO01 remains `TODO_VERIFY`. It covers separate biomethane price sensitivities, not the LOW / MID / HIGH anchors.

## Authoritative files

Frozen results, not intermediate screens:

- Business Case 1 primary and export sensitivity: `outputs/tables/dispatch_export_sensitivity.csv`. The primary row is `primary_screening`.
- Business Case 2 current-cost NPV: `outputs/tables/electric_boiler_current_vs_forward.csv`.
- Business Case 2 forward NPV, including 30 MW on the MID path only: `outputs/tables/electric_boiler_forward_npv.csv`.
- Assumptions: `config/assumptions.yaml`.
- Sources: `docs/source_register.md`.

`outputs/tables/dispatch_final_sensitivity.csv` is an earlier ramp screen. Its `primary_screening` row does not apply the 10 MW export cap, and its about 7.18 EUR/t result is not the frozen primary. `outputs/tables/electric_boiler_sizing.csv` is the earlier redispatch-only investment screen, in which every positive size had a negative NPV. It is not the current-cost or forward result.

The electricity-price shapes are historical observations, while the fuel/carbon paths are forward-looking screening scenarios. This is a scenario investment model, not a forecast of future hourly electricity prices.

## 6. First onsite validation

To be finalized in case-study write-up.
