# V63: top-five market inference and explicit price requests

Implementation under qualification. Do not start the full campaign until the
workstation full-path tests and timing evidence have been reviewed by the agent.

The source is self-contained under this directory. V62 results are not reusable
financial evidence. Fresh founders retain 1-2 nodes per action; reproduction
retains the 1024 total/per-action evolution budget.

- Existing ranked top-ten inputs supply the first five slots, shared by every
  market feature resolution. Only population-requested features are projected.
- All action inference and candidate price expressions are market-only. The
  compact graph uses five slots, with ticker identities resolved at every clock.
- Dynamic native execution traverses current top-five plus open-position/pending
  ticker obligations, loading only quote/mark data for execution. No indicators
  or policy programs are interpreted by execution.
- Rank loss cancels pending buys and latches liquidation until flat. Execution
  consumes quote sizes once per new snapshot, caps sells at owned inventory,
  and preserves the three-second post-exit entry delay.
- Explicit candidate stop/target/limit expressions use existing shard high/low
  and quote data. Missing references abstain; no implicit fallback or clamp.
- The financial objective keeps logarithmic $2000 ticker-date/$5000 session
  targets, coefficient .1, and drawdown weight 1. Each distinct blocked request
  deducts $5 once; repeated waiting and partial fills are not charged again.
- Existing market shards remain immutable. Reference features and calibration
  samples are calculated in RAM; only calibration recipes and result evidence
  are written beneath D:/TradingML/runtimes.

Bounded full-path profiling:

    python -B -m research.vectorized_backtest.v63.profile_end_to_end --source-job <stopped-v62-job.json> --output D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v63/qualification --count 1024 --days 2026-08-13

The operational target is 35 seconds per 1024-candidate session. Cold preparation,
compilation, transfer, inference, execution, publication and total wall time must
be reported separately; no target achievement is claimed by this document.
