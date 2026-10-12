# Causal feature enrichment and four-candidate experiment

Keep the exact 128 TRAIN and 2,898 public-development target identities, original
120-row inputs, and all action/quality/allocation/future labels from the audited
32-model experiment. Do not use the sealed sessions or run full training.
Select T14, T18, T24 and T09 before viewing new validation results: these are the
top four under the prior ENTRY-prioritized multi-objective percentile weighting.

New branches:

- 5s/15s/60s: 120 completed session-calendar slots each. Intraday empty slots
  have false masks, not forward-filled prices. Prior session tail provides the
  opening context; closed overnight hours do not consume slots. Actual time gaps
  and session boundaries remain inputs. All bucket ends are strictly before the
  target, and prior scalar values use the existing split-adjustment authority.
  Sixteen channels: open/high/low distances, close change, body/range/location,
  bar/session VWAP distances, volume/trades, active-second fraction, actual gap,
  boundary, and price/extrema validity. These are new candle aggregates, not
  claims that the certified 1s MACD is a 5s/15s/60s MACD.
- Thirty liquidity/quote channels at the 1s decision: trailing 1/5/15/60s volume,
  dollar volume, trades and active-second fractions; trade age, fee/price,
  trailing realized movement and its ratio to fees, short/long activity ratios,
  availability, and latest eligible completed 100ms quote spread, age and sizes.
  Quote evidence uses pinned broker attempts, full sparse producer integrity,
  explicit live_market_ssd placement, and a one-second freshness cutoff.
- Thirty shared market-context channels: median return, advancing fraction,
  dispersion and contributing coverage/count over15/60/300s; aggregate activity;
  SPY/QQQ returns with availability masks. Use every listing in the certified
  session population. Reject duplicate-symbol populations rather than double
  count or silently choose a venue. Unknown benchmark prices remain unavailable.

The original causal held-state supervision remains a hypothetical reference
position, not broker execution. Pending orders and historical policy fills are
not fabricated. Cross-decision prediction feedback is a separate future ablation;
the existing current-plus-four forecast recurrence and all heads stay unchanged.

Preparation uses vectorized grouped reductions and prefix sums once per listing,
not per epoch/window. Shared context is computed once per unique target clock.
Source hashes are authenticated before projections; no fixed bars or labels are
rewritten. The prepared tensors and TRAIN-only normalizers are hash-bound.
Report preparation wall time, memory headroom and explicit source coverage.
The additional encoders increase parameter count; comparisons with old models
test features plus their representation branches, not a pure feature-only effect.

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B -m research.rl_trading.v6.prepare_enriched_benchmark --output D:/TradingML/runtimes/v6-enriched-feature-panel-v2-20261012
python -B -m research.rl_trading.v6.run_architecture_benchmark --transformer-search --candidate-indices 14 18 24 9 --snapshot-runtime D:/TradingML/runtimes/v6-transformer-search-32-project-20261011 --enriched-runtime D:/TradingML/runtimes/v6-enriched-feature-panel-v2-20261012 --wandb-project rl-v6-transformer-search --output D:/TradingML/runtimes/v6-enriched-comparison-4-20261012
```

Do a bounded GPU smoke before the640-update experiment. Keep sequential laptop
GPU50% duty, microbatch8, CPU2 BelowNormal,4GiBVRAM/8GiBhost reserve. Every original
underfit/replay gate remains required before loading validation tensors for
evaluation. Preparation joins their feature sources using frozen target clocks
and identities; validation never enters normalization or architecture selection.
Use new W&B groups, preserving old model logs. No production promotion.
