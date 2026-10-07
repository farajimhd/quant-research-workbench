# Vectorized backtest v4

V4 implements all 147 V6-schema feature channels plus two causal split flags,
shared 120-observed-candle
history, typed variable-length lifecycle rule programs and a stability objective.
This version is independent of v2/v3. Its copied broker preserves the shared
25% approximate liquidity, costs, cash, partial fills and stop-risk contracts.
V6 feature/schema utilities are reused only by the explicit offline producer;
V6 teacher labels, oracle targets, model checkpoints and losses are not inputs.

## Active entry points

- `torch_backtest.bootstrap`: bind the existing 30-training/six-validation split
  to compatible extracted feature banks and immutable execution snapshots.
- `torch_backtest.extract_features`: separate SELECT-only missing-bank producer;
  emits feature banks and identity maps, no labels and no market-data writes.
- `torch_backtest.optimize` / `run_search`: file-only optimizer and exact resume.
- `torch_backtest.observe`: single-owner read-only Rich terminal dashboard.
- `torch_backtest.qualify`: CPU/CUDA rule and full-ledger synthetic parity,
  requiring same-source real full-session B128 profile evidence first.
- `torch_backtest.audit`: all30 receipt/hash/population/objective reconciliation.
- `torch_backtest.sync_workstation`: committed/pushed immutable deployment.

The inherited `run_grid`, `legacy_search`, source adapters and population-study
modules are compatibility/reference mechanics, not V4 full optimization launchers.

## Search and objective contract

Every feature and its validity mask is represented in the bank. The premarket
search excludes the three session-regime operands (`premarket`, `regular`,
`after_hours`), leaving 146 searchable channels; bank IDs and history remain
intact. Programs may use typed
constants, feature-to-feature comparisons, arithmetic, crossings, Boolean
composition, lag, differences, sliding mean/min/max. Each candidate has entry,
exit, trailing-amendment and replacement rule outputs. Insertion/deletion lets
ruleset/operand counts vary, under 32 instruction nodes per output; total chained
history dependency cannot exceed the preceding 120 observed candles. This is a
resource envelope, not a claim to exhaustive or unlimited program search.

Six class/count policy coordinates and 40 bounded policy coordinates remain
searchable. Four legacy entry-mode coordinates and 27 legacy clause/connectors
are canonical, inactive storage; they are not advertised as active V4 genes.
The broker's initial stop/target, sizing, retry and ranking policies remain
bounded policy choices. V4 does not yet emit arbitrary numeric stop/target/rank
values from its program outputs; those require a separately qualified extension.

Maximize .5 median daily net return + .5 mean daily net return excluding the best
day - .25 worst20%-tail daily loss - .25 mean normalized daily drawdown
- .10 mean normalized stop-risk hours - .002 mean normalized capital hours
- .001 active instruction count /32. Median averages the middle pair for an
even session count; tail uses ceil(0.2*n) worst returns and clips positive tail
returns to zero loss. Feasibility requires positive mean return excluding best
day,1..20 filled acquisition batches each day and terminal flatness. Daily cash
resets to $10,000. Admission stop risk is capped at 2% equity; minimum
nonprotective hold3s,maximum hold queues an exit after3600s.

Sharpe is reporting only: sample SD of daily net returns, zero risk-free rate,
daily ratio and annualized estimate using sqrt(252), plus ex-best-day estimate.
Undefined variance/insufficient samples become JSON null, not zero or infinity.
Selected-session annualization is an estimate, not annual profitability evidence.

## Causality and integrity

At t process prior orders for completed interval[t-1,t), then decide from features
available by t. New orders/amendments cannot act in that signal interval. Broker
OHLC ambiguity retains the inherited conservative stop-before-target ordering.
Financial time is integer UTC seconds and the validated full one-second clock;
sparse feature history never advances holding or risk time by candle count.
Feature program windows use observed-candle counts explicitly.

A V7 slot is a nearest-level context, not a persistent level identity. Rolling
slot values describe that context. No claim of tracking one level across slot
replacement is made. Missing evidence fails closed even through NOT/OR.

Bank bytes/producer plans, source certificates, identity maps and execution
snapshots are verified; observed bank prices must reconcile against broker marks.
Files are treated as immutable: hash once on load, check size/mtime on reuse;
full audit must rehash inputs before final delivery. Host execution cache is
bounded320GiB; feature residency24GiB, broker tape12GiB and financial state8GiB.
These are defaults to profile, not measured optimal allocations.

CPU code orchestrates metadata, bounded listing chunks, evolutionary construction
and receipts. Candidate/candle arithmetic is Torch, and financial tick has no
per-candidate/ticker Python loops. Shared features are not copied into overlapping
candidate histories. Financial graph pointers are reused across compatible
session shapes. Further GPU prefetch/residency tuning is pending workstation
profiling; no speedup is claimed in advance.

## Launch gates and current status

V4 broker snapshots must be built from the same certified market-build revision
as the feature bank; the old V3 snapshots are incompatible with the corrected
V6 producer. `bootstrap` requires explicit market manifest/ledger arguments and
allocates V4-owned execution roots. `prepare_execution` is a SELECT-only training
snapshot producer. It retains the price envelope and admits at a completed 1s
price; it does not require the fixed V3 squeeze signal. The searched entry program
decides when to trade. Final validation preparation through `extract_features`,
`prepare_execution` and `certify_splits` requires `--frozen-winner`, whose exact
full-budget identity, checkpoint, generation hashes and passed audit are checked
before market reads.

V4 requires an immutable opening-as-of split certificate for every consumed
feature bank. `certify_splits` reads the existing canonical reference authority
with execution_date<=session and inserted_at<=04:00 ET; it writes only a runtime
sidecar. Prior OHLC is restated by split_from/split_to, prior volume by its
inverse, and same-clock RVOL uses a denominator on the current share basis.
Ratio indicators and V7 distances stay invariant; historical fundamentals retain
their original point-in-time semantics. Raw banks and broker prices are unchanged.
Missing/conflicting certificates fail closed. Standard session-opening actions
are supported; date-only reference evidence does not establish support for an
intraday corporate-action event. Accounts begin flat and finish flat each day.
Forward/reverse split conversion and loader binding have focused CPU coverage;
historical split evidence and GPU qualification remain launch gates.
The 147 V6 input IDs remain unchanged. V4 appends two searchable Boolean
inputs: split_this_session (either direction) and reverse_split_this_session.
They come from the opening-known sidecar and can combine with the existing
float, shares outstanding, split presence and split-age channels. Historical
prefix flags describe their original session, not the current session.

Implementation is under qualification. Do not start a full campaign until all30
training banks/identity maps/execution snapshots are certified and the same-source
B128 workstation qualification passes. Full optimization budget defaultsB128,
32generations,seed20261005. V4 owns this split; V6's label/model research is unchanged.
Validation input data stays unopened until a feasible final winner is frozen,
then default/winner are evaluated once per session with receipt reuse on resume.
No feasible winner leaves validation sealed.

V4's private offline execution certificate binds the core-complete producer
manifest and read-only ledger to each selected broker attempt. It checks the
broker/bar canonical source fingerprint, complete physical row counts, unique
keys and full content hashes, including data outside the replay window. Storage
policy and actual active-part placement must pass the existing SSD checks.
This research certificate creates no app-release fence and grants no app or
live trading authority. It replaces the copied app-release requirement only
for V4's independently certified offline research preparation. Opening-known
V6 references feed the unchanged causal V7 stream; certified missing prior
V7 evidence masks structural targets and never creates an artificial seed.
Broker products use the dedicated broker read principal. Reference preparation
uses the existing reference-reader configuration with SELECT-only SQL guards
and readonly=1; it does not grant additional permissions to the broker principal.

Use PYTHONDONTWRITEBYTECODE=1 for all repository Python invocations; artifacts
belong under D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v4.
The installed pinned Torch/Triton toolchain may be read from the existing v3
compiler dependency directory; no V3 strategy source or runtime is modified.

Terminal views: F financial, P performance, C objective. Compact windows retain
primary metrics and expose full data in status.json; pipes emit no cursor codes.
Run examples from a committed immutable workstation checkout:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B -m research.vectorized_backtest.v4.torch_backtest.optimize --sessions <sessions.json> --output <new-runtime> --execute --profile
python -B -m research.vectorized_backtest.v4.torch_backtest.qualify --sessions <sessions.json> --profile <profile.json> --output <new-qualification-runtime>
python -B -m research.vectorized_backtest.v4.torch_backtest.optimize --sessions <sessions.json> --output <new-experiment> --execute --qualification <qualification.json>
python -B -m research.vectorized_backtest.v4.torch_backtest.observe --output <experiment>
```

Before final delivery complete input rehash,receipt/metric audit,task-owned docs,
commit/push and owned-worker/monitor cleanup. Preserve failed receipts and all
unrelated user edits. Stop V3 only after V4 implementation validation, before
exclusive workstation GPU profiling; never restart a stopped V3 campaign.
The receipt audit independently reconstructs cash, fees, position quantities,
closed-position win counts and share-weighted elapsed holding time from actual
fills. It rejects overselling, spending unavailable cash, invalid fill order and
metric disagreements. Position win rate is reporting evidence, not an added
fitness term. Each experiment identity seals the fixed financial settings.
# Sealed input preparation

V4 feature extraction requires `--sessions` and checks the requested day,
output bank, manifest and ledger against that owned plan before market reads.
Training days are available for preparation. Final validation preparation
(feature extraction, broker snapshot and split certification) also requires
`--frozen-winner`: an exact full-budget winner with a passed training audit
bound to the experiment identity, checkpoint and generation receipts.
An arbitrary winner JSON does not authorize final-session inputs.
After freeze, unpublished final artifacts produce `awaiting_validation_inputs`
and exit code 3, with the worker lock released. Prepare those artifacts using
the audited frozen winner, then use the exact original command with `--resume`.
Completed training is retained; final evaluation receipts remain immutable.

`--ticker-capacity` fixes the inactive-padded ticker axis across training
sessions, allowing compatible sessions to reuse a captured financial graph.
Choose it from the maximum width of all 30 certified training inputs, rounded
up to 64. A smaller capacity fails rather than dropping tickers, and padded
memory must fit the declared tape budget before device allocation. The value
is sealed in the experiment identity. Final validation uses its own full width
after winner freeze; its inputs never inform the training capacity.

The dashboard's `T` view reports closed-position holding time in actual elapsed
seconds: minimum, mean, median, P90 and maximum, from first buy fill to final
sell fill. Partial fills remain one position until fully closed; reusing a slot
starts a new timestamp. Cross-session quantiles pool the actual position
durations, rather than averaging daily quantiles. Share-weighted holding time
and current open-position age are separate statistics. Empty samples display
as undefined. These metrics do not change fitness or financial execution.

Training uses an ordered, single-worker CPU prefetch queue. While the current
session executes on CUDA, the next training session is loaded and verified.
Only one session is queued; CUDA transfers remain on the consumer thread.
Session-specific split metadata is isolated while immutable bank arrays are
shared. Receipts report both loading time and the unhidden `prefetch_wait`;
wall-clock session time includes only that wait, avoiding double-counting
overlapped preparation. `--profile --profile-sessions 2` measures overlap on
two complete training sessions without selection or validation access.

The qualified 52b8e9c83 workstation revision profiled two full B128 sessions.
The second session prepared inputs for 48.529 seconds with only 0.0000134 seconds
of residual wait; graph reuse avoided another compile and its total was 392.512
seconds. In the live campaign, the first overlapped session hid 59.497 seconds
of preparation with 0.0000174 seconds of wait, but took 421.985 seconds overall
because transfer and rule preparation varied by day. These are scoped timings,
not a whole-campaign speedup estimate. The initial continuation reused the first
receipt instead of replaying it, so its next cold load still had a 27.995-second
wait. Financial fill counts and valid ledger entries matched exactly between
fresh and reused full-session graphs on July 31.

Display revisions may run from a separate immutable UI checkout while the
financial worker retains its qualified source. The current read-only renderer
uses 6e28822be; the worker uses 52b8e9c83. Profile counts follow the identity's
actual session count. Live population P&L, pooled win rate, profit factor and
holding times remain visible before any completed leader exists. They are
separate from completed strategy metrics. During next-session preparation, the
previous completed replay is labeled `Last backtest s`, with no stale zero ETA.

The explicitly authorized `--continue-from` operation supports a stopped
campaign before its first generation completes. It requires unchanged inputs,
financial settings, objective and search budget; reconstructs the exact initial
population from the original seed; verifies and copies only complete session
receipts and ledgers; and records both source identities in `continuation.json`.
The parent directory remains immutable. This operation requires a new
same-source workstation qualification and a new output directory. Ordinary
checkpoint resumes continue to require exact source identity.

