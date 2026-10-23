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
active-only or all-history views and optional waiting bands. Role thresholds hide
periods below their current score (active) or last intact score (broken). Waiting
overlays are controlled separately by the checkbox. Exact tables and
scores are retained. Long-view opacity uses a disclosed bounded display stride;
at most 20 strongest qualifying active bands receive margin labels. Rendering
limits do not change discovery or numerical results.

Source caches, per-tier receipts, executed notebooks, profiles, charts and compact
results belong under `D:/TradingML/runtimes/level-book-v8/pressure-test/`.

## 08: Incremental reaction prediction

`08_incremental_reaction_prediction.ipynb` tests a small online count model on
the same verified 50,000 AAPL events and notebook-07 band engine. Run all cells
in order. The first 25,000 events build history; later touches are scored before
their outcomes are admitted. `incremental_prediction.py` holds the shared
extractor and learner. The notebook shows raw trades, encounters, toy arithmetic,
shared/personal counts, duration sums, individual predictions and an as-of viewer.

A fresh approach-side entry after activation starts an encounter. A two-cent
move away from the band's near edge is rejection; a strict opposite-boundary
crossing is a break. Pending encounters are excluded from accuracy but reported.
Shared history is split by role and age at touch (under/over 60 seconds); a
weight-4 blend adds personal role-period evidence without counting it twice.
Reaction durations are stored, not yet used to update pending probabilities.

Evaluation reports accuracy, balanced accuracy, ROC AUC, Brier error, log loss,
calibration and role/age slices against shared-only, role-only, global-online
and frozen-warmup baselines. Prefix replay, toy labels in both directions,
pending/tied-event semantics and update counts are checked in the runnable
notebook. Settings are illustrative and fixed before evaluation. One session
does not establish cross-session accuracy, profitability or calibrated scores.

Executed notebooks, aggregate snapshots, predictions and manifests belong under
`D:/TradingML/runtimes/level-book-v8/incremental-prediction/`. Snapshots are
research evidence, not a complete streaming-resume implementation.

### Full-session history-length comparison

Run `python -B research/level_book/v8/run_history_comparison.py` from the repository
root. The launcher sets `PYTHONDONTWRITEBYTECODE` itself. Defaults use AAPL's
25 canonical sessions ending 2026-08-20, with the final five held fixed for
evaluation and rolling 1/5/20 prior-session histories. All eligible regular-session
trades are read; the bounded loader fails rather than truncating beyond its
two-million-trade capacity. The existing integer-count sample loader is unchanged.

Each history contributes only resolved shared role/age aggregates. Personal
state and bands reset daily. Pending close encounters are reported and never
converted to failures or carried into a new session. Within each evaluation
session, resolved outcomes update the model strictly after their event. Identical
encounter identities/outcomes are asserted across all history lengths.

Outputs under `D:/TradingML/runtimes/level-book-v8/history-comparison/` include
per-session canonical provenance, source hashes, labels, completion receipts,
predictions, paired daily/pooled metrics and calibration. A rerun reuses only
hash- and implementation-matched completed sessions; changed configuration or
code requires a new `--run` directory. No notebook or database writer is involved.
Metrics compare the personal model with shared-only, role-only, global-online and
frozen-history baselines. Five correlated sessions are descriptive evidence, not
a statistical significance or profitability claim.

Measured default run (`aapl-20260820-full`): 7,151,346 eligible trades across
25 sessions; evaluation on 2026-08-14, 17, 18, 19 and 20 used 86,682 resolved
encounters, with four pending encounters excluded. For 1/5/20 prior sessions,
pooled model Brier errors were 0.24603/0.24669/0.24907, versus online global
baselines 0.24414/0.24428/0.24509. Mean per-session model AUCs were
0.54520/0.54303/0.54937: slightly stronger within-session ranking with 20 days,
but weak discrimination and worse probability calibration. Pooled AUCs differ
(0.55044/0.54258/0.54452) because they also compare scores between sessions.
Five thousand independently scanned encounter outcomes, seeded prefix isolation
for all three histories, original notebook-08 prediction parity and hash-verified
source-cache reuse passed. Full receipts are in that runtime directory.

### Separate recency and personal-bonus diagnostics

Run `python -B research/level_book/v8/run_score_ablation.py`. It reuses the
hash-verified full-session experiment above; no source reads from ClickHouse,
band recalculation or notebook are needed. Generated evidence goes under
`D:/TradingML/runtimes/level-book-v8/score-ablation/` in a new run directory.

Each of the 1/5/20-session histories is evaluated on the same five sessions.
Recency-only variants weight prior-session counts and duration sums by
`2 ** (-session_lag / half_life)`, with half-lives 1 and 5 sessions. The previous
session has lag 1; newly resolved current-session outcomes have unit weight.
There is no intraday decay, and the original personal bonus remains unchanged.
Bonus-only variants retain uniform history and use
`p_shared + multiplier * (p_original - p_shared)` for multipliers 0.5 and 0.
The shared estimate still excludes the current role period. No combined
recency-plus-reduced-bonus variant is tested. Matching recency-weighted global
baselines are also reported.

The 20261023T150151337431Z run reproduced all 15 original prediction sets
exactly and passed 30 weighted prefix-isolation checks. On 86,682 resolved
encounters, the primary 20-session comparison had Brier errors: current 0.24907,
global baseline 0.24509, recency H1 0.24640, recency H5 0.24766, half bonus
0.24485, zero bonus 0.24508. Halving the bonus retained mean daily AUC near
0.5494; removing it reduced AUC to 0.5051. The 1-session half-bonus control
had Brier 0.24265 versus baseline 0.24414 and accuracy 58.02% versus 57.61%.
Half bonus beat each matching baseline on Brier on all five days for 1/5-session
histories, and three days for 20 sessions. These are calibration improvements,
not strong level discrimination. The previously inspected evaluation sessions
make this diagnostic reuse, not fresh held-out confirmation.

### Fixed-horizon direction transfer

Run `python -B research/level_book/v8/run_horizon_transfer.py`. This evaluates
the saved ablation probabilities at 1/2/5/10 seconds on the same five sessions,
without fitting the reaction models again. Scores are reproduced exactly before
use. At support, `p(up)=p(rejection)`; at resistance, `p(up)=1-p(rejection)`.
The one-session, half-bonus variant is the prespecified primary model.

Targets use the latest eligible trade at or before the deadline (last ordinal
on a timestamp tie), measured against the touch trade. Deadlines at/after the
regular-session close are unavailable. Unchanged prices are counted separately;
binary accuracy/AUC/Brier condition on price change. Accuracy across every
available outcome is also reported, counting unchanged outcomes as incorrect
for these up/down-only forecasts. Last-trade staleness and no-new-trade counts
are retained. All 86,686 original touches are considered, including the four
encounters without a resolved barrier-reaction label.

Baselines include incoming-direction continuation and historical fixed-horizon
direction frequencies, both global and conditioned on support/resistance. The
frequency baselines use the matching 1/5/20 prior sessions and only current-day
outcomes whose deadlines have strictly passed; ties remain withheld. Saved
reaction-rate baselines are also transferred unchanged. No combined parameter
tuning or threshold search is performed.

The 20261023T155144887180Z run found primary-model changed-price accuracies of
46.07/47.31/48.39/48.90%, versus role-conditioned direction-frequency baseline
56.52/54.66/52.94/51.89%. The original one-session model scored
48.64/49.29/49.74/49.82%. Unchanged counts were 14,089/9,239/5,641/3,850;
close-excluded counts were 133/247/591/1,037. The saved reaction scores do not
transfer successfully to these fixed-horizon targets. This is diagnostic reuse
of previously inspected sessions, with correlated encounters and last-trade
price targets; it is not fresh validation or a profitability estimate.

Evidence under `D:/TradingML/runtimes/level-book-v8/horizon-transfer/` includes
full predictions, coverage, daily/pooled metrics and provenance. Validation
reproduced 15 saved score sets, independently scanned 500 as-of endpoints,
checked 1,200 scalar baseline updates, and checked exact timestamp/close ties.

### Fixed-pair first-boundary-break experiment

Run `python -B research/level_book/v8/run_boundary_race.py`. This reuses the
hash-verified canonical-derived cache and replays August 13 plus the same five
evaluation sessions (August 14, 17, 18, 19 and 20). Outputs live under
`D:/TradingML/runtimes/level-book-v8/boundary-race/aapl-fixed-pairs-v2`.
No notebook or database writes are involved.

Starting at event 200, select the nearest active support entirely below price
and resistance entirely above it. Freeze both identities and their outer band
boundaries until a subsequent trade strictly crosses one boundary. New levels
do not replace an open pair. Only one race is pending at a time; a new race can
start on the previous race's resolution event. Session-close pending races are
reported separately. Missing brackets are counted, not filled with invented
levels. These are new pair episodes on the same sessions, not the individual
touch cohort used by earlier experiments.

The online count model predicts upper-boundary-first using price position
(five bins), last nonzero trade direction, each role's age (below/above 60
seconds), and each boundary's half-bonus reaction estimate (fixed thirds).
It backs off from 540 full cells to 15 position/direction cells, then to the
distance probability `(price - lower) / (upper - lower)`, using fixed weight
20 at each step. Training uses the previous session and only current-session
outcomes resolved strictly before the prediction event. Same-event outcomes
are withheld. Existing reaction estimates follow the same availability rule.
The smoothing weight is an engineering default, not a tuned parameter.

Comparisons include the learned position/direction-only model, distance,
historical upper-first frequency, last-direction continuation, and 50/50.
Fenwick trees select active boundaries in O((events + role periods) log bands)
time; scoring uses constant-size count tables. The existing band replay engine
is unchanged. Validation independently scans sampled future paths and active
boundaries, checks truncated-prefix identity, and reconstructs predictions
from admitted outcome prefixes. Reused evaluation sessions remain diagnostic,
not fresh confirmation or evidence of profitability.

The five-session experiment evaluated 23,399 resolved races from 1,056,104
trades, with five pending at close. After the initial 199 events per session,
27,307 events lacked a valid active bracket (21,581 while no race was pending).
Median resolution time was 1.370 seconds, the 90th percentile 12.938 seconds,
and median frozen outer-boundary width $0.06. Upper-first outcomes were 50.17%.

| Method | Accuracy | Mean daily AUC | Brier error |
| --- | ---: | ---: | ---: |
| Full model, including reaction and age | 63.69% | 0.6792 | 0.22541 |
| Learned position/direction only | 63.81% | 0.6792 | 0.22520 |
| Distance baseline | 63.81% | 0.6847 | 0.23404 |
| Historical outcome frequency | 50.11% | 0.4949 | 0.25020 |
| Last-direction continuation | 55.87% | 0.5589 | N/A |
| Constant 50/50 | 50.17% | 0.5000 | 0.25000 |

The constant predictor's accuracy uses an upper-first tie decision, not a
simulated random coin. The direction rule emits hard 0/1 predictions; its
probability errors are retained in the artifact but not treated as calibrated
probabilities. Both learned models improve Brier error over distance on all
five days. The full model beats distance accuracy on only two days and does
not improve aggregate accuracy or Brier over the simpler learned model. Thus
this experiment supports learning probability calibration for boundary
distance, but does not establish incremental value from the reaction/age
features. Validation passed 1,200 independent first-break/nearest-pair checks,
six prefix comparisons and 500 independent prediction reconstructions.
Reaction probabilities are represented by coarse fixed bins, so this is a
test of this specific simple model, not proof that richer reaction features
cannot add predictive value.
