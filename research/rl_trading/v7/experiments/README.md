# V7 isolated experiments

These research variants preserve the V7 causal inputs, execution rules and
training-only date authority. Their results do not establish profitability.

`predictive_data.py` and `pretrain.py` fit future mark-return targets on the first
120 approved training dates and select an epoch on the remaining 30 training
dates. These supervised targets are never observations or PPO rewards. The
canonical 38 validation dates are excluded throughout development.

`batched_ppo.py` initializes from that forecast checkpoint, freezes its encoder
and forecast head, and optimizes buy, control and value heads against each
independent account's actual simulated net-equity rewards. Four accounts share
inference batches; cash, positions and quote capacity remain separate. Updates
follow complete episodes. Training returns are changing-policy evidence.

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B -u research/rl_trading/v7/experiments/batched_ppo.py --forecast D:/TradingML/runtimes/rl-trading/v7/forecast-seed1917/checkpoint_best.pt --output D:/TradingML/runtimes/rl-trading/v7/batched-full150-seed1917 --environments 4 --prepare-workers 2 --memory-gib 8 --device cuda
```

Omitting `--dates` uses all 150 approved training dates. Memory estimates for
every batch are checked before loading, and current available RAM is checked
again before preparing each batch. `status.json`, `batches/`, `results.json`
and checkpoints record progress, fills, metrics and source/data identity.

Creating `STOP` in the output directory finishes and checkpoints the current
batch. Rename that marker and repeat the identical command with `--resume` to
continue. An interrupted, uncommitted batch is replayed from its saved boundary.

For frozen account replay, use a new output directory and add
`--evaluate PATH_TO_BATCHED_CHECKPOINT`. `--greedy` is an explicit evaluation-only
decision rule; the default samples actions. Neither mode opens validation data.
Checkpoint/source compatibility is enforced, so changing bound source requires
a separately identified experiment.
