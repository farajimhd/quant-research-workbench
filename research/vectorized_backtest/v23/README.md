# V23: V19 single-stage evolution with expanded rules and local ticker trimming

V23 duplicates V19, not V20's two-phase session-expert controller. One fresh random population is evaluated across every training session each generation. There is no local pretraining phase, expert archive stage, imported seed pool, or per-session optimizer. Local means the per-session scoring/selection route inside this same all-session generation.

## Selection and costs

V19 reproduction is retained byte-for-byte: default 50% global slots and 50% local session-specific slots, global elites, local champions, cross-session champion mating, intrasession mating, ancestry exclusion, original crossover-only mutation mask/rates, deduplication and fresh fallback. V21's new reproduction routes and mutation of fresh candidates are not included.

Global objective coefficients, session trimming, pooled complete-position-trimmed reward and global eligibility remain V19's. Complexity cost remains zero. Local selection now aggregates all fee-adjusted position profits by traded ticker, then trims the positive-only fractional top 10% of ticker contributions; every loss is retained. Trim strength remains V19's full removal, not V21's 0.25 concentration coefficient. Local drawdown, tail-loss, risk-duration, capital-time, peak-risk, other-session loss penalties, minimum three completed positions and positive-trimmed-profit eligibility remain. Other-session loss uses the corresponding ticker-trimmed local profits. Ticker totals must reconcile with complete-position/session P&L; missing or altered evidence fails closed. Local diagnostics and observers explicitly identify ticker trimming. Validation remains a frozen training-selected diagnostic shortlist, never reproduction feedback.

## Rule/feature changes ported from V22

- Maximum 128 nodes per rule; temporal context remains 120 observed candles.
- Random-rule requested bands remain 3..9 / 10..19 / 20..32; crossover may grow to 128. This changes the ceiling without forcing large initial rules.
- Balanced typed arithmetic/temporal/comparison sampling, including cross-above/below; same-unit division and uncentered x/LAG(x,h) ratios.
- 50/50 uniform/log-magnitude numeric priors and matching ratio thresholds.
- Average absolute movement normalized as movement/close, not movement/close-1, under v23-magnitude-ratio-v1.
- Maximum additions evolves over 0..20; $1,000 entries, $200 additions, 20% stop-distance and $300 planned whole-position risk checks remain.
- Sampling coverage and rejection diagnostics. No sixfold initialization.

Original certified feature producers and inputs are retained. New source and explicit rule/feature/local-trim contracts prevent silent checkpoint reuse from older versions. Full generations preserve population, RNG, IDs, pedigree and split seals.

## Running

The authoritative single-stage entry point is research.vectorized_backtest.v23.run_search. Default population 1,024, generations 50, batch 512 and two session workers match the V19 search design. Supply the approved chronological training/validation JSONs and a fresh directory under D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v23. For example, in PowerShell:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B -u -m research.vectorized_backtest.v23.run_search `
  --training D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v18/datasets/20261014-79261b811-24-12/enriched-ca3efc7c1/training.json `
  --validation D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v18/datasets/20261014-79261b811-24-12/enriched-ca3efc7c1/validation.json `
  --output D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v23/campaigns/fresh-run `
  --population 1024 --generations 50 --batch-size 512 --session-workers 2 `
  --seed 182237 --capture-gib 8
```

This is a compute command, not a readiness probe. Before a workstation launch, sync committed/pushed code immutably, verify certified producer byte identities (including line endings), input/source hashes, ownership and absence of another active optimizer. Legacy copied V19 handoff/qualification scripts are retained for historical compatibility; they are not V23's launch procedure and must not be used to replay old predecessor qualifications. No automatic campaign or monitor is installed by this change.

Tests cover unchanged V19 selection/global cost arithmetic, ticker-local aggregation/losses/alignment, 128-node boundaries and old-rule exact evaluation, feature semantics/literal units, one shared population across all 24 training sessions, validation independence, sealed resume and tamper rejection. CPU/available-device fixture checks do not establish full campaign speed or trading generalization.
