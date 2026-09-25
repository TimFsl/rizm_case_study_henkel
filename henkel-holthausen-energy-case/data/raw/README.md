# Raw market inputs

## Electricity

- Source: Bundesnetzagentur | SMARD.de
- Dataset: Germany/Luxembourg day-ahead wholesale price
- Market year: 2025
- Resolution: hourly
- Licence: CC BY 4.0

The file in this folder is the SMARD export. `scripts/build_market_prices.py` reads that export and maps the 2025 hour sequence, in time order, onto the synthetic 2026 model hours. The mapped timestamps are a representative market year. They are not actual 2026 prices.

On the autumn clock change, SMARD repeats one wall-clock hour. Both rows are kept: the first is treated as summer time and the second as standard time. The missing spring-forward hour is not invented.

## Natural gas proxy

The 2025 representative wholesale proxy is 36 EUR/MWh of fuel. It is a fixed screening value, not a Henkel contract price. The rationale is in `config/assumptions.yaml`.

## EUA

The 2025 average German auction price used here is 73.86 EUR/tCO2. It is held constant for every hour. The rationale is in `config/assumptions.yaml`.

## Biomethane

The biomethane premium is a fixed screening assumption, not a Henkel contract price. The base case uses a zero EU ETS combustion factor only for qualifying certified biomethane. That is not a lifecycle-emissions claim.
