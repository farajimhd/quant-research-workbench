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

The price overlay after section 7 uses `ipywidgets` (available in `ml4t`) for
four independent role thresholds. Run the cell with an active notebook kernel;
drag a slider and release to redraw. Each line starts at the first selected
event at that exact price, while its score still uses the complete sample.
Slider changes affect this chart only, not the scoring formula or earlier
tables. The latest PNG and `chart_thresholds.json` are saved in the run folder.

## 04 — Adaptive-gap reaction bands

`04_adaptive_gap_reaction_bands.ipynb` uses the same saved 2,000-trade sample.
An event-count slider also exposes the earlier 200-trade prefix. An exponential
average of nonzero absolute log-price changes estimates the gap without a
clock-time window. Defaults are a 1,000-change half-life and gap multiplier 2;
these are illustrative settings, not fitted parameters.

Prices are sorted and grouped from the lowest unassigned price. Each band's
entire span must fit its anchor's adaptive gap, rounded up to source price
precision. This width cap prevents adjacent prices chaining into a wide band.
Original nine-cell evidence is summed before recomputing normalized scores
across bands. The four scores retain notebook 03's formulas.

One large interactive chart provides event-count, half-life, gap-multiplier,
time-reference and independent support/resistance threshold sliders, starting
at 0.50. It overlays green support and red resistance with shaded observed
bounds, volume-weighted centers and role/score labels. Continuation bands and
their sliders are omitted. Bands start at their first member-price appearance; their
membership and scores use the selected complete prefix, so these overlays
are retrospective. The scale checkpoint is resumable, while band membership
is rebuilt per selected view and band IDs are not persistent identities.

Run with `ml4t` and an active `ipywidgets` kernel. Checks cover state resume,
flat prices, bounded spans, evidence conservation and prefix isolation.
Latest chart, settings and tables are saved under
`D:/TradingML/runtimes/level-book-v8/adaptive-gap-bands/<UTC-run>/`.

## 05 — Score-valley reaction bands

`05_score_valley_reaction_bands.ipynb` preserves notebook 04 and replaces its
gap grouping with peaks and surrounding valleys in each role's exact-price
score profile. It uses the same 2,000 saved trades, without a movement half-life,
gap multiplier or smoothing. Equal-score plateaus use their lower middle
observed price. Neighboring bands share a valley boundary; boundary evidence
belongs only to the right band. Range-edge boundaries are explicitly open.
A flat profile produces no distinguishable bands.

The notebook displays the hand example, plateau neighbor comparisons, boundary
reasons, exact-price membership, summed evidence and recomputed band scores,
including a worked real band. Support and resistance share one large price
chart with independent 0.50-default threshold sliders. Two smaller score-profile
plots expose peaks and valley boundaries. Thresholds filter band strengths;
they do not alter the partition. Unsmooth profiles can produce many small bands.

Run with the same `ml4t` kernel. Hand examples, plateau/edge behavior, unique
membership, evidence conservation and score bounds are checked. Runtime tables,
chart and provenance are saved under
`D:/TradingML/runtimes/level-book-v8/score-valley-bands/<UTC-run>/`.
As with notebook 04, overlays use final-prefix evidence and are retrospective.

## 06 — Evolving bands, retests and time strength

`06_unbroken_age_strength.ipynb` now updates discovery after every event from
200 onward. Earlier sections retain the original 200-event partition as an
explained comparison. The replay trims each peak to its connected score region
above the higher neighboring valley; valleys are left outside bands. No gap or
smoothing parameter is added. One region has one active/pending lifecycle;
overlapping new candidates are deferred and counted.

Strict boundary crossings end a role period and leave an opposite-role candidate
pending. Entry into the band from the opposite side confirms a retest, activates
the new role, recomputes its base score from that role's completed evidence and
resets age. A gap across the whole band does not confirm a retest. Band IDs and bounds stay fixed across role changes; period IDs identify
individual roles. Completed reaction count, volume and delay update each event. Relative
normalization can also change the score. Evidence accumulates per role over the
observed session; the age clock resets on retest. Timestamped score history
preserves earlier values.

The combined chart has no legend. Its event slider shows new discoveries,
breaks and retests known by that cutoff. Broken segments remain visible; only
active bands receive margin labels. Dotted pre-discovery origins are retrospective.
Opacity along each band uses its score at that event, never the latest score
backfilled over history. Below-threshold intervals are dotted and remain visible;
thresholds control status and margin labels. Gray intervals mean waiting for a
retest. Separate tables distinguish historical broken periods, current waiting
bands and active bands above/below threshold. Intermediate tables show discovery
cores, transitions, age, reaction totals and score contributions. An as-of view
never shows future prices or levels. The session-normalized age bonus remains an uncalibrated heuristic.

Run with `ml4t`. Output belongs under
`D:/TradingML/runtimes/level-book-v8/unbroken-age-bands/<UTC-run>/`.
Evidence aggregation is incremental; candidate profiles are scanned each event
for this bounded research sample. This is not a production checkpoint engine.
Checks cover prefix invariance, new discoveries, retest direction, frozen bounds,
zero age on role change, strict breaks, stable identities, independent reaction
aggregation, live score updates and score bounds.

## 07 — Compiled replay and pressure testing

`07_compiled_replay_pressure_test.ipynb` preserves notebook 06's per-event
discovery and lifecycle rules while moving numeric scans/state transitions into
Numba (`cache=False`). `pressure_engine.py` uses prefix range totals, a price-to-band
owner index, observed-price masks, and compact evidence changes plus shared
normalizers instead of per-band/per-event score dictionaries. Age is reconstructed
at read time. Sequential causality remains; compiled loops replace Python overhead.

`pressure_source.py` is a separate bounded canonical loader for up to 100,000
trades. The notebook uses 50,000 AAPL trades from 2026-08-20, extending the read
window to the regular session because the first hour contains fewer than 50,000
eligible trades. Original eligibility, reporting checks, metadata rereads and
source hashing remain in force. No database writes or raw-file reads occur.

Run all cells in `ml4t` (Numba, Polars, NumPy, Matplotlib, psutil, ipywidgets).
The notebook profiles the old engine, reports compilation separately, compares
every transition and saved score on 2,000/10,000 events, and pressure-tests
2,000/10,000/25,000/50,000 prefixes. An independent direct trade scan audits breaks
and retests, with prefix/future-perturbation checks. It records wall time, retained
array size, process RSS, band growth, pending bands and deferral counts.

Existing bands are **not merged or expired**. Pending zones retain ownership;
overlapping candidates are deferred. The notebook exposes this accumulation
instead of changing the method during optimization. Saved compact NPZ results
are analysis caches, not streaming-resume checkpoints.

The interactive chart keeps event/threshold controls and opacity history, with
active-only or all-history views and optional waiting bands. Exact tables and
scores are retained. Long-view opacity uses a disclosed bounded display stride;
at most 20 strongest qualifying active bands receive margin labels. Rendering
limits do not change discovery or numerical results.

Source caches, per-tier receipts, executed notebooks, profiles, charts and compact
results belong under `D:/TradingML/runtimes/level-book-v8/pressure-test/`.
