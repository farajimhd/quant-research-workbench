# Squeeze grid Torch backtest v2

Status: implementation and synthetic qualification; historical experiments await
user approval. This is an approximate research simulator, not live broker parity
or a published numbered strategy. Source availability has not been checked by a
historical run. Missing certified coverage fails preflight; it is never built here.

## Grid awaiting approval

| Dimension | Choices | Count |
|---|---|---:|
| Entry | signal; VWAP cross held 2s/5s; breakout/retest; MACD subsets | 30 |
| Independent positions per ticker entry batch | 5, 10, 15 | 3 |
| Cash weights by target ordinal | equal, inverse log, log | 3 |
| Target | percentage ladder, structural ladder | 2 |
| Trailing | adaptive movement, percentage steps | 2 |
| Initial stop | percentage, confirmed swing low | 2 |
| Portfolio rotation | disabled, enabled | 2 |

Total: **30 × 3 × 3 × 2 × 2 × 2 × 2 = 4,320**.

MACD timeframes are 1s, 5s, 10s, 30s. Four singleton choices, six pairs with
ANY/ALL, four triples with ANY/ALL and one quadruple with ANY/ALL give 26
distinct choices. Open means completed MACD line > signal line; neither positive
line nor increasing histogram is implied. Singleton ANY/ALL duplicates are absent.

`grid.py` is the canonical exact grid and fixed settings. All v2 Python bytes,
the declared behavior version, complete candidates, fixed settings and execution
contract enter the approval digest. A code/settings change invalidates that digest.
No numeric threshold tuning is performed by this first grid.

## Fixed assumptions to approve

| Concern | First grid setting |
|---|---|
| Decision/broker clock | completed 1s, next-interval eligibility |
| Initial account | $10,000 per candidate, independently reset each session |
| Signal population | existing released 100ms Early Squeeze SQL; $1–$50 source envelope |
| Watchlist lifetime | persists through the configured session; first admission remains causal |
| Signal entry | first completed 1s boundary at/after the signal; no delayed signal-only retry |
| VWAP hold | actual observed below-to-above cross after admission; 2s or 5s elapsed |
| Missing hold evidence | reset; require a new evidenced crossing |
| Retest | previous five completed observed highs; within 0.5% of break level, 30s timeout |
| Retest continuation | later completed close above retest high, still above VWAP |
| Common gates | fresh quote ≤1s; spread ≤1%; ≥$1,000 eligible notional and ≥5 trades in last 1s |
| Cash allocation | all free cash divided among M positions; residual rounding remains cash |
| Competing tickers | highest current opportunity score, stable ticker order for ties; one new batch per account/second |
| Acquisition | one submission batch/ticker/session; no additions or second entry batch |
| Acquisition limit/deadline | proposal ask +1%; pending until entry cutoff, no resubmission |
| Minimum size | all M positions must have ≥1 share; otherwise no batch is submitted |
| Fees | $0.005/share, $1 minimum per distinct order; partial fills accumulate once |
| Participation | 10% of eligible interval volume, shared across all orders and both sides |
| Percentage targets | j ×2.5% above each position's actual average fill: up to 37.5% for M=15 |
| Structural targets | first M distinct current resistance identities; lower edge −$0.01, frozen at submission |
| Structural insufficiency | reject ticker/configuration boundary; never replace targets or reduce M |
| Percentage initial stop | 3% below first actual fill average |
| Swing initial stop | confirmed 2-left/2-right observed 1s pivot low, minus $0.01 |
| Adaptive trail | mean absolute close-to-close movement over 10 completed consecutive post-fill intervals ×3; minimum distance 1% of average entry |
| Percentage trail | every 3% rise earns 1% of entry-price stop increase |
| Stop ratchet | upward only; amendments after interval matching, capped below current fresh bid |
| Same-bar stop/target evidence | old position only; stop-first, exit eligible in following interval |
| Final liquidation | queue market-style exit 60s before session end; no invented final fill |
| Residual exposure | invalid candidate, null fitness, explicit quantity/count; never silently dropped |
| Objective | (net P&L −0.5 × maximum sampled drawdown)/initial cash |

The swing is a causal rule selecting a local minimum from certified completed
price bars, not an approximated indicator or retrospective pivot. A five-bar
window including two later observations confirms it; it becomes available to
entry on the next decision. Flat tied minima are allowed. Missing extrema do
not create a pivot. One-second sampled VWAP hold cannot prove intra-second hold.

Volume/notional and cumulative session VWAP come from pinned
`arte.liquidity_100ms_v1`; execution VWAP is interval notional/volume, distinct
from cumulative session VWAP. SQL aggregates before transfer. Each interval's
execution price is its VWAP ± half of the completed quote spread. Buy fills
must meet the working acquisition limit; target fills must meet their limit.
Stops/rotation/terminal exits are market-style. This is an explicit coarse
liquidity approximation; no within-bar trade sequence or queue depth is invented.

## Portfolio rotation

The same base score applies to current candidates and held positions:

```
0.30 × clipped 5s momentum
 + 0.20 × clipped distance above VWAP
 + 0.20 × relative dollar activity (previous 10s mean)
 + 0.15 × executable liquidity / initial account cash
 + 0.15 × remaining upside / (upside + stop downside)
 - 0.15 × stagnation (held positions only, capped at 60s)
```

Momentum and VWAP strength normalize at 3%; relative activity caps at 3×.
The 5s momentum reference is the completed close five decision intervals ago.
This is a deterministic heuristic, not estimated future profit. Past ticker P&L
is not used. Rotation is considered only if free cash cannot fund all M new
positions. It closes one weakest eligible position, not its whole ticker batch.
Require a score advantage ≥0.15 for 3 elapsed seconds with the same incoming
ticker/outgoing position, with a 30s cooldown. Pending acquisitions/protection
exits are excluded. A request waits for its exact outgoing position to finish,
then revalidates the incoming setup and cash. If it fails, the proceeds stay cash.
The incoming acquisition fills no earlier than the interval after submission.
Protective stops take precedence over a pending rotation. Cash is immediately
reusable in this simulated account; broker settlement is outside this model.

## Package independence and execution shape

Copied foundations: v1 Torch compiler/data, scalar graph and ATen program;
local typed encoding core/catalog/config/ClickHouse preparation/vocabulary;
local read-only ARTE manifest/SQL/liquidity certificate helpers. Imports were
rewritten into v2. The original `ReplayRunner`, Strategy 1 implementation,
candidate/static filters and genetic optimizer are not imported or modified.

The new runner has one fixed semantic parameter table. Choices switch tensor
masks instead of compiling 4,320 Python functions. State is `[B,N,15]`, setup
state `[B,N]`, cash `[B]`, and market history `[H,N]` shared across candidates.
Broker/protection/order state and liquidity are independent across B. All slots
within an account share capacity and reservations. The hot tick has no host
reads, dataframe/database work, or Python loops over B/N/positions. Sequential
time is retained. Compiled CUDA graphs capture 16 ticks by default with an exact
remainder. Candidate values update in-place with fixed batch shape.

The low-level batch size defaults to 8, bounded at 1,024. Tape/state/ledger memory has explicit
guards; no hidden CPU fallback or input truncation. V7 preparation loads and
validates the complete prior-session book at 04:00 New York, applies only splits
available then, and advances the canonical streaming engine on completed current
session 1s OHLC/volume. Current-session end-of-day checkpoints are forbidden.
The exact sequential reaction fitter runs once per ticker in a bounded CPU pool,
not inside candidate replay. NumPy comparisons across levels remain vectorized.
Sorted nearest 15 resistance prices are stored as shared `[T,N,15]` FP64 tensors;
15 is the maximum strategy M, not a truncated source book. Regular/after-hours
preparation retains the full 04:00 prefix. Missing bars never forward-fill a
structural validity clock. Valuation retains the latest completed valid close
when a subsequent bar has no eligible last-price update; such seconds remain
unobserved and cannot supply entry, extrema or structural evidence. The tape
seal includes the preparation implementation as well as input authority.

Per-ticker immutable caches seal prior checkpoint, decoded book, causal splits,
OHLC/quote clocks, source certificate, numerical-library versions and canonical
engine code. Reuse still revalidates the prior checkpoint. Atomic completion
receipts and kernel process locks support restart after interruption. Cold
structural preparation and verified cache hits appear separately in terminal
progress and tape provenance. `--structural-workers 0` automatically reserves
25% CPU/RAM, budgets at least 512 MiB per worker, and caps the pool at 32; explicit
positive counts are bounded by that resource guard. NumPy/SciPy and threadpoolctl
are required in the research environment. GPU calibration slices the shared
structural tensors with its market prefix.
The output ledger is bounded; exhaustion rejects execution instead of dropping
fills. Final padded batch lanes are excluded from exported counts and ledgers.
Every submitted parent has a normalized order row with requested/filled/canceled
quantity, submission/deadline, stop/target and separate exit-order fee histories.
Requested shares reconcile exactly to fills, pending shares and explicit
deadline/protection/terminal cancellations. Unfilled and partially filled order
counts remain visible even after their remainder is canceled.

## Planning, approval and execution

Run from the repository root in PowerShell:

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
& C:/Users/g835l/miniconda3/envs/ml4t/python.exe -B -m research.vectorized_backtest.v2.torch_backtest.run_grid
```

This **only** writes the review grid under
`D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v2/plans/<id>`. It does
not connect to ClickHouse or replay prices. `--settings FILE` permits complete
fixed-setting overrides and produces a different digest. The historical path
requires `--execute --approval-digest APPROVED_DIGEST`, explicit `--manifest`,
`--ledger` and `--dates`. Dates, source, code, account settings and campaign
identity are frozen. Do not execute before the human approves the displayed grid.

Historical source preflight also requires the shared dedicated Backtest read
principal and Keeper-attested source certificates, all four completed MACD
resolutions, full squeeze population identity and general V7 prior-session
checkpoint coverage. Strategy 1 candidate/clock/interval tables are not required
for this independent population. The package derives exact causal streaming
levels into its private research cache; it never writes ARTE tables or regenerates
indicators. Missing general seed or certified market coverage fails closed.

Campaign receipts expose active/queued/completed/failed units, preserve completed
batch hashes and support `--resume PATH`. An interrupted active batch is replayed
from the original account; completed batches are verified and skipped. Source,
grid or code changes prohibit resume. Runner state snapshots also support exact
prefix restart. Every session/candidate pair is reconciled before the aggregate
CSV is ranked. Candidates invalid in any session have null aggregate fitness.
No holdout dates are selected implicitly, and no inactivity constraint is invented.

All grids, receipts, ledgers, caches and reports stay beneath the runtime root.
V2 has no services, writers, migrations, publication or live-order integration.

## Validation

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
& C:/Users/g835l/miniconda3/envs/ml4t/python.exe -B -m pytest research/vectorized_backtest/v2/torch_backtest/tests -q -p no:cacheprovider --basetemp D:/TradingML/runtimes/vectorized_backtest/v2-squeeze-tests
& C:/Users/g835l/miniconda3/envs/ml4t/python.exe -B -m research.vectorized_backtest.v2.torch_backtest.validate --device cuda --seconds 120 --listings 16 --batch 8
```

These commands use synthetic fixtures only. Tests qualify causal prefixes,
independent position targets/stops, hold/retest/MACD rules, integer partial fills,
shared capacity/cash, cumulative commissions, structural insufficiency,
rotation sequencing, accounting, fail-closed buffers, candidate reset and exact
checkpoint restart. GPU qualification compares full fill ledgers/account metrics
and in-place candidate updates. Synthetic timing is not full-market throughput,
profitability or certification that current historical products are available.

## Workstation launcher

Run `run_workstation.py` directly with the workstation `ml4t` interpreter.
No arguments starts the approved grid over **all available dates, premarket**.
The launcher never installs dependencies, starts a producer or writes market tables.

The workstation's PyTorch 2.12/CUDA 13.2 environment initially lacked Triton.
`setup_gpu.py` explicitly installs `triton-windows==3.7.1.post27` under
`D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v2/dependencies`.
The launcher discovers that pinned compiler when the base environment has none;
it never upgrades shared environments or silently chooses a different compiler.
The separate setup command is reproducible; normal runs do not install packages.
The task-local `CC` path points to the wheel's bundled TinyCC because a
`--target` installation is outside Python's normal site-packages. Compiler scratch
and caches remain under the runtime root; existing explicit `CC` is respected.

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
$launcher = 'research/vectorized_backtest/v2/torch_backtest/run_workstation.py'
python -B $launcher dates
python -B $launcher preflight
python -B $launcher run
python -B $launcher run --date 2026-09-18 --sessions regular
python -B $launcher run --from 2026-08-18 --to 2026-09-18 --sessions premarket
python -B $launcher run --from 2026-08-18 --to 2026-09-18 --sessions afterhours
python -B $launcher run --date 2026-09-18 --sessions premarket regular afterhours
python -B $launcher run --resume D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v2/jobs/JOB_ID
```

Verified read-only on October 2: published scope plus completed V5 manifests
cover **36 XNYS trading dates, July 30–September 18, 2026**, in two builds.
The catalogue is rediscovered at launch. Bars present in a month are not proof
of strategy-input readiness; full selected-day liquidity/MACD/V7/identity/Keeper
preflight remains mandatory. Missing expected trading dates fail selection;
weekends/holidays are explicitly listed as excluded calendar dates.

Sessions use America/New_York: premarket 04:00–exchange open, regular exchange
open–close, afterhours exchange close–20:00. XNYS scheduling honors early closes.
Each date/window independently resets cash and the one-batch ticker lock. Only
accepted squeeze episodes starting within that window can admit a ticker.
Daily episode history, prior indicators and swing geometry remain causal;
the signal producer's 300-second greedy episode expiry is preserved. This is
the declared extension for separate regular/afterhours sessions.

`preflight` defaults to **September 18 premarket** (latest discovered date),
prepares one complete tape, checks GPU resources and measures replay sizing.
It does not run or rank the 4,320-grid experiment. `--date` selects another test
day. `plan` selects dates/windows and writes the exact request without tape
preparation or calibration. `dates` prints catalogue membership.

The source identity certificate also requires the dedicated reader to have
`SELECT ON q_live.feature_tradable_universe_snapshot_v2`. The ARTE planned scope
does not contain the listing identity rows or replace the pinned snapshot hash.
An operator must authorize this one-table grant; the launcher never uses writer
credentials or changes privileges itself.

Preflight displays source-integrity ticker counts, price/squeeze admission,
feature rows, liquidity preparation and GPU calibration batch sizes separately
from durable grid results. Stage elapsed time and time since the last update
remain visible during long database queries. A failed job retains its database
reason and a redacted traceback in `error.json`, alongside the request receipt;
the terminal shows the reason instead of a generic exception class.

All runs require the build's pinned preopen snapshot `is_tradable=1` population.
Reference Gateway's tradability rules include positive IBKR conid, supported
listing scope and no open mapping issue. This is historical eligibility evidence,
not a query of today's universe or a guarantee that IBKR accepts every order.
The operator-declared LGHL broker identity/tradability exclusion is enabled by
default. `--exclude-tickers LGHL OTHER` adds explicit research exclusions; the
same arguments must be repeated on resume. Snapshot counts and content hashes
are verified before exclusions; both LGHL identity rows are retained in the
population eligibility artifact, with counts and reasons. Eligibility and
exclusions are sealed into the job/campaign request, source-cache identity and
tape provenance. The 4,320 parameter combinations are unchanged.

The workstation path requires the 96 GB CUDA GPU (at least 80 GiB physical
memory). Default `--batch auto` measures compiled CUDA graph batches
32/64/128/256/512/1024 on a 128-second timing witness, with synthetic admission
over selected prefix geometry. Calibration P&L is discarded. Choose the highest
measured candidate-seconds/second within declared resource headroom, rather than
allocating VRAM merely to fill it. Preserve at least 10 GiB or 15% of total GPU
memory; account for state, bounded fill buffers and capture intermediates.
An explicit `--batch N` measures that choice. No OOM/CPU fallback is hidden.
Money and commission arithmetic use FP64. Cash sizing reserves pending buy costs and conservative
future exit commissions for held and pending shares. Each new position reserves
its buy minimum plus the four independent protective-order minima, and one
per-share charge for buying and exiting. Paid minima, cancelled parent shares,
and completed exits release their cover; future sale proceeds are never assumed
to fund fees. This prevents low-value partial exits from making cash negative.
Commission multiplication explicitly uses FP64, including cumulative shares.
The fee-accounting repair introduced `squeeze-grid-v2-2`. The current grid is
`squeeze-grid-v2-3`; earlier financial results cannot be resumed or merged
under its revised acquisition and closeout identity. The session tape is shared across
all configuration lanes; a selected batch/graph is reused in place. Subsequent
days still enforce current tape and state headroom. Default tape limit is 48 GiB;
The workstation ledger default is 65,536 fills per account (overflow fails
closed); `--maximum-tape-gib` and `--maximum-fills` are explicit resource overrides.

The compact progress panel separates preparation, calibration, compilation,
replay and artifact saving. Saved configuration counts advance only after
ledger hashes and campaign receipts persist. Current replay seconds are a
provisional cursor, checked at bounded graph barriers (no per-tick host reads).
It shows active date/window, queued/failed/reused counts, invalid terminal
exposure, batch/tickers, compile/replay durations, replay-only rate/ETA and
allocated/total GPU memory. Rate excludes verified reused batches. `--plain`,
redirected output and `NO_COLOR` are supported. Ctrl+C retains saved batches;
an active batch restarts from its original account. Failed stages stay visible
and are recorded in job receipts and `progress.jsonl`.

Job outputs contain the date catalogue, exact grid, selected source/windows,
GPU calibration, progress events, equivalent low-level commands and resumable
per-build/window campaigns. No group/day is skipped on resume without checking
source fingerprints and saved artifact hashes. Per-window result CSVs remain
separate; there is no implicit pooled cross-session performance claim.
When resuming a non-default job, repeat its original date/session/resource flags.

`sync_workstation.py` packages only a committed **pushed** snapshot of v2 and
shared source readers into a new isolated workstation directory, verifies
every copied file hash and records deployment evidence under the workstation
runtime root. It copies no secrets or v1 Torch package. The read credential file
is discovered locally at `D:/TradingML/secrets/backtest_v3_read.env` only when
explicit reader environment configuration is absent. Workstation help/import,
date inventory and synthetic GPU checks are validation; historical experiments
remain for the user to start.


## Revision 3 persistent target-priority batches

The grid remains 4,320 configurations. All M fixed-quantity parents are submitted
once with cash and conservative fee cover reserved for the complete batch.
Entry capacity completes nearer target ordinals before farther ones; integer
cumulative demand keeps the GPU clock vectorized. Partial quantities remain
working at the original price cap until the session entry cutoff. Completed
price evidence at or below the frozen original setup stop cancels the remaining
acquisitions prospectively. Existing protective or rotation exits still cancel
their own pending parent. No rejection, cancellation or exit unlocks the ticker.

Defaults are an entry cutoff five minutes before session end and liquidation
one minute before session end. They are explicit Settings fields, with entry
cutoff required to precede liquidation. They improve the opportunity to close,
but do not guarantee sufficient executable liquidity. Remaining holdings fail
terminal qualification. Brackets activate on partial acquisition; a newly
activated bracket cannot trigger retrospectively in its parent's fill interval.

Revision 3 changes the approval and resume digest. Previous revision 2 results
remain preserved and cannot be mixed into a revision 3 campaign. No new full-grid
historical experiment has been run as part of this implementation.
