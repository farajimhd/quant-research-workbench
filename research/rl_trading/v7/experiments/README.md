# Isolated V7 experiments

All experiments reject dates outside the shared dataset's150 training sessions.
The38 canonical validation sessions are excluded from preparation, fitting,
hyperparameter selection and training replays. Experiments bind their additional
source code in manifests without changing the running V7 control implementation.

- `normalized.py`: economically scaled input columns; original PPO and execution.
- `episode_credit.py`: gamma=lambda=1 and one complete account episode per PPO
  rollout. Rewards still come solely from the agent's simulated account.
- `cost_aware.py`: adds one14-channel current-cost token to the98 market channels.
  It exposes absolute mark, feasible allocation, estimated fees, spread/slippage,
  remaining displayed capacity and quote age. It changes information, not fills.
- `predictive_data.py`, `predictive_model.py`, `pretrain.py`: **supervised**
  training-only prediction of60/300/900-second forward log mark returns. These
  labels are separate arrays and never enter the observation functions. They are
  not account rewards, trade instructions, or executable-profit estimates.
- `predictive_ppo.py`: frozen replay or own-reward PPO initialized from the
  supervised encoder. Encoder/forecast weights are frozen by default; only the
  buy/control/value heads train. `--train-encoder` is an explicit separate contract.

Predictive snapshots are sampled every30 seconds with a deterministic day-specific
offset. The first120 training sessions fit the predictor; the last30 training
sessions select its checkpoint by Huber loss. These are internal partitions of
the approved training set, not the canonical38 validation sessions. Shards store
inputs and targets separately with hashes and masks; missing targets are counted,
never silently dropped. Forward-filled marks keep the shared source semantics.
Sparse inputs were checked against full simulator observations at sampled clocks.

The predictor uses timeframe/cost attention followed by candidate/account
attention. Prediction heads are supervised; buy/control/value heads and holding
behavior are not pretrained. The initial trading rule averages three predicted
price ratios, applies current spread and single-fill fee estimates, and adds10
times that estimated net percentage to the buy logit. Forecast scoring clips log
returns to+/-50 percent. The wait prior remains+6. These explicit design choices
require frozen execution evaluation; lower forecast error does not establish
trading profit. Actual partial exits can cost more than the single-fill estimate.

Example laptop workflow from the repository root:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B -u research/rl_trading/v7/experiments/predictive_data.py --source D:/TradingML/runtimes/market_shards/datasets/2026-top5-v1-training-local --output D:/TradingML/runtimes/rl-trading/v7/predictive-data-v1 --verify-first
python -B -u research/rl_trading/v7/experiments/pretrain.py --output D:/TradingML/runtimes/rl-trading/v7/forecast-seed1917 --device cuda --epochs 8
python -B -u research/rl_trading/v7/experiments/predictive_ppo.py --forecast D:/TradingML/runtimes/rl-trading/v7/forecast-seed1917/checkpoint_best.pt --frozen-forecast --output D:/TradingML/runtimes/rl-trading/v7/forecast-initial-replay --dates 2026-01-05 2026-04-01 2026-08-10
python -B -u research/rl_trading/v7/experiments/predictive_ppo.py --forecast D:/TradingML/runtimes/rl-trading/v7/forecast-seed1917/checkpoint_best.pt --output D:/TradingML/runtimes/rl-trading/v7/forecast-ppo
```

Prediction training saves immutable epoch snapshots before committing latest
state; resume repairs best-checkpoint and metric publication from that authority.
Run manifests record initialization lineage and distinguish supervised
initialization from PPO's own-account reward targets. No pretrained V2/August
checkpoint is imported into this split. No architecture or pilot here is claimed
to be state of the art or profitable across sessions without measured evidence.
