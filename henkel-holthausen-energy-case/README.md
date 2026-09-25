# Henkel Düsseldorf-Holthausen energy screening model

Pre-onsite screening model for the Holthausen industrial energy system. Version v0.1 is a transparent assumptions layer. It is not a digital twin and it does not dispatch the plant.

## Objective

Build a reproducible pre-onsite screening model for the Holthausen industrial energy system. Assumptions stay explicit, so later work can add dispatch optimization and an electric-boiler investment case without quietly changing the physical scope.

The operating site is more detailed than this model. It includes combined-cycle and gas-fired CHP, steam turbines, boilers, several steam pressure levels, a grid connection, third-party consumers, and district-heating export. v0.1 keeps only the aggregated system below.

## Current scope

v0.1 represents:

- one aggregated CHP block, with fossil natural gas and biomethane in and electricity plus useful steam/heat out
- one aggregated fuel-fired boiler block, with the same fuels in and useful steam/heat out
- electricity grid import and export
- one aggregated steam bus
- one aggregated site electricity demand

Physical demand is the full industrial site. Henkel, BASF, and other consumers are not split. Economic allocation of value to Henkel comes later.

## Explicit exclusions in v0.1

- four individual steam pressure levels
- individual turbines and boilers
- BASF vs Henkel physical allocation
- district-heating optimization
- ramping
- startup costs
- storage
- electric boiler
- dispatch optimization

Optimization libraries are not dependencies yet.

## Modeling philosophy

Public facts, derived quantities, and screening assumptions stay separate. Historical measurements live in `historical_reference`. StoREN model anchors live in `storen_reference`. Parameters with no screening prior stay `null` and are marked `TBD`.

Plant priors follow: technology prior -> Holthausen sanity check -> sensitivity analysis.

Where site data are unavailable, later runs should use the stored sensitivities instead of a single silent guess. The model should become more detailed only when the extra complexity would change the business-case result.

`config/assumptions.yaml` is the single source of truth. `src/assumptions.py` loads it and checks the v0.1 rules.

## Current plant screening assumptions

The operating Holthausen system is more detailed than this model. v0.1 aggregates the CHP system into one CHP block and the steam-only boilers into one boiler block.

The base screening configuration assumes:

- CHP heat capacity = 110 MW_th
- boiler heat capacity = 100 MW_th
- CHP power-to-heat ratio = 0.50

These are not claimed to be current Henkel nameplate values. They are transparent screening assumptions anchored to public Holthausen and StoREN information. Boiler-heavy and CHP-heavy configurations are stored for sensitivity analysis and are not applied automatically. Historical measured and reference values are stored separately from these assumptions.

## Synthetic demand profiles

No public hourly site load data were available, so the hourly steam and electricity series are synthetic. They do not represent measured Henkel production. The flat, base, and variable scenarios are deterministic, contain no random noise, and are normalized to the same annual energy totals. Hour, weekday, and month factors are explicit in `demand_profile_scenarios`.

The purpose is to test whether a later dispatch result depends strongly on the unknown load shape. StoREN 2030 peaks and the 2012 steam-load ratios are plausibility references only. They do not constrain or reshape the profiles. Site 15-minute demand data would replace this layer in a real deployment.

Build them with `python scripts/build_demand_profiles.py`.

## Rule-based baseline dispatch

The baseline is a calibrated reference operation. It is not measured Henkel dispatch, and it is not evidence that the site currently operates in a non-optimized way.

Steam demand is the operational driver. The configured CHP steam share is assigned to the aggregated CHP, and the aggregated boiler supplies the residual steam. Capacity limits are enforced; unmet steam is not clipped. CHP electricity follows from the fixed power-to-heat ratio. The electricity grid balances the difference between site electricity demand and CHP generation. Export is recorded without a capacity limit because that limit is still unknown.

The baseline ignores hourly market prices. It is the reference case for a later price-responsive optimization.

Run it with `python scripts/run_baseline.py`.

## Market and cost assumptions

2025 is used as a representative complete market year. The physical demand profiles remain synthetic 2026 profiles. The 2025 market environment is applied to the 2026 physical screening demand. Mapped timestamps are not actual 2026 prices.

Hourly Germany/Luxembourg day-ahead prices come from Bundesnetzagentur | SMARD. They are a marginal opportunity-price proxy for dispatch screening. They are not Henkel's electricity procurement contract, and they do not include a full industrial network tariff. In the base case the import adder and the export discount are both zero. Those sensitivities are stored and not run.

Natural gas is a fixed 2025 wholesale proxy, plus a small variable delivery allowance. There is no hourly gas series. Biomethane is that same gas commodity stack plus a fixed screening premium. The EUA cost applies only to the fossil-gas fraction, using a zero EU ETS combustion factor for qualifying certified biomethane. That factor is not a lifecycle-emissions claim. The blended fuel cost is constant in v0.1.

The resulting baseline cost is a screening variable energy cost. It is not an actual Henkel energy bill. It excludes fixed network charges, demand charges, taxes that are not modeled, contractual hedging, procurement margins, maintenance, startup costs, other operating costs, district-heating revenue, and any split between Henkel and third parties. The EUR per tonne figure only normalizes that site-level screening cost by Henkel production. It is not an attributable Henkel cost.

Build the price series with `python scripts/build_market_prices.py`, then value the baseline with `python scripts/cost_baseline.py`.

## Assumptions file

Each important assumption is a small mapping. Fields are included where they apply:

- `value`: number, string, boolean, or `null`
- `unit`: physical unit, when there is one
- `type`: `public`, `derived`, `assumed`, `modeling_choice`, or `TBD`
- `confidence`: `high`, `medium`, or `low`
- `source_or_rationale`: short reason, without invented citations or URLs
- `sensitivity`: values to test later
- `critical`: `true` when the business case depends on this parameter, whether the current entry is a screening value or still `null`

A `null` value is allowed only when `type` is `TBD` and the parameter is on the explicit list in `src/assumptions.py`.

The annual steam and electricity totals were judged low/medium. The file stores `confidence: medium`, because the allowed set is high, medium, or low. The original judgment is written in `source_or_rationale`.

## Layout

```text
henkel-holthausen-energy-case/
├── README.md
├── requirements.txt
├── config/assumptions.yaml
├── data/raw/
├── data/processed/
├── src/assumptions.py
├── src/profiles.py
├── src/baseline.py
├── src/market_prices.py
├── src/costs.py
├── scripts/check_assumptions.py
├── scripts/build_demand_profiles.py
├── scripts/run_baseline.py
├── scripts/build_market_prices.py
├── scripts/cost_baseline.py
├── outputs/figures/
├── outputs/tables/
└── tests/
```

## Checks

From this directory:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python scripts/check_assumptions.py
python scripts/build_demand_profiles.py
python scripts/run_baseline.py
python scripts/build_market_prices.py
python scripts/cost_baseline.py
pytest
```

## Planned next steps

1. Implement cost-optimized CHP, boiler, and grid dispatch against this baseline.
2. Extend the same model with an electric boiler investment case.
