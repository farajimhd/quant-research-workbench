# Premarket scanner PPO research

V7 starts fresh weights on the sealed shared `market_shards/v1` dataset. It does
not reinterpret V2 checkpoint features. The approved split is150 training
sessions (2026-01-05 through2026-08-10) and38 validation sessions (2026-08-11
through2026-10-02). No separate untouched holdout exists. August-trained V2
weights are incompatible with this split and are not imported.

`stage_data.py` copies only the training manifest, source/seal metadata,150
training scanner shards and151 required bar/context days to
`D:/TradingML/runtimes/market_shards/datasets/2026-top5-v1-training-local`.
Every copied file is hash verified; partial copies resume without overwriting
different existing bytes. No validation manifest or validation market shard is
copied. The shared source retains its `approved_retrospective` identity evidence;
this is not historical membership certification.

Training uses stored causal per-second top-five selections, so no live scanner
is required. The whole morning's ticker union is storage scope only. Passing
future union membership to the policy would leak admission information.

The98 observation channels are seven values plus explicit validity masks for
five trade timeframes and bid/ask1s windows. They summarize completed windows,
including returns, ranges, activity and coverage; they are not supervised
labels. Missing history is masked rather than filled from future data. Shared
timeframe attention pools these seven groups per candidate; two market-attention
layers compare five candidates with holding and account tokens. There are no
ticker-ID embeddings or copied pretrained weights. This is an experimental
architecture, not an established state-of-the-art profitability claim.
The wait action starts with a+6 logit prior to avoid uniform-action churn;
it remains trainable. Initial weights are saved separately for baseline replay.

The first risk contract permits one cash-funded long position,15% allocation,
2% stop,4% target and30-minute maximum holding time. Actions are wait, buy one
of five current scanner candidates, or close the holding. Holdings stay visible
after leaving the scanner. Learned decisions stop before09:30 New York; remaining
positions use fixed exits, with forced liquidation from19:45 and invalid terminal
status if still held at20:00. Orders execute at the next second's displayed ask
or bid, with2bps extra slippage and at most10% of displayed size. Consumed size
does not replenish until the quote timestamp changes. Withdrawn books are
unavailable. The shared no-age-limit displayed-book scenario is retained.

Commissions reuse the pinned V2 IBKR2026-09-26 scenario. This is a simulated
cost scenario, not reconstructed historical invoices or execution certification.
The only learning reward is the agent's own net equity change after costs.
PPO returns during learning are not frozen-policy evaluation results.

Run from the repository root with the configured Python environment:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B -u research/rl_trading/v7/stage_data.py
python -B -u research/rl_trading/v7/train.py --output D:/TradingML/runtimes/rl-trading/v7/pilot --dates 2026-01-05
python -B -u research/rl_trading/v7/train.py --output D:/TradingML/runtimes/rl-trading/v7/pilot-eval --dates 2026-01-05 --evaluate D:/TradingML/runtimes/rl-trading/v7/pilot/checkpoint_latest.pt
```

Omitting `--dates` uses all150 training days. `--passes` repeats the training
schedule; default1. `--resume` requires the same source/config/data contract.
Creating `STOP` in the run folder checkpoints at the current rollout boundary.
Checkpoints preserve optimizer, account, quote consumption and RNG state.
Status, update metrics, caches and models stay under the laptop runtime root.
Prepared dense caching is opt-in (`--cache PATH`): one measured day takes356MB,
so the default prepares one day in memory and avoids expanding150 days on disk.
The entry point is deliberately training-only, including its evaluation mode;
validation dates are rejected before opening market data. Validation must wait
until architecture and model selection are locked from training evidence.
