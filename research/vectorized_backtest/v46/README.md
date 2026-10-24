# V46: V45 selection with resident bid/ask execution

August 13, 2026 laptop experiment: 50 generations, population 1024, seed 2020,
the exact original V42 initial 6144 programs and post-initialization RNG.
V45 selection is unchanged: 102 positive global-net-profit elites, 205 baseline
fresh candidates, 358 global and 359 adaptive positive ticker/session crossover
slots. Preserve V45 mutation, species compatibility, ancestry, sibling/mask
operators and bounded fallback rules. This version changes financial execution.

## Execution contract

Use the same certified prepared market data. At each existing execution second,
entry/add buys use the timestamped ask; reductions/exits use the bid. Quotes must
be finite, positive, uncrossed (locked allowed), as-of that second, and at most
one second old, matching the existing neural quote-feature validity rule.
No per-second database work or quote joins. No stale/missing quote falls back
to close. Existing observed-price gating remains for normal fills. Terminal
liquidation also requires a valid quote; unresolved positions make that candidate
ineligible and are counted in unliquidated_positions. Their realized open-episode
cashflows are retained separately and reconciled with completed episodes; no
completed trade or exit price is fabricated. Static quote coverage is
published separately under quote-coverage/.

Neural market inputs and trigger clocks remain unchanged. Position cost basis,
entry targets, prospective risk, paid fees and action-fill feedback reflect ask
purchases; realized sales and turnover reflect bids. Equity marks and stop/target
triggers retain the original last-price convention. Fees and original risk checks
remain mandatory. There is no L2 depth, market-impact, latency or partial-fill
simulation; this is an idealized full-fill top-of-book model, not execution proof.

Quote assembly happens once during bounded CPU session loading. Float64 bid/ask
and boolean validity tensors share across candidates and follow the existing
pinned upload path. The fused Triton transition consumes them inside the existing
CUDA graph. V46_MASK_QUOTES selects equivalent all-lane or potential-order-lane
quote loads. The measured faster form is unconditional resident loads (V46_MASK_QUOTES=0); financial/episode outputs
must match exactly. No lower precision or omitted risk checks are permitted.
V46 evaluator, data adapter, worker and graph are version-owned; immutable V42
and existing V45 deployments are not patched.

## Fair comparison and integrity

Only original programs and RNG may be reused. Old close-priced financial receipts,
result caches, scores, offspring and archives are forbidden. All 6144 initial
programs receive new full-session bid/ask replay before generation-one selection.
The original cohort digest must match its V42 seal. V46 source/data/execution
identities bind its own result cache and checkpoint. Preserve the laptop 12 GiB
reserve plus 8 GiB admission, bounded caches/queues and GPU guards. No validation
inputs or feedback. All outputs belong below D:/TradingML/runtimes.

The case score remains net profit - 0.05 * realized case drawdown. Global elites
rank by global net profit. See V45's selection design for adaptive case rank rounds
and exact population budgets. The experiment remains explicitly single-session.

## Profile, launch, observe

Use the original immutable V42 dependency tree plus committed V46 files in a new
runtime snapshot. The scheduler helper belongs to V46, never the hashed V42 tree.

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B -m research.vectorized_backtest.v46.profile --job D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v42/distributed-399e034e1/full-job.json --pool D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v45/aug13-mixed-p1024-s2020-g50/initial-pool.json --output D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v46/qualification/profile --candidates 1024
python -B -m research.vectorized_backtest.v46.launch --job D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v42/distributed-399e034e1/full-job.json --output D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v46/aug13-bidask-p1024-s2020-g50 --initial-pool D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v45/aug13-mixed-p1024-s2020-g50/initial-pool.json --authorize-run
python -B -m research.vectorized_backtest.v46.progress --active D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v46/active-test.json
```

The profile fixes one stratified original 1024-candidate cohort and training session,
loads/uploads once, then measures original close, naive bid/ask and masked bid/ask.
Capture/compilation, steady GPU replay, host wall time and export are separate.
Naive/masked full financial and position-episode results must be identical. The
close and bid/ask paths have different trading behavior, so their timing ratio is
end-to-end workload evidence, not a pure instruction-overhead measurement.
--resume-variants reuses an already completed original close control with the same
job, cohort and dimensions; it never reuses a training financial result.

Task Scheduler owns the run outside the app process tree while logged in. No reboot
recovery or automatic optimizer retries. STOP under output/local/2026-08-13 requests
a checkpoint-boundary stop. Inspect exact owners and seals before checkpoint resume.

The progress table shows global profit, best case score, and E (financial/terminal
eligibility). Key 2 sorts global profit; key 5 sorts case score. The observer is
read-only and optional. It does not change training RNG or selection.

## Measured qualification

Matched 1024-candidate Aug13 profile, 19,799 replay seconds, 453 listings:
original close GPU replay 60.65284 s; resident bid/ask 60.81184 s (+0.26%);
conditional/masked quote loads 61.62493 s (+1.60%). Both bid/ask variants had
exactly identical common financial and completed-episode output hashes.
Conditional loads were rejected as slower. These are observed single-profile
wall/GPU timings, not a guarantee for every future population. Initial loading
and compilation are separate costs. The additional resident quote data is about
145 MiB. The profile had 762/1024 terminal-valid candidates versus 1024/1024 for
forced close-price liquidation; V46 does not manufacture fills for the remainder.
The final open-cashflow bookkeeping is host-side export; profiled replay/step,
Triton and graph functions are bound unchanged and the complete wrapper plus
reconciliation path is tested independently.
