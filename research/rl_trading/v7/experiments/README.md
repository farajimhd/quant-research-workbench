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

## Batched account training


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

## Exact entry-outcome diagnostic

`entry_outcomes.py` samples every tenth stored 30-second snapshot (five minutes),
tries each currently eligible candidate from a flat $10,000 account, and waits
for the existing fixed exits. It reuses the simulator for every fill and fee;
the accelerated path skips holding clocks before the first exit trigger. It
does not preserve a PPO reward trajectory, peak equity or drawdown. Hindsight
profits are opportunity diagnostics, never policy results or observations.

Observed nonfills are known zero; current ineligibility and failed liquidation
remain distinct statuses. Day reports have hash seals and resume verifies them.
The default covers all 150 training dates, with day-boundary `STOP` support:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B -u research/rl_trading/v7/experiments/entry_outcomes.py --output D:/TradingML/runtimes/rl-trading/v7/entry-opportunity-full150
```

The current V7 simulation charges commissions separately for each partial fill.
This differs from V65 persistent-order commission aggregation. Results retain
this explicit scenario; changing order semantics requires a separate contract.

`entry_score.py` requires all 150 sealed outcome days before fitting two fixed
ridge predictors of standalone net dollars. One uses the 112 causal features
plus account inputs; the other uses the frozen attention encoder plus current
cost and account inputs. Both fit only on the first 120 training days, with
fit-only scaling and fixed regularization. The remaining 30 training days
provide diagnostics. Unliquidated targets block fitting rather than disappearing
from the sample. No representation or score threshold is selected automatically.

`entry_replay.py` loads either frozen predictor and runs one persistent account
per training day. Only at the matching five-minute clocks, a flat account buys
the highest currently permitted score strictly above zero. All other actions
are WAIT, leaving the original fixed exits in control. This replay measures
cash, overlapping opportunity constraints, partial fills and actual fees; a sum
of independent entry targets is not a substitute for it.

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B -u research/rl_trading/v7/experiments/entry_score.py --output D:/TradingML/runtimes/rl-trading/v7/entry-score-v1 --device cuda
python -B -u research/rl_trading/v7/experiments/entry_replay.py --bundle D:/TradingML/runtimes/rl-trading/v7/entry-score-v1/attention --forecast D:/TradingML/runtimes/rl-trading/v7/forecast-seed1917/checkpoint_best.pt --output D:/TradingML/runtimes/rl-trading/v7/entry-score-attention-replay --dates 2026-01-05 2026-04-01 2026-08-10
```

`entry_net.py` predicts the same net-dollar targets with a nonlinear residual
added to the causal current-quote roundtrip estimate. It fine-tunes the forecast
encoder at 1e-5 and an 86→64→1 residual head at 1e-3 for at most 20 epochs. The
head starts at zero, so epoch zero is exactly the known-cost baseline. Selection
uses internal-development MSE and includes epoch zero; a baseline win is not a
learned improvement. Sealed epoch checkpoints support exact boundary resume.
`entry_net_replay.py` uses the same entry schedule, positive-score rule and
simulator as `entry_replay.py`, with the neural scorer injected explicitly.

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B -u research/rl_trading/v7/experiments/entry_net.py --output D:/TradingML/runtimes/rl-trading/v7/entry-net-v1 --device cuda
python -B -u research/rl_trading/v7/experiments/entry_net_replay.py --bundle D:/TradingML/runtimes/rl-trading/v7/entry-net-v1 --forecast D:/TradingML/runtimes/rl-trading/v7/forecast-seed1917/checkpoint_best.pt --output D:/TradingML/runtimes/rl-trading/v7/entry-net-three-days --dates 2026-01-05 2026-04-01 2026-08-10
```
