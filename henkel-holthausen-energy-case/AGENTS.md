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

Approximate screening values:

- annual steam / heat demand: 1,040 GWh
- annual electricity demand: 290 GWh
- Henkel production denominator: 455,000 t/a

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
- export discount base: 0 EUR/MWh

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
- fixed network charges
- demand charges
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
- electricity generation: about 285 GWh/a
- total fuel utilization: about 83%

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

- cost-optimized CHP / boiler dispatch
- Pyomo / HiGHS optimization
- electric boiler
- industrial heat pump
- district-heating dispatch
- four separate steam pressure levels
- ramp-rate constraints
- CHP minimum-load constraints
- startup / shutdown costs
- minimum runtime
- steam storage
- actual CHP availability / maintenance periods
- actual Henkel / BASF energy allocation
- actual export-capacity limit

---

## Current critical TBD

The main remaining physical TBD is:

- grid export capacity

Do not automatically assume export capacity equals the 64 MW import connection.

---

## Current next step

The next main model step is:

PRICE-RESPONSIVE CHP / BOILER DISPATCH OPTIMIZATION

The optimization should use the SAME:
- hourly demand profiles
- plant assumptions
- market assumptions

as the baseline.

The only major change should be that CHP steam share becomes an hourly decision variable.

Decision variables will likely include:
- CHP steam output
- boiler steam output
- grid import
- grid export

Steam balance:

Q_CHP_t + Q_boiler_t = Q_demand_t

CHP electricity:

P_CHP_t = P/H x Q_CHP_t

Electricity balance:

P_CHP_t + Import_t =
ElectricityDemand_t + Export_t

Objective:

minimize variable energy cost

subject to capacity constraints.

The analytical CHP-vs-boiler electricity break-even under current base assumptions
is approximately 69-70 EUR/MWh electricity.

This should be used as a sanity check against the optimizer.

The first optimization should deliberately stay simple.
Do not add minimum loads, ramping, startup costs or other realism until the simple
optimization has been validated.

---

## Possible later business cases

Current shortlist:

1. price-responsive CHP / boiler / grid dispatch
2. electric boiler investment / power-to-heat
3. high-temperature heat pump / process waste-heat recovery

The heat-pump case should be modeled parametrically unless actual Henkel waste-heat
source temperature, available MW, annual hours and target temperature become available.

The already-commissioned district-heating waste-heat stream must not be double-counted
as freely available waste heat.