# Project context

## Purpose

This repository contains a transparent pre-onsite energy-system screening model
for the Henkel Düsseldorf-Holthausen industrial site.

It is being developed for a RIZM Field Value Engineer case study.

The goal is NOT to recreate Henkel's actual digital twin.

The goal is to:
- reconstruct a plausible site energy system from public information
- make assumptions explicit
- quantify energy-related business cases in EUR/t
- test sensitivity to missing customer data
- identify the most load-bearing data request for an onsite visit

Method and reasoning are more important than false numerical precision.

---

## Current physical model

The real Holthausen energy system is simplified to:

1. Aggregated CHP block
   - fuel input: fossil natural gas + biomethane
   - output: process steam / useful heat + electricity

2. Aggregated fuel-fired boiler block
   - fuel input: same gas blend
   - output: process steam / useful heat

3. Electricity grid
   - import and export

4. One aggregated steam bus

5. Site-wide electricity demand

Henkel, BASF and other industrial consumers are NOT physically separated in the
current model.

The four real steam pressure levels are aggregated into one steam bus.

---

## Current annual demand assumptions

Demand assumptions are estimated first.
The baseline calculates fuel requirement from steam demand and the modeled
CHP/boiler dispatch, using the CHP power-to-heat ratio and asset efficiencies.
Electricity demand determines the residual grid import/export balance.
Fuel use is compared against historical evidence as a consistency check.

Approximate screening values:

- annual steam / heat demand: 1,040 GWh
- annual electricity demand: 290 GWh
- Henkel production denominator: 455,000 t/a

Steam demand is anchored to historical Holthausen steam production of about
1.5 Mt/a in 2016. Converting tonnes of steam to MWh_th requires pressure,
temperature, and feedwater enthalpy. A screening range of about
0.65-0.70 MWh_th/t, which is not a measured Henkel value, gives about
975-1,050 GWh_th/a. The 1,040 GWh/a base case sits in that range. It is
not a measured current 2026 demand.

Electricity demand is derived from that steam base case and the interpolated
StoREN useful-energy demand structure of about 78% steam / 22% electricity:

1,040 GWh x (22 / 78) ≈ 293 GWh/a, rounded to 290 GWh/a.

StoREN's 2018 reference is about 81% steam / 19% electricity. The 2030
modeled structure is about 76% steam / 24% electricity. The 78/22 split is
the 2026 screening interpolation of that demand structure. It is a screening
assumption, not measured current site electricity consumption. The split
describes site useful-energy demand, not power-plant fuel output.

About 1.55 TWh/a is an independent site-level fuel-input magnitude check,
inside a screening range of about 1.4-1.7 TWh/a. The baseline calculates
fuel requirement from steam demand and the modeled CHP/boiler dispatch,
using the CHP power-to-heat ratio and asset efficiencies. Electricity demand
determines the residual grid import/export balance. That result is about
1.50 TWh/a. Fuel input is a validation output / external plausibility check,
not the source from which demand is derived.

The 455 kt/a figure is only used later as a normalization denominator.
It must not be used to physically allocate site energy between Henkel and third parties.

---

## CHP assumptions

Current base screening assumptions:

- total utilization efficiency: 0.86
- power-to-heat ratio: 0.50 MWh_el / MWh_th
- max heat output: 110 MW_th
- baseline steam share: 0.52

Sensitivity:
- power-to-heat ratio: 0.30 / 0.50 / 0.70
- total utilization efficiency: 0.82 / 0.86 / 0.90

These are screening assumptions, NOT verified current Henkel equipment parameters.

The baseline CHP steam share was calibrated so that:

1,040 GWh steam
x 52%
x P/H 0.50

produces about 270 GWh CHP electricity.

This is consistent in magnitude with historical Holthausen electricity generation:
- 2012: about 257 GWh
- 2016: about 285 GWh

---

## Boiler assumptions

Current base assumptions:

- thermal efficiency: 0.90
- max heat output: 100 MW_th

Sensitivity:
- efficiency: 0.85 / 0.90 / 0.95

The capacity is a screening assumption, not a verified existing Henkel nameplate value.

---

## Plant structure sensitivity cases

Three plant structures exist:

- boiler-heavy:
  - CHP heat capacity: 80 MW
  - boiler heat capacity: 140 MW

- balanced:
  - CHP heat capacity: 110 MW
  - boiler heat capacity: 100 MW

- CHP-heavy:
  - CHP heat capacity: 140 MW
  - boiler heat capacity: 80 MW

The balanced case is the current base case.

---

## Fuel mix

Current screening assumption:

- coal: 0%
- fossil natural gas: 68%
- biomethane / biogas: 32%

Reasoning:
A late-2023 source was interpreted as approximately:
- 80% gaseous fuels
- 20% coal
- biomethane / biogas = about 40% of the gaseous-fuel portion

Therefore:
0.8 x 0.4 = 0.32 biomethane share of total fuel.

After coal phase-out in 2024, the model assumes the former coal share was replaced
by fossil natural gas while the absolute biomethane share remained approximately
32%.

This is NOT a verified current Henkel fuel mix.
The actual renewable-gas share may now be higher.

---

## Synthetic demand profiles

Hourly demand profiles are synthetic.

They are NOT intended to reproduce actual Henkel operation.

Three deterministic profiles exist:
- flat
- base
- variable

All three have exactly:
- 1,040 GWh annual steam / heat demand
- 290 GWh annual electricity demand

The profiles differ only in temporal shape.

The base case assumes continuous three-shift industrial operation with modest
hour-of-day, weekday/weekend and seasonal variation.

The variable scenario is intentionally more volatile and is used only as a sensitivity case.

No random noise is used.

---

## Rule-based baseline dispatch

The current baseline is a:

"calibrated rule-based reference dispatch"

It is NOT claimed to represent Henkel's actual current control logic.

Hourly logic:

CHP target steam =
baseline CHP steam share x site steam demand

Boiler supplies the residual steam demand.

Capacity constraints are respected.

CHP electricity is:

P_CHP = Q_CHP x power_to_heat_ratio

CHP fuel is:

Fuel_CHP =
(Q_CHP + P_CHP) / CHP_total_utilization_efficiency

Boiler fuel is:

Fuel_boiler =
Q_boiler / boiler_efficiency

Grid balance is:

grid_net =
electricity_demand - CHP_electricity

Positive = import.
Negative = export.

The grid balance is an output, not an assumed time series.

---

## Baseline physical results

For the current balanced base assumptions:

- CHP steam: about 540.8 GWh/a
- boiler steam: about 499.2 GWh/a
- CHP electricity: about 270.4 GWh/a
- total fuel: about 1,498 GWh/a
- net grid import: about 19.6 GWh/a

These results are intentionally treated as model outputs, not measured Henkel values.

Calculated fuel use of about 1,498 GWh/a (about 1.50 TWh/a) is compared with
the independent screening range of about 1.4-1.7 TWh/a, base reference about
1.55 TWh/a. Fuel input is a validation output / external plausibility check,
not the source from which demand is derived.

The low grid import partly results from the calibration of CHP electricity generation
close to total modeled site electricity demand.

Do not treat the modeled annual grid import as an independent validation point.

---

## Market assumptions

Representative market year:
- 2025

Electricity:
- hourly German/Luxembourg Day-Ahead prices from SMARD
- average about 89.32 EUR/MWh
- negative prices are retained
- import adder base: 0 EUR/MWh
- export discount base: 5 EUR/MWh

The export discount is a screening marketing, balancing, and transaction allowance.
It is not a network charge. Export price = day-ahead price - 5 EUR/MWh.
The generic import adder stays at zero when the explicit network tariff is used,
so the two are not stacked.

These prices are marginal opportunity-value proxies.
They are NOT Henkel's actual electricity procurement contract.

Natural gas:
- fixed commodity proxy: 36 EUR/MWh_fuel
- variable gas adder: 4 EUR/MWh_fuel

EUA:
- 73.86 EUR/tCO2

Natural gas emission factor:
- 0.2016 tCO2/MWh_fuel

Biomethane:
- modeled as gas commodity + gas adder + 15 EUR/MWh premium
- EU ETS combustion factor assumed zero only for qualifying certified sustainable biomethane
- do not interpret this as zero lifecycle emissions

Resulting current blended fuel cost:
about 54.9 EUR/MWh_fuel

---

## Baseline cost interpretation

The current model calculates:

"screening variable energy cost"

It is NOT:
"Henkel's actual energy bill"

It excludes or simplifies:
- actual Henkel hedging
- PPAs
- procurement margins
- the network tariff, which is added only in the separate tariff layer
- detailed taxes and levies
- startup costs
- ramping costs
- maintenance
- district-heating revenues
- third-party commercial allocation

Current variable energy cost is roughly 84 M EUR/a at site-system level.

Do NOT interpret the site-system cost divided by 455 kt as an attributable Henkel energy cost.

---

## Important modeling principles

1. Do not invent missing plant data merely to make the model more realistic.

2. Prefer:
   technology prior
   -> Holthausen sanity check
   -> sensitivity analysis

3. Preserve the distinction between:
   - public facts
   - derived quantities
   - screening assumptions
   - TBD customer-specific parameters

4. Only add model complexity if it can materially change the business-case conclusion.

5. Annual cost or energy outputs from the full site must not automatically be attributed to Henkel.

6. The model should remain reproducible and understandable by a reviewer.

---

## Historical / public sanity anchors

Historical Holthausen references include approximately:

2012:
- steam production: 1.85 Mt/a
- electricity generation: 257.2 GWh/a
- observed steam-load range: about 120-380 t/h

2016:
- steam production: about 1.5 Mt/a
- electricity generation: about 285 GWh/a
- total fuel utilization: about 83%

Independent fuel-input magnitude check, not a demand input:
- base reference: about 1.55 TWh/a
- screening range: about 1.4-1.7 TWh/a

StoREN:
- reference demand year: 2018
- useful-energy structure: about 81% steam / 19% electricity
- modeled 2030 maximum steam demand: 168.4 MW_th
- modeled 2030 maximum electricity demand: 47.3 MW_el
- existing grid connection used in scenarios: approximately 64 MW

2030 values are model assumptions / projections, NOT measured current peaks.

---

## What is intentionally NOT implemented yet

Do not assume the following already exist:

- industrial heat pump
- district-heating dispatch
- four separate steam pressure levels
- CHP minimum-load constraints
- startup / shutdown costs
- minimum runtime
- binary unit commitment
- part-load efficiency curves
- boiler ramp constraints
- steam storage
- actual CHP availability / maintenance periods
- actual Henkel / BASF energy allocation
- a verified export-capacity contract

---

## Price-responsive dispatch

The simple hourly optimization is implemented in `src/optimization.py`.
It is a screening dispatch value relative to the calibrated reference,
not an estimate of realized Henkel savings.

The optimized result is not an estimate of realized Henkel savings because
actual current operating rules, unit-level constraints and export capability
are unknown.

Modes:

- full_flex: theoretical upper bound. The CHP/boiler split is free every hour.
  There is no annual CHP production target. Annual CHP steam, boiler steam,
  and CHP electricity can all change.
- redispatch_only: timing value at constant annual CHP generation.
  The hourly 52/48 steam split is removed. The annual CHP electricity of the
  corresponding baseline is preserved. With the current fixed power-to-heat
  ratio, the base case therefore keeps about 540.8 GWh CHP steam,
  499.2 GWh boiler steam, and 270.4 GWh CHP electricity.
  Hourly CHP and boiler shares may vary.

Objective, for every hour:

fuel cost at the blended fuel price
+ grid import cost
- grid export revenue

A 1e-6 EUR/MWh tie-breaker on gross import plus export is numerical only.
It is excluded from reported screening energy cost.

Main constraints:

- steam balance, with no storage and no unmet demand
- CHP and boiler heat capacities from assumptions.yaml
- CHP electricity = CHP steam x power-to-heat ratio
- electricity balance
- grid import capacity
- redispatch_only also fixes annual CHP electricity to the baseline total

No export-capacity constraint is applied in the stored energy-only and tariff runs.
The final export sensitivity caps hourly export at the configured screening limit.
The 64 MW import connection is not used as that limit.

The CHP-versus-boiler break-even electricity price is calculated from the
current fuel price and efficiencies. It is the sanity check for full_flex:
low prices move CHP to the feasible minimum, high prices move it to the
feasible maximum.

The stored energy-only run does not apply a CHP ramp.
The final base-profile sensitivity does. See "CHP ramp and regime-consistent value".

Minimum loads, startup costs, part-load curves, and unit commitment stay out.
Unit-level current operating constraints are not public.

Run `python scripts/run_optimization.py`.

---

## Network tariff

The tariff layer is a screening sensitivity, not a verified Henkel contract.
High voltage is assumed from the approximately 64 MW grid-connection reference.
Confidence is low. The actual connection and billing level must be checked onsite.

Public proxy, Netzgesellschaft Düsseldorf – Preisblatt Netznutzungsentgelte Strom 2025:

- hv_high_utilization, at least 2,500 h: 7.90 EUR/MWh and 123.93 EUR/kW/a
- hv_low_utilization, below 2,500 h: 49.70 EUR/MWh and 19.30 EUR/kW/a

Work charge and demand charge stay separate from the day-ahead commodity price
and from the 5 EUR/MWh export discount.

The two regimes are solved independently. There is no binary tariff choice and
no utilization-hour constraint inside the dispatch. After each solve, report
whether annual import MWh / peak import MW matches the regime that was applied.
Keep inconsistent results.

The annual peak is the maximum hour of the synthetic profile. Actual billed
peaks can be higher because of outages, maintenance, and abnormal states.
The 15-minute import series, the billed maximum demand, and the network or
reserve-capacity agreement are onsite data needs. Do not invent them.

Düsseldorf publishes reserve-capacity pricing for customers with decentralized
generation. That product is not modeled. Henkel's arrangement is unknown.

Run `python scripts/run_network_tariffs.py`.
Do not overwrite the energy-only dispatch files.

The same-tariff table from that script is a sensitivity.
The headline comparison is the regime-consistent value in the final sensitivity:
baseline cost uses the tariff matched to baseline utilization hours, and the
optimized cost is used only when the solved dispatch matches its own tariff.

---

## CHP ramp and regime-consistent value

CHP heat in the final sensitivity cannot change, hour to hour, by more than
`chp_ramp_fraction_of_heat_capacity_per_hour` times CHP heat capacity.
The base screening value is 0.50. Sensitivities are 0.25, 0.50, and 1.00.
This is a technology screening assumption, not a measured Henkel ramp.
The first hour has no invented prior operating state.

The aggregated boiler has no ramp constraint. At hourly resolution it is the
fast steam-balancing asset. Unit-level boiler ramp and start limits are unknown.

Part-load efficiencies are not modeled. Equipment curves are not public, and
an assumed nonlinear curve would add false precision. Keep the existing
efficiency sensitivities instead.

Baseline and optimized tariffs are classified from utilization hours after
the solve. The optimizer does not choose the regime. Actual Henkel ramp limits
and the grid contract remain onsite validation needs.

The primary screening dispatch value is base demand, redispatch-only, 50%/h,
the tariff that solved result actually matches, and a 10 MW export limit.
It is not a Henkel saving. 5 MW, 20 MW, and unconstrained export are sensitivities.
Public information confirms export is possible. The contracted capacity was not
found. Do not set the limit equal to the 64 MW import connection.

Run `python scripts/run_final_sensitivity.py` for the ramp cases.
Run `python scripts/run_export_sensitivity.py` for the export cases.

---

## Current critical TBD

Contracted grid export capacity is still unknown. The screening value is 10 MW.
5 MW, 20 MW, and an unconstrained run test that assumption. It remains a
high-value onsite validation point.

Do not treat 10 MW as a verified Henkel contract.
Do not assume export capacity equals the 64 MW import connection.

---

## Current next step

Business Case 1, the price-responsive dispatch with the tariff, ramp, and
10 MW export screen, stays as its own result. It is not overwritten by the
electrode-boiler screen.

Business Case 2, the electrode-boiler investment screen, is implemented.
The investment comparison is full flexibility without the boiler versus
full flexibility with the boiler. The earlier redispatch-only investment
result remains as the annual-CHP-utilization-constrained sensitivity.
See "Electrode boiler investment" below.

Do not add minimum loads, startup costs, part-load efficiency curves,
binary tariff switching, or a boiler ramp unless a later result needs them.

---

## Electrode boiler investment

Business Case 2 asks what an industrial electrode boiler is worth on the
existing Holthausen system if the external grid connection is not expanded.

Power-to-heat was selected because it is the next shortlisted case after
dispatch, and it can be tested without a new steam-storage or heat-pump
technology. It is incremental to the optimized existing plant, so Business
Case 1 savings are not counted again. The investment counterfactual is
full flexibility with zero electrode-boiler capacity. Both sides use the
same base demand, CHP and boiler capacities, efficiencies, power-to-heat
ratio, 50%/h CHP ramp, 64 MW import, 10 MW export, fuel, carbon, and
tariff. Annual CHP generation is free. The only difference is the boiler.

Useful steam capacity is screened at 0, 5, 10, 15, 20, 25, 30, 35, and
40 MW. Capacity is not a variable inside the hourly model, so the dispatch
and the investment decision stay separate. CAPEX and fixed O&M are applied
to the annual operating benefit afterwards.

The operating benefit in each investment year is the mean of three solves
that reuse the 2023, 2024, and 2025 day-ahead shapes. Those years are an
equally weighted observed ensemble. They are not averaged into one price
series and they are not a forecast of 2026-2040. The EUA path is an explicit
scenario. Day-ahead prices are not trended with that path.

The primary size is the capacity with the highest NPV at a 10% real hurdle,
a 15-year life, 200,000 EUR/MW_th, and fixed O&M of 1.7% of CAPEX, under
full flexibility. The redispatch-only screen, which holds annual CHP
electricity at the calibrated reference, remains available as the
conservative utilization-constrained sensitivity. It does not choose the
size.

The e-boiler has no minimum load, startup cost, or ramp. Existing CHP and
gas-boiler fixed O&M is not reduced, because those assets stay installed.
Fuel and carbon costs fall only when fuel use falls. The single steam bus
remains a simplification: the model assumes a suitable header, and the real
pressure level is an onsite check. External grid reinforcement is excluded.

Run `python scripts/run_electric_boiler.py` for the redispatch-only sensitivity.
Run `python scripts/run_electric_boiler_full_flex.py` for the full-flex
investment screen. The full-flex script does not overwrite the redispatch files.

The forward fuel-cost screen keeps the 2026 Düsseldorf high-voltage tariff
constant in real terms and solves full flexibility only at 2026, 2030, and
2040. The 2023, 2024, and 2025 day-ahead shapes are observed market structures,
not future hourly prices. LOW, MID, and HIGH change the pre-carbon gas price
and the EUA price. MID is the primary forward-looking screening case. The
capacity with the highest MID NPV is a screening optimum, not a recommended
Henkel investment. A 5 MW result is small flexible power-to-heat capacity,
about 4% of average steam load, not site-wide steam electrification.

Run `python scripts/run_electric_boiler_forward_cost.py`.

Limitations of this investment case:

- the steam pressure level for the connection is unknown
- onsite electrical integration cost is a literature allowance, not a quote
- the grid contract is unknown, and the external connection is not expanded
- 2023-2025 shapes are not future price forecasts
- EUA paths are scenarios, not forecasts
- no tax, subsidy, depreciation, or financing structure
- no electricity-generation emissions model
- no future site demand growth
- no unit-level CHP model and no e-boiler degradation
- 10% is a screening hurdle, not Henkel's WACC
- CAPEX is literature-based, not a Henkel vendor quote

---

## Possible later business cases

Current shortlist:

1. price-responsive CHP / boiler / grid dispatch — implemented as Business Case 1
2. electric boiler investment / power-to-heat — implemented as Business Case 2
3. high-temperature heat pump / process waste-heat recovery

The heat-pump case should be modeled parametrically unless actual Henkel waste-heat
source temperature, available MW, annual hours and target temperature become available.

The already-commissioned district-heating waste-heat stream must not be double-counted
as freely available waste heat.