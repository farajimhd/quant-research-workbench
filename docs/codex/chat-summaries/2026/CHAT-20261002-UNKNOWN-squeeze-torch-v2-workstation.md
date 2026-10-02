# Independent squeeze grid and workstation Torch backtest v2

- Chat started: Exact start time unavailable; implementation continued October 2, 2026.
- Last activity: October 2, 2026.
- Summary written: October 2, 2026; exact timestamp omitted.
- Chat/task identifier: 01a0fd2b-8a72-7813-8847-433343435543.
- Repository or scope: quant-research-workbench; research/vectorized_backtest/v2/torch_backtest.
- Related task-history entries: TASK-0221; source dependencies TASK-0215 and TASK-0206.
- Source completeness: Partial. Earlier implementation was recovered from compacted context; final source, tests and workstation evidence were verified directly.

## Narrative

The user proposed a squeeze-watchlist strategy with alternative entry rules,
independent profit targets, ratcheting protection and portfolio replacement.
Clarification established one submission batch of M independent position orders
per ticker/session, rather than a single parent acquisition split into parts.
M is 5, 10 or 15. Each position has exactly one target and one stop; no later
entry batch is allowed after submission, even after cancellation or an exit.

The agreed grid contains 4,320 configurations: 30 entries, three position
counts, three allocations, two target modes, two initial-stop modes, two trail
modes and replacement off/on. Entries are signal-only, VWAP cross held for 2s
or 5s, breakout/retest and 26 distinct MACD subset/ANY/ALL choices across
1s/5s/10s/30s. Singleton ANY/ALL duplicates are excluded. Allocation is equal,
inverse logarithmic or logarithmic by target ordinal. Percentage targets are
ordinal multiples of 2.5%; structural targets freeze current causal resistance
identities. Initial stops use 3% or a confirmed 2-left/2-right swing low. Trails
use a 10-interval mean absolute movement distance or 3% rise/1% stop steps,
always upward. Detailed fixed execution and score assumptions live in the
version README and grid.py, not in duplicated history definitions.

The user required a new v2 folder with no imports from the original Torch
package, preserving its vectorized, GPU-resident spirit. Needed compiler,
encoding and read-only source foundations were copied locally. The new runner
retains deterministic time sequencing, independent candidate accounts, shared
cash and per-ticker liquidity within an account, partial fills, per-order fees,
next-interval order eligibility and bounded ledgers. Missing certified inputs,
ledger overflow or broken accounting fail closed. Residual terminal exposure
invalidates fitness rather than inventing a liquidation. Completed batch
artifacts and source fingerprints are verified on restart. Historical runs are
approval-gated and do not reconstruct producer-owned indicators or geometry.

After approving the grid, the user chose to launch experiments personally on
the workstation GPU. The follow-up added an operator launcher, measured GPU
batch sizing and a compact progress panel using the design-terminal-ui skill.
The launcher selects a single date, inclusive range or all available dates and
premarket, regular or after-hours windows. Each date/window resets capital and
the ticker lock. Squeeze admission must occur within the selected window while
retaining the causal daily episode context, including the released 300-second
greedy expiry, prior indicators and swing history. This extension was explained
before implementation. XNYS scheduling supplies exchange open/close and honors
early closes; no raw SIP files are read.

Read-only workstation inspection verified an RTX PRO 6000 Blackwell Server
Edition with 95.59 GiB physical memory. Published market-day scope and matching
completed V5 producer manifests/SQLite certificates reconcile 36 trading dates,
July 30 through September 18, 2026. They span two source builds, one for
July 30–August 17 and one for August 18–September 18. Monthly physical ARTE
part inventories also show bars, indicators and liquidity in July–September.
This is catalogue evidence, not full selected-population MACD/V7/identity/Keeper
qualification. Full tape preflight remains mandatory before financial replay.

Default execution is all discovered trading dates, premarket 04:00–exchange
open in America/New_York. The separate preflight defaults to the latest date,
September 18 premarket at this inspection, and prepares one full tape plus
GPU calibration without running/ranking the complete grid. Expected missing
trading dates reject selection; closed calendar dates are exposed. Regular
and after-hours runs remain separate counterfactual accounts and result groups.

Automatic GPU sizing measures 32/64/128/256/512/1024 configuration lanes on a
128-second timing witness using selected prefix geometry with synthetic
admission. Its P&L is discarded. It chooses candidate-seconds/second subject
to current resource guards, reserving at least 10 GiB or 15% of total GPU
memory. It preserves FP64 accounting, shares the immutable tape, reuses fixed
compiled CUDA graphs and candidate buffers, and has no hidden CPU/OOM fallback.
Workstation defaults allow a 48 GiB tape and 65,536 fills per account; overflow
still rejects execution. These are resource settings, not grid dimensions.

The workstation ml4t environment had PyTorch 2.12/CUDA 13.2 but no Triton.
Compilation failed visibly. The fix installs the pinned Windows Triton 3.7.1
wheel into a v2-only runtime dependency directory rather than altering the
shared environment. A second probe established that an isolated --target
installation requires an explicit CC path to its bundled TinyCC; the runtime
now sets it unless the operator already supplies CC. Compiler scratch/cache
directories also stay under runtimes. Setup is explicit and reproducible;
ordinary launch commands never install dependencies. No eager fallback was
used to claim the compiled path passed.

The progress panel separates catalogue/preparation, calibration, compilation,
replay and saving. Saved counts advance only after durable receipt/hash writes.
Replay cursors use bounded graph synchronization, without per-tick host tensor
reads. It shows date/window, completed/queued/reused/failed counts, validity,
batch/tickers, replay-only rate/ETA, timings and GPU memory. Reused work is
excluded from throughput. It supports compact dimensions, NO_COLOR, plain or
redirected output, final failure/interruption retention and Ctrl+C restart.

Source snapshots are committed/pushed before deployment, then copied into new
isolated workstation code directories with every payload file hash verified.
Shared source certificate readers are included; secrets and v1 Torch are not.
Deployment evidence stays under workstation runtimes. A concurrent unrelated
task committed the first staged launcher files in commit 34700e776; subsequent
task-only fixes were committed separately. No unrelated working changes were
edited or reverted to repair this shared-index race.

## Evidence and operational handoff

- Baseline v2 commit: bcdb639f5. Workstation launcher/corrections were pushed in
  34700e776, c5c9b8f9a and 5a75e359b, with further compiler-path/history fixes
  reflected in the final source/deployment receipt.
- Local synthetic qualification reached 58 passing tests, including two
  targeted compiler-overlay tests. It includes a 1,024-lane compiled GPU
  comparison against independent CPU accounts and full normalized accounting.
- Normal and compact terminal layouts, failure/interruption states, live cursor
  restoration, redirected output, calendars, source grouping and launcher
  restart/export boundaries were exercised. The actual isolated workstation
  help/date command passed and reconciled all 36 dates.
- On the workstation, a synthetic 120-second/16-ticker/eight-configuration
  replay had a compiled-graph median of 0.021807 seconds and full CPU ledger/
  accounting parity. Initial setup was 37.17 seconds. This is not full-market
  throughput or profitability acceptance.
- A bounded workstation sizing witness tested 64/256/1024 lanes on 128 seconds
  and 16 synthetic tickers, with a small 256-fill ledger. Medians were
  0.025240/0.029411/0.061318 seconds; 1,024 had the highest measured throughput.
  Actual full-population calibration must use its real shapes and ledger limit.
- Reports are under D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v2/
  validation; deployment, jobs, progress, source caches and campaign outputs
  remain under this runtime family. No historical experiment was started.

The user owns the next execution: run the deployed workstation preflight,
resolve any full squeeze-population certification gap through its producer,
then start the requested date/session grid. Missing V7 coverage cannot be
replaced by saved Strategy 1 survivor tapes or reduced candidate populations.
Profitability, complete historical-input availability, full-market throughput,
numbered strategy publication and live broker integration remain unverified.
