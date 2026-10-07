# V5 staged population search

Independent snapshot of V4's causal Torch engine. V2/V3/V4 are unchanged.
Original certified 30 training days and six sealed validation days remain the data split.
All V6-derived inputs, split-adjusted histories/indicators, 120 observed-candle context,
premarket operand pruning, timestamp durations, next-interval fills, cash/risk/commission
and 25% approximate liquidity contracts are retained.

## Search

`torch_backtest.staged_search` requires an explicit JSON schedule and same-source
qualification; no final population or session schedule is silently selected.
Each stage declares `end_generation`, `population`, `sessions`, `archive_top`, and
`archive_random`. Panels are balanced seeded random training-day samples, shared by
every candidate in a generation. Scores from different panels are never compared
to select a global winner. Each generation contributes top and random valid archive
members; exact duplicates are removed. Stage checkpoints evaluate the archive on
all 30 training days and seed the next stage. Final selection produces a training
finalist requiring an independent ledger/input audit before freeze; this controller
never opens validation. Final audited freeze/evaluation delivery remains a separate
gate, not automatic permission from a profiling result.

`staged_audit --output RUN --freeze` independently reconstructs the complete
training search, checks exact RNG/population evolution, receipt and actual-fill
hashes, objective arithmetic and fresh training-input bytes before freezing.
`staged_validation --output RUN` accepts that audited freeze only, then evaluates
the default and frozen finalist together once on the six sealed sessions. A
completed report cannot be evaluated again; interrupted work reuses hash-bound
batch receipts. Final input and independent fill audits accompany the report.

The V4 score and seven component weights are unchanged. Positive ex-best profitability
and 1–20 acquisitions every day are no longer eligibility gates. Invalid/nonfinite
metrics and nonflat terminal accounts remain invalid; accounting and causal checks
fail closed in the engine. Negative profitability and zero-trade days are rankable.
Typed programs still have bounded operand windows and node counts. Missing startup
history masks the operation until known, rather than fetching future candles.

Next-generation shares: 10% elite unchanged, 10% other valid candidates selected
randomly unchanged, 10% fresh random, 50% offspring from the top 10% of valid parents,
20% offspring from random valid parents. Offspring use either mutation or typed
whole-stage crossover followed by mutation. Largest-remainder rounding preserves
the exact population count. If too few valid unchanged survivors exist, expose that
condition and fill the deficit with fresh valid constructions; no invalid parent is
used. All-invalid panels stop. Checkpoints persist RNG, panel schedule, population,
archive and stage/generation counters; partial session/batch receipts bind input,
population and actual fill hashes for exact reuse.

## Parallel execution and profiling

`torch_backtest.batched` loads/prefetches sessions in order, holds one shared GPU
feature/tape bank and evaluates bounded candidate lanes in Torch. One GPU owner
reuses captured financial graphs across equal-sized batches/sessions. It reuses
the packed gate allocation in place after replay synchronization and copies
compatible host sessions directly into the captured tape allocation, preserving
pointers without retaining duplicate full gate/tape tensors. Candidates
are independent accounts; liquidity is shared among positions within each candidate,
not between strategies. Logical population may greatly exceed a physical batch.
No per-candidate feature histories or competing GPU worker processes are created.

Execution specialization keeps the original strategy population and RNG unchanged.
It stably sorts execution by configured lot count, allocates each physical batch's
actual maximum (1–15), and restores all metrics to original candidate order.
Batch receipts bind the permutation and candidate identities; the final auditor
requires complete, unique candidate coverage. A changed lot/mode/history signature
requires a new captured runner rather than changing a graph's assumptions.

Native programs skip unused legacy entry/retest/MACD computations and atomic-rule
history. Uniform batches skip disabled replacement, structural-target, swing-stop,
or alternative trailing branches. Rolling financial histories use the batch's
maximum required windows; zero-weight momentum/attention skip their calculations.
Mixed batches retain every needed branch. Certified feature windows remain 120
observed candles and financial durations remain UTC timestamp based.
Temporal rule operations gather only the candidate lanes requesting each
operator/window pair, rather than calculating every distinct window for all lanes.
The reference masked/full-batch implementation remains available for parity checks.

Actual fill logging uses a masked FP64 GPU scatter behind a Torch custom operator:
native Torch prefix ranks keep the original fill order, while a Triton kernel
writes only real rows instead of materializing inactive zero rows. CPU/native
Torch logging remains the reference. No account arithmetic or liquidity contract
changes. Deliberate compiled variants have a bounded 128-entry recompilation
limit; full-graph compilation still fails rather than falling back to eager work.

`profile_execution --sessions PLAN --output UNIQUE --baseline-session RECEIPT_DIR`
measures cold and warm specialization passes against immutable before-change
evidence. It requires identical population/input/duration bindings, exact actual
fill rows and financial metric agreement (rtol 1e-10, atol 1e-7), and audits cash,
quantity, fees and holding durations independently. Prefix checks explicitly
exclude final terminal eligibility. Reports separate replay, rule preparation,
compilation and end-to-end improvement; slot-count ratios are not speed evidence.

Run from the laptop repository or an immutable committed workstation copy:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B -m research.vectorized_backtest.v5.torch_backtest.profile_staged `
  --sessions D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v4/campaign_inputs/20261006-source-aligned/sessions.json `
  --output D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v5/profiles/UNIQUE-ID `
  --populations 4096 8192 --batch-sizes 128 256 512 --session-count 2 --profile-seconds 256
```

The prefix replay still compiles rules against the complete training session and
measures residency, graph setup, rule preparation and replay separately. Prefix
performance is not full-session timing, financial qualification or profitability
evidence. Repeat selected configurations with `--profile-seconds 19800` for real
session timing and prefetch overlap. Reports preserve peak allocated/reserved GPU
memory, candidate-timestamp throughput, cold and total timing, resource failures,
population/source/input identities and receipts. Final search budgets stay unset
until those measurements are reviewed.

`staged_observe --output ...` is the read-only fixed-screen dashboard. Keys 1/2/3
select rank; F/O/T/P select financial/objective/positions/performance; N pages metrics;
Q exits the renderer. Search and full-training ranking scopes are explicit. Live
batch metrics remain provisional. Logs stay in a bounded timestamped panel and
complete `events.jsonl`, rather than scrolling the terminal.
