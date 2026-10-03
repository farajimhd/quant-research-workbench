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

The agreed grid contains 4,320 configurations: 30 entries, three M values (5/10/15), three allocations, two target modes, two initial-stop modes, two trail modes and replacement off/on. Entry choices include immediate signal, VWAP holds of 2s/5s, retest and MACD 1s/5s/10s/30s subsets. Duplicate singleton ANY/ALL choices are removed. Exact formulas and execution/score assumptions are pinned in the version README and grid.py.

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

## Follow-up: visible preflight and tradable-only eligibility

At the user's request, SSH dispatched preflight into the active workstation desktop using a temporary interactive scheduled task. The task is temporary; its PowerShell window stays open for review. Final cleanup removed the task-owned registrations through SSH because limited interactive execution did not consistently unregister them. The first preflight failed because backtest_v3_reader lacked SELECT on q_live.feature_tradable_universe_snapshot_v2. The user explicitly approved that single read grant; it was applied and reader access verified without widening other grants. ARTE scope metadata cannot replace snapshot identity rows and their content hash.

The next check found 6,193 selected snapshot rows for 6,192 planned tickers: LGHL had two distinct listing identities with the same security identity. No snapshot row was altered. The user clarified that grids must contain only tradable listings and LGHL should be excluded because of its broker tradability/identity issue. The implementation keeps historical preopen is_tradable=1 authority, verifies the full snapshot before explicit exclusions, and defaults excluded_tickers to LGHL. Both LGHL identities, counts and operator-declared reasons are retained in population-eligibility.json and sealed into job/campaign requests, caches and tape provenance. Reference Gateway rules include valid IBKR conid and no open mapping issue; the historical flag is not a guarantee of current broker order acceptance. No current-universe substitution is used.

The enhanced terminal separates preparation counters from saved grid results, names source/admission/features/liquidity/calibration stages, reports stage/update timing and retains actionable error reasons. Redacted tracebacks persist in error.json. Compact, legacy Windows, redirected, interruption and bounded long-error layouts were checked; 65 local tests pass.

Real September 18 preflight passed identity, source-content certification, causal squeeze admission and feature preparation, then failed on structural intervals. All 727 selected squeeze tickers lack arte.strategy_one_v7_coverage_v1 rows for build 1521ba7702a9ee0783916f706f4885a24a3f32a91630b04ff738a90e65bc9dd5; duplicate count is zero. That requirement was superseded by the user-authorized causal streaming preparation described below. No ticker is silently skipped, missing geometry is not synthesized by backtest, and no historical grid/ranking was launched. Read-only evidence is under workstation runtimes/vectorized_backtest/torch_backtest_v2/launches/preflight-tradable-8fa9423e86824f2187cb0a2967a24cd3/coverage.json; job 91ea41a1fc904861a628121bf0a60987 retains the failed request. Source commits 7cf4a6b13, 175b6e645 and b38e39010 were pushed and hash-verified on the workstation. The initial interval-product dependency was subsequently removed; general prior-session checkpoints are the correct starting authority.


## Follow-up: prior checkpoints and exact causal intraday streaming

The user clarified that structural checkpoints must be loaded at session start and advanced by the streaming algorithm. A current-session end-of-day checkpoint would leak future structure. Read-only September 3 evidence showed all 833 selected tradable squeeze tickers have general prior-session structural coverage available before 04:00; 435 lacked the separately produced Strategy 1 intervals. The interval producer restricts its population to Strategy 1 candidates. Thus the earlier broad diagnosis of missing structures was incorrect, and requiring that product for this independent squeeze population was an inappropriate dependency.

V2 now validates and loads the general prior-session book at 04:00 New York, applies only split evidence available then, and advances the exact canonical FixedV7Stream/StreamingLevelBook on completed current-session 1s OHLC/volume. Same-day seeds, late availability, malformed bars and source mismatches fail closed. No shared production engine or ARTE product is changed. The sequential reaction/Student-t fitter remains exact; a bounded CPU ticker pool runs it once per ticker/session, outside the GPU grid. Numeric level comparisons remain vectorized. Sorted nearest-15 raw resistance prices and fresh structural validity are shared as [T,N,15] and [T,N] tensors across all configurations. Full 04:00 history is retained for later session windows. Missing seconds do not forward-fill structural clocks.

Per-ticker cache signatures include checkpoint/book hashes, causal splits, source certificate, completed OHLC/quote inputs, numerical versions and engine code. Atomic completion/hash readback and parent/worker kernel locks permit safe restart without racing orphan workers. Automatic CPU concurrency reserves resources and caps at 32; --structural-workers permits a bounded override. Progress separates ticker preparations, pending work and cache hits from grid results. Replay consumes the dense target row directly, avoiding repeated interval scanning/top-k. The grid itself is unchanged, and the user retains full experiment execution.

Tests exercise exact canonical projection parity with nonempty prior resistance geometry, future-suffix invariance, late/same-day seed rejection, missing-second validity, multiprocessing cold/warm cache identity, corruption/locking, and full CPU/compiled-GPU ledger/accounting equality at 1,024 lanes for both tape layouts. A follow-up repaired an actual liquidity-reader contract error by adding FORMAT ArrowStream, and keeps preparation timings/cache hits outside the stable tape fingerprint so resume accepts unchanged causal inputs. Preflight/campaign paths save tape authority and structural preparation receipts under runtimes. Source commits 92d9c4608 and bdef73c99 were pushed and hash-verified in isolated workstation deployments.

A visible September 3 workstation preflight is validating all 833 tickers, exact streaming preparation and measured GPU batch sizing. No full 4,320-strategy experiment or profitability acceptance has been claimed. Cold structural preparation completed for all 833 tickers in 164.43 seconds, consuming 119,708 completed valid input bars (117,639 after the canonical early-trade policy). All seed sessions are September 2 and availability precedes September 3 04:00. The resulting tape reached GPU compilation, which exposed a PyTorch 2.12 masked-modular-index code-generation error in shifted cumulative-fill concatenation. Replacing the shifted slice with the mathematically identical previous cumulative demand (cumulative minus wanted) preserves FP64 multiply/divide/floor ordering and exact fills, while keeping default compiler optimizations. A workstation 833-ticker, 32-candidate synthetic compiled-graph witness matched the full CPU ledger and accounting and replayed 35 seconds in 0.098568 seconds after 44.85 seconds setup. The latest local suite passes 74 tests. Source cdc9f9574 is pushed and its 853-file workstation deployment verified; the final automatic preflight is reusing the causal caches. That intermediate attempt subsequently found the valuation adapter issue described below.


The next historical prefix exposed a valuation adapter error: a completed NVO close of 47.42 was erased by a later published bar with price_valid=0, extremes_valid=0 and zero price fields. That flag denotes no eligible last-price update; an existing position still has a causal prior mark. V2 now aligns only completed price-valid closes for valuation, while observed/extrema/structural lanes stay strict and unobserved gaps cannot authorize entries or protection triggers. Missing initial marks remain unavailable. The preparation implementation is included in the stable tape seal. The corrected historical 128-second prefix passes eager replay; all 75 local tests pass. Source 42e956b3c is pushed and its 853-file deployment verified. The final visible September 3 preflight reuses all causal caches and measures automatic batches 32 through 1,024; it completed successfully.


Final preflight acceptance: September 3 job 8941e531cb9e46058fc369e4b2262498 completed with exit 0; all 833 structural caches were revalidated. Tape 4.10 GiB; measured prefix calibration selected batch 64. Source 42e956b3c was deployed.

Final cleanup verified zero preflight/child processes for the successful run and removed the four task-owned scheduled-task registrations created during this correction. The completed review terminal is intentionally retained.


## Full-session cash failure and correction

The user subsequently authorized all 4,320 configurations over one session. A visible September 3 premarket run at batch 64 saved 768 configurations in 12 batches before rejecting the next batch on financial invariants. The 36 saved summary/order/fill artifacts passed checksum verification. Of the saved configurations, 438 fully exited and all lost money; 330 had invalid terminal exposure. Best saved objective corresponded to net P&L -$372.67, drawdown $435.78 and fees $164.76 on $10,000. This is an ordered incomplete prefix, not a grid winner. Job db8bfe6e52f54576a7a746962d7062ba retains partial CSV/report and diagnostic witnesses under workstation runtimes.

The initial grid failure came from fifteen one-share MIMI stops charging $1 minima and exhausting cash despite no new buys. Revised sizing reserves future exit minima and uses FP64 commissions; accounting checks remain strict.

Revision squeeze-grid-v2-2 reserves conservative future exit fees for held and pending shares: one per-share exit charge plus unpaid minima for the four independent exit histories. New entry sizing covers its buy minimum, all protective minima, and buy/exit per-share fees. Cover releases as commissions are paid, unfilled parents cancel, or positions close; future proceeds are not assumed. The fixed-shape tensor path stays vectorized, with no new host synchronization, candidate loop, fee waiver, fictitious fill or weakened cash check. Entries can be slightly smaller. Commission multiplication is explicitly FP64.

All 78 package tests passed. Source 273124420277b4a7f80b5900c78c94b1d4e0d17b was pushed and 853 deployed files verified. The formerly failing 64-configuration full-session batch passed financial checks in 69.62 replay seconds. Sixteen accounts ended flat; 48 remained terminal-exposure-invalid. Validation artifacts remain separately labeled beside the old job; owned processes stopped.

The user then requested continuation to verify faithful completion. Because corrected fee reserves change sizing, a fresh full September 3 premarket grid replaced any attempt to merge the old prefix. The visible run on source 273124420 completed all 4,320 configurations with exit 0 in 5,076.23 seconds (84.60 minutes), including 4,752.47 seconds replay and 23.64 seconds graph setup. Job 156e49cd57fd48e295b64acbc7aed207 retains the full CSV ranking, session metrics, 68 batch summaries/order/fill ledgers, and FINAL_REPORT.md/json under workstation runtimes. Independent checks verified all 204 artifact hashes, exact/unique configuration identities, requested/filled/cancelled/pending order quantities, fees, fill counts, entry shares, residual shares and net cash for flat accounts. All pending parents are zero.

There are 1,046 fully exited eligible configurations, all with actual entry fills and all losing; 3,274 retain terminal exposure and have null fitness. Best eligible objective -0.0588346662 corresponds to net P&L -$371.27, drawdown $434.15 and fees $163.87 on $10,000. Signal entry, M=15, decreasing allocation, swing initial stop and adaptive trail tie across percentage/structural targets and replacement on/off; these tied cases made zero rotations. Median eligible P&L is -$1,312.61. Residual holdings most often include BTCT, FLNC and USAR; ticker counts overlap across independent accounts. This one-session outcome is not profitability acceptance, and residual marked P&L is not eligible realized fitness. No accounting failure occurred. Cleanup confirmed no task Python processes and removed the temporary launch registration, retaining the visible review terminal. Do not combine the old prefix with these corrected results. Investigate terminal exposure and execution economics before broader date/session research; production and live acceptance remain separate.


## Strategy 43 app comparison requested

The user chose the first eligible September 3 winner as independent Strategy43: 1s decisions/100ms native fills, signal entry, fifteen inverse-log brackets, structural targets, swing stop, adaptive trail, no replacement. Native Portfolio/OMS execution, isolated stop amendments/liquidation, normalized V4 journal/command lineage, cold entry reconstruction, restart-safe producer contracts and SELECT-only certificates pass focused checks. Three SSD tables and exact narrow grants are provisioned. Actual certification confirms 6,066 tradables and 833 squeeze candidates with prior-session seeds. The missing-only campaign published 435 V7 units and certified all 833. All 833 dense histories then published; decimal transport drift was corrected with exact IEEE Float64 Arrow inserts and ordered 100ms notional sums, without hash tolerance or deleting failed unsealed rows. Full child certification passed in 1,669 seconds, token 27cea50921e8be48e5451014cac524dfd291a340172bee5f9fdfbf63cd4fbcd6.

Independent configuration and app controller are implemented; exact AST projections preserve historical strategies. The immutable release certified. The first run failed because callbacks appended a journal suffix during prefix persistence. Repair 8732e361e drains bounded suffixes with unchanged equality checks; an exact executor fingerprint pair preserves the release. All 89 Strategy43 tests pass; 2,136 deployed files were hash-verified.

Run f0d31e83-5c39-4377-abf8-9ee275d2caa8 completed 198,000 broker intervals and independent cold readback at sequence 2264. Native lost $446.13, paid $170.37 fees, acquired 17,037 shares and produced 77 fills, ending flat. Torch lost $371.27, paid $163.87, acquired 16,387 shares and produced 390 fills. Both trade GELS/MIMI. Native uses 100ms quote/price-level matching; Torch approximates 1s VWAP plus/minus spread. Drawdown definitions differ. Failed runs remain separate. Report: D:/TradingML/runtimes/strategy43/comparison/3649dda277b64b2aabeac122029ce28b/COMPARISON.md. Verified idle legacy laptop backend was replaced by managed startup. Wider dates, public resume and profitability remain unqualified.
