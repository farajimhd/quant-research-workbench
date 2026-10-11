# V8 robust assumed-fill search

V8 preserves V7's causal feature programs, $1,000 initial entries, $200 adds,
next-observed-close assumed fills, fractional shares, and commission accounting.
It adds no broker, capacity, spread-execution, cash, or pending-order simulation.
V7 source, campaigns, checkpoints, and input artifacts remain unchanged.

## Objective

Minimize `-trimmed_profit + 0.25*N*trade_tail_loss - 0.25*D*session_tail_mean
+ 0.25*sum_session_drawdown + 0.10*risk_dollar_hours
+ 0.002*capital_dollar_hours + 9.375*K`.

The initial configuration removes the best 10% of complete positions, penalizes
the mean loss in the worst 10%, and rewards the mean profit in the worst 20% of
sessions. Exact fractional boundary weighting avoids integer rounding jumps.
Positions encompass additions, partial sales, final liquidation and commissions.
Each coefficient and percentile is immutable within a campaign and configurable
through `--objective JSON`. The per-node default is V7's 30-session equivalent.
K counts simplified reachable nodes plus enabled age conditions once at birth.
Zero-position strategies have no empirical trade distribution and cannot be parents.
Inactivity has no direct penalty; participation and profitable-session rates are
diagnostics. Validation cannot select coefficients or candidates.

The bounded per-candidate/session episode buffer defaults to 65536 positions.
An overflow fails explicitly before any score is published. It never truncates
authoritative positions. Training score verifies episode-count and profit sums
against financial replay. Tail sorting/reduction uses NumPy segmented operations.

## Selection and lineage

Five generations of parent-child links exclude related crossover mates; merely
surviving as an unchanged elite does not age an ancestor out. Elites are
chosen by training score; generation recency only breaks exact ties. Up to 10%
of the elite budget admits additional relatives; insufficient independent
families leave room for new offspring rather than relaxing the ancestry limit.
Fresh sampling, typed crossover and expanded local/discrete mutations generate
unique effective programs. Exhausted mate searches fall back to mutation and
report their counts. Exact uniqueness has a bounded retry budget.

IDs such as `G012-000047-M006-X002-a91f` remain stable for unchanged elites.
Counters follow the primary-parent path; both parents and the full durable birth
ledger preserve the ancestry DAG. Numeric parent gathers update counters at
generation boundaries; bounded vectorized membership checks replace all-pairs
behavior comparisons. No trade-overlap or P&L-correlation penalty runs in selection.

## Display-only validation

Every generation freezes the top one batch by training score and constructs the
successor before evaluating validation. Validation uses separate process lanes,
source roots, receipts, result caches, and ranking. It does not access or consume
the training RNG or affect selection, mutation, crossover, early stopping, or
checkpoint choice. A validation candidate with no trades remains visible.

The V7-style terminal supports `F` training and `V` validation, with stable IDs,
training rank in the validation table, net/trimmed profit, tails, participation,
complexity, listing contribution and turnover. The previous complete validation
table stays visible with its generation while the next panel evaluates.

Explicit versioned split manifests bind sorted dates and input/history receipt
hashes. Training and validation must be chronologically disjoint. Legacy source
guards are not bypassed: validation has new V8 producers/readers with a separate
display authorization. Feature encoding, canonical source reads, causal masks,
and all hash/shape/time checks are retained. No raw SIP fallback is available.

## Speed and correctness gates

Identical lifecycle programs within a batch evaluate once and expand their
signals to matching candidates. A bounded plan cache reuses capture shapes
across sessions. Exact strategy/source/data/accounting keys reuse unchanged
candidate results. Reuse is split-specific and integrity checked. Source version,
objective, selection, seeds, population and RNG are resume-bound.

After V7 completes and releases ownership, `qualify_workstation` compares its
frozen final population on complete training sessions under the immutable V7
deployment and V8. Both cold/warm repeats disable result reuse. Every original
financial metric must match exactly. A warm speedup is required, but this gate
alone does **not** qualify complete generations with validation overhead.
Full all-training + validation + selection + publication wall time must also be
measured before accepting V8 as faster. No measured speedup is claimed by source
implementation or synthetic tests.

An explicitly user-authorized early V7 stop can be qualified with
`--stopped-v7-receipt PATH` on `workstation_acceptance`, `qualify_workstation`,
and `prepare_validation`. This binds the preserved checkpoint and last complete
generation seal, verifies every session receipt and confirms recorded processes
and ownership locks are gone. Partial next-generation files are retained but
excluded. Without that explicit receipt, the original full-completion gate remains.

## Commands

All generated artifacts go beneath `D:/TradingML/runtimes`; every launcher sets
`PYTHONDONTWRITEBYTECODE=1`. Run only from a committed verified deployment.

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B -m research.vectorized_backtest.v8.prepare_validation `
  --v7-campaign <completed-v7-campaign> --sessions <original-sessions.json> `
  --output D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v8/validation-inputs
python -B -m research.vectorized_backtest.v8.qualify_workstation `
  --campaign <completed-v7-campaign> `
  --output D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v8/qualification
python -B -m research.vectorized_backtest.v8.run_search `
  --training <training-manifest.json> --validation <validation-manifest.json> `
  --output D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v8/campaigns/NEW-RUN `
  --population 1024 --batch-size 512 --generations 50 --session-workers 2
python -B -m research.vectorized_backtest.v8.observe --output <campaign> --view validation
```

`STOP` takes effect after a durable generation. Exceptions preserve diagnostic
status and completed training/validation seals; resume verifies them. Prepared
validation features use the canonical source and the same V6 feature encoder.
`prepare_validation` requires V7 completion before opening the held-out sources.
Qualification/profiling authorization does not itself launch a 50-generation V8
optimization campaign.

## Nonblocking W&B metrics

`wandb_observer` is a separately launched, CPU-only process. Search does not
import, start, queue work to, or wait for it. It reads existing completed split
rankings and the full-generation wall receipt, including from an older V8
deployment. A network outage or SDK shutdown can delay only the observer.
No changes to selection, validation, replay, or campaign checkpoints are needed.

The default project is `optimization-v8`. Each generation sends about 30 scalars
and the readable leader ID. Train and validation metrics refer to the **same
training-selected leader**, with per-session profit, trimmed profit, trade/session
tails, worst session drawdown, profitable/active session rates and position count.
Raw scores are separately labeled because split lengths differ. Shortlist medians
use the same training-selected candidates on both splits. Other charts show K,
generation/validation seconds, exact-result reuse and unrelated-mate fallbacks.
No-trade candidates remain in the validation shortlist median.

Console capture, source/git, package lists, machine metadata, system metrics and
job creation are disabled explicitly. No artifacts, trades, rankings, datasets,
checkpoints or raw logs are uploaded. W&B still stores its normal run identity
and metric history. SDK scratch data stays under the observer runtime directory.
Credentials use environment/shared secret discovery and are never added to metrics.

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B -m research.vectorized_backtest.v8.wandb_observer `
  --campaign D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v8/campaigns/NEW-RUN `
  --output D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v8/observers/NEW-RUN `
  --project optimization-v8 --entity mehdifaraji --follow
```

Run this in a separate process. It may start before the campaign exists. `--follow`
retries SDK upload errors every 30 seconds and stops when the campaign completes
or stops; remove the observer's `STOP` file before restarting. Without `--follow`
it uploads available completed generations and exits. The stable run ID and SDK
resume cursor prevent normal restart duplicates; local enqueue status is never
claimed as cloud acknowledgement. Immutable small summaries are retained locally
for recovery. `--mode offline` exercises the SDK without uploading. Observer
status distinguishes waiting, queued-to-SDK, retry, upstream stop and completion.
