# V8 next-trade reaction walkthrough

Open `01_price_arrival_frequency.ipynb` with the existing `ml4t` Python environment and
run cells in order. This is an educational experiment, not a V8 production
level engine. It does not change V7 or write database tables.

Notebooks use the naming convention `NN_method_name.ipynb`. Notebook 01 is
the smoothed conditional-frequency baseline using exact price and arrival
direction. Introduce subsequent methods in separate notebooks numbered 02,
03, and so on, preserving earlier methods for comparison.

Notebook 01 loads exactly 2,000 eligible AAPL trades for 2026-08-20 from
certified canonical SIP, beginning at 09:30 ET. `reaction_demo_source.py`
handles the bounded source read, reporting/condition filters and provenance.
Configured repository ClickHouse credentials are required. Missing source or
reporting coverage fails; there is no external-provider fallback.

The walkthrough shows the original three fields and six vectorized differences,
then the nine arrival/departure groups for the first 1,000 events. A smoothed
frequency model conditions only on exact price and arrival direction. Time and
volume remain explanatory columns, not additional fitted model features.

Predictions start at event 1,001. Each new event reveals the preceding outcome,
which updates the model before its next prediction. There are 999 scored
forecasts and one pending forecast. The unconditional baseline uses the same
available outcomes. Accuracy, balanced accuracy, log loss, Brier score,
confusion counts and cumulative plots are included. Embedded checks cover a
hand-calculated example, prefix invariance, future perturbation and boundaries.

Runtime artifacts go only to
`D:/TradingML/runtimes/level-book-v8/reaction-demo/<UTC-run>/`.
Save executed notebooks there; keep the repository notebook output-free.
Select the `ml4t` interpreter explicitly if the editor's generic Python kernel
points to another environment. Required packages are NumPy, Polars, Matplotlib,
IPython and the repository's existing source-loader dependencies.

One short ordered trade sample is not evidence of generalization or trading
profitability. Equal SIP timestamps use canonical ordinal as the tie-breaker;
the target is the next eligible trade in that order.

## 02 — Price concentration and reaction levels

`02_price_concentration_reaction_levels.ipynb` finds descriptive candidate
levels using the first **200** events from notebook 01's original saved sample.
It verifies that snapshot against its original hash and fails if unavailable;
it does not fetch a replacement or change notebook 01.

Detailed Markdown and code comments explain the six vectorized differences,
the nine direction groups, local peaks across observed prices, and conditional
support/resistance evidence. Every price and selection decision is retained.
Three-trade candidate minimum, three-departure evidence minimum and 60%
reversal share are visible illustrative settings, not validated parameters.
Unchanged reactions and unknown boundary rows are accounted for explicitly.

The notebook displays raw contributing trades, intermediate tables, final
candidates and charts. End-of-prefix levels drawn over earlier trades are
retrospective overlays, not causal historical signals. No prediction model or
profitability evaluation is run in notebook 02.

Run with the same `ml4t` environment. Its `SOURCE_RUN` points to the original
20261020T204419563339Z reaction-demo runtime; results and executed copies belong
under `D:/TradingML/runtimes/level-book-v8/level-discovery/<UTC-run>/`.

## 03 — Normalized count, volume and time scores

`03_count_volume_time_scores.ipynb` keeps the same 200-event prefix, six
differences and nine-cell aggregation, then scores every observed price
directly. It uses no local-peak tests or minimum-count selection gates.

Support/resistance use down-to-up/up-to-down cells and reward quick next-trade
responses with `tau / (tau + mean_delay)`. Continuation cells reward longer
delays with `mean_delay / (tau + mean_delay)`. Count and volume are independently
normalized by their maxima across prices for each role. The three components
have equal default weights, `tau=1` second and threshold `0.60`. Empty cells
score zero. Overall occupancy remains a separate measurement.

These illustrative scores are relative to the selected sample, not calibrated
probabilities. The notebook explains sparse evidence, unchanged cells,
timestamp ties, normalization and the short sample's sensitivity to tau.
It displays component contributions, source rows, sorted scores and charts,
with hand-example, monotonicity, empty-cell and accounting checks.

Use the same `ml4t` environment and original saved source run as notebook 02.
Results belong under
`D:/TradingML/runtimes/level-book-v8/normalized-level-scores/<UTC-run>/`.
