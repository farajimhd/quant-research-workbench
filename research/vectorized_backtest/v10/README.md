# V10: session profit shortfall

Copied from V9. Execution, inputs, initialization, mutation, lineage, validation
isolation and caches are unchanged. Fresh campaign only; do not resume V9 state.

Cost = V8 cost + concentration penalty + shortfall penalty.
Concentration = sum(max(session_profit,0)^2) / sum(max(session_profit,0)),
zero when the denominator is zero; weight 1.
Shortfall = sum(max(1000 - session_profit, 0)); weight 1.
Both target and weights are configurable, training-only and checkpoint-bound.
An inactive session costs $1000 extra; a -$200 session costs $1200 extra;
a $1000 or greater session has no shortfall. Lower cost is better.
There is no block reward or capped profit reward. Block metrics remain diagnostics.
The existing V8 trade/session tails, drawdown, risk, exposure and complexity terms
are retained. No additional replay or GPU computation is introduced.

User authorized stopping V9 and launching V10 from generation 1 on 2026-10-12.
Preserve V8-format input and initialization certificates and start a separate
V10 runtime and W&B run. Validation is display-only.

## Inherited execution documentation

# V10 robust assumed-fill search

V10 preserves V7's causal feature programs, $1,000 initial entries, $200 adds,
next-observed-close assumed fills, fractional shares, and commission accounting.
It adds no broker, capacity, spread-execution, cash, or pending-order simulation.
V7 source, campaigns, checkpoints, and input artifacts remain unchanged.

## V8 base objective

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

New acceptance outputs prepare validation with `--ranking-before-features`:
opening-known split-adjusted prior-close price eligibility precedes rolling-volume
top-10 ranking. Only the union of ranked identities receives expensive features,
plus identities needed for the next session's prior-day warm-up. This union is a
computation scope, never a replacement for per-clock causal admission. Full
session observations for selected identities remain available after rank exit.
Ranking receipts and source hashes bind the feature scope; compact preparation
recomputes and compares the ranking and checks opening split evidence again.
Use a new output directory when switching from full-market feature preparation.
The legacy preparation path remains available for exact parity comparisons.
Worker source metadata is initialized once per process rather than serialized
again for each listing.

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
guards are not bypassed: validation has new V10 producers/readers with a separate
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
deployment and V10. Both cold/warm repeats disable result reuse. Every original
financial metric must match exactly. A warm speedup is required, but this gate
alone does **not** qualify complete generations with validation overhead.
Full all-training + validation + selection + publication wall time must also be
measured before accepting V10 as faster. No measured speedup is claimed by source
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
python -B -m research.vectorized_backtest.v10.prepare_validation `
  --v7-campaign <completed-v7-campaign> --sessions <original-sessions.json> `
  --output D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v10/validation-inputs
python -B -m research.vectorized_backtest.v10.qualify_workstation `
  --campaign <completed-v7-campaign> `
  --output D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v10/qualification
python -B -m research.vectorized_backtest.v10.run_search `
  --training <training-manifest.json> --validation <validation-manifest.json> `
  --output D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v10/campaigns/NEW-RUN `
  --population 1024 --batch-size 512 --generations 50 --session-workers 2
python -B -m research.vectorized_backtest.v10.observe --output <campaign> --view validation
```

`STOP` takes effect after a durable generation. Exceptions preserve diagnostic
status and completed training/validation seals; resume verifies them. Prepared
validation features use the canonical source and the same V6 feature encoder.
`prepare_validation` requires V7 completion before opening the held-out sources.
Qualification/profiling authorization does not itself launch a 50-generation V10
optimization campaign.

## Nonblocking W&B metrics

`wandb_observer` is a separately launched, CPU-only process. Search does not
import, start, queue work to, or wait for it. It reads existing completed split
rankings and the full-generation wall receipt, including from an older V10
deployment. A network outage or SDK shutdown can delay only the observer.
No changes to selection, validation, replay, or campaign checkpoints are needed.

The default project is `optimization-v10`. Each generation sends about 30 scalars
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
python -B -m research.vectorized_backtest.v10.wandb_observer `
  --campaign D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v10/campaigns/NEW-RUN `
  --output D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v10/observers/NEW-RUN `
  --project optimization-v10 --entity mehdifaraji --follow
```

Run this in a separate process. It may start before the campaign exists. `--follow`
retries SDK upload errors every 30 seconds and stops when the campaign completes
or stops; remove the observer's `STOP` file before restarting. Without `--follow`
it uploads available completed generations and exits. The stable run ID and SDK
resume cursor prevent normal restart duplicates; local enqueue status is never
claimed as cloud acknowledgement. Immutable small summaries are retained locally
for recovery. `--mode offline` exercises the SDK without uploading. Observer
status distinguishes waiting, queued-to-SDK, retry, upstream stop and completion.

## Initial sampling from V7

For the final run, prepare a frozen population with `python -B -m
research.vectorized_backtest.v10.initial_population --v7-campaign <campaign>
--stopped-v7-receipt <authorized-stop.json> --output
D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v10/initialization/population.json`.
Then pass that file to `run_search --initial-population` alongside the normal
training, validation and output arguments. No optimization starts during preparation.
Default: 1024 candidates, at most floor(10%) = 102 V7 seeds and 922 fresh random
candidates. Fraction, population, RNG seed and similarity threshold are explicit options.

V7 seeds come in descending V7 training-score order from the last completed
generation, reconstructed deterministically and verified against every generation's
population hash and sealed session receipts. The checkpoint's successor population
is never indexed with the preceding generation's ranking. Ranking is independently
recomputed from training receipts. Exact effective-program duplicates are excluded;
candidates with both session-profit and closed-position-count cosine similarity
at least 0.98 to an accepted seed are excluded. This is a compact behavioral proxy,
not a guarantee of different trades. It runs once; no per-generation similarity cost.
Any diversity shortfall is explicitly reported and filled with unique random candidates.
Seeds retain V7 generation/candidate provenance; V10 ancestry starts at import because
V7 did not persist parent links. Sampling, hashes and selected indices are frozen in
the initialization artifact and checkpoint contract. Validation is never read.
Matched-workload V7/V10 profiles retain their identical comparison populations.

Graph-cache experiments use `--capture-gib` on `run_search`, `qualify_workstation`
or `workstation_acceptance`. The unchanged default is 4 GiB **per worker**, split
across its retained plans; the explicit bound is 0.25 through 16 GiB. Admission
includes this allocation in each worker's memory budget. Profiling changes only
the V10 budget, retains the V7 baseline, and records the setting in its receipt.
The search checkpoint freezes the setting for exact resume. Larger caches are
experimental until measured; no larger default is assumed to be faster.
