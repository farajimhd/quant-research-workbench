# V8 next-trade reaction walkthrough

Open `01_price_arrival_frequency.ipynb` with the existing `ml4t` Python environment and
run cells in order. This is an educational experiment, not a V8 production
level engine. It does not change V7 or write database tables.

Notebooks use the naming convention `NN_method_name.ipynb`. Notebook 01 is
the smoothed conditional-frequency baseline using exact price and arrival
direction. Introduce subsequent methods in separate notebooks numbered 02,
03, and so on, preserving earlier methods for comparison.

The notebook loads exactly 2,000 eligible AAPL trades for 2026-08-20 from
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
