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

## Ordered historical bars

`temporal_data.py` preserves native OHLC/activity sequences at the same five-minute
entry grid. Seven windows are right-aligned to 60 cells, with separate field
masks and no imputation. It verifies all existing summary values and current
scanner admissions before sealing each day. Daily positions are historical-day
ordinals; other windows retain the shared completed-clock semantics.

`temporal_net.py` starts from fresh weights. Width-32 attention encodes ordered
bars within each window, combines timeframes and current costs, then compares
current candidates with the account context. It predicts a residual over known
current-quote costs, using the same 120/30 internal split and epoch-zero baseline.
The scorer binds all temporal receipts and, during account replay, selects only
the current sampled clock after verifying market features and membership.
Replay opens causal arrays only. The shared CLI's `--forecast` argument is
accepted for compatibility but is not read or reused by this temporal model.

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B -u research/rl_trading/v7/experiments/temporal_data.py
python -B -u research/rl_trading/v7/experiments/temporal_net.py --output D:/TradingML/runtimes/rl-trading/v7/temporal-net-v1 --device cuda
python -B -u research/rl_trading/v7/experiments/temporal_replay.py --bundle D:/TradingML/runtimes/rl-trading/v7/temporal-net-v1 --forecast unused --output D:/TradingML/runtimes/rl-trading/v7/temporal-net-three-days --dates 2026-01-05 2026-04-01 2026-08-10
```

`temporal_policy.py` tests a different objective using the same sealed inputs:
maximize expected labeled entry payoff over five currently eligible candidates
and WAIT. This is supervised full-information counterfactual learning, not PPO
or realized account reward. It initializes the temporal body, resets the final
head to the all-WAIT greedy baseline, and uses a fixed softmax scale and entropy
term. The output is a policy score, not a calibrated dollar forecast.
Selection maximizes greedy internal-development payoff per sampled decision;
WAIT contributes zero and ties retain the earlier checkpoint. An all-WAIT winner
does not meet a positive-profit target. Account replay remains necessary because
independent labeled entries may overlap.

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B -u research/rl_trading/v7/experiments/temporal_policy.py --output D:/TradingML/runtimes/rl-trading/v7/temporal-policy-v1 --device cuda
python -B -u research/rl_trading/v7/experiments/temporal_policy_replay.py --bundle D:/TradingML/runtimes/rl-trading/v7/temporal-policy-v1 --forecast unused --output D:/TradingML/runtimes/rl-trading/v7/temporal-policy-three-days --dates 2026-01-05 2026-04-01 2026-08-10
```

`temporal_weighted_policy.py` tests payoff-weighted binary cross entropy instead
of softmax expected payoff. A profitable missed entry retains a gradient even
when its predicted entry probability is near zero. Labels are the sign of net
payoff, weighted by its absolute magnitude; eligible zero-payoff observations
remain in the denominator with zero loss weight. The current-cost offset stays
in the score. Scores represent independent binary logits, not dollar forecasts.
The same internal split, strict greedy-payoff selection and WAIT baseline apply.
Both fit and development greedy metrics are saved to distinguish failure to fit
from failure to generalize. This remains supervised counterfactual research.

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B -u research/rl_trading/v7/experiments/temporal_weighted_policy.py --output D:/TradingML/runtimes/rl-trading/v7/temporal-weighted-policy-v1 --device cuda
python -B -u research/rl_trading/v7/experiments/temporal_weighted_policy_replay.py --bundle D:/TradingML/runtimes/rl-trading/v7/temporal-weighted-policy-v1 --forecast unused --output D:/TradingML/runtimes/rl-trading/v7/temporal-weighted-policy-three-days --dates 2026-01-05 2026-04-01 2026-08-10
```

`entry_tree_policy.py` provides one fixed histogram-boosted tree comparison:
200 iterations, 15 leaves, minimum 100 samples per leaf, learning rate 0.05 and
L2 regularization 10. It uses the same payoff weights but the existing 112
summary/cost features plus eight account values; it does not use ordered bars.
Thus the comparison changes representation as well as learner. Only the final
candidate is compared with WAIT on the internal development set, with no sweep.
Completed runs have verified model/data/source seals and can be reopened using
`--resume`; interrupted fits require a new output directory. No forecast weights
are opened. Pickle loading is restricted to hash-verified local runtime bundles.

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B -u research/rl_trading/v7/experiments/entry_tree_policy.py --output D:/TradingML/runtimes/rl-trading/v7/entry-tree-policy-v1
python -B -u research/rl_trading/v7/experiments/entry_tree_policy_replay.py --bundle D:/TradingML/runtimes/rl-trading/v7/entry-tree-policy-v1 --forecast unused --output D:/TradingML/runtimes/rl-trading/v7/entry-tree-policy-dev --dates 2026-07-01 2026-08-10
```

`entry_temporal_tree.py` keeps that classifier fixed and adds six statistics per
native window: close slope at scheduled positions, close location within the
high/low range, adjacent-close RMS change, direction persistence over three
consecutive cells, and recent-minus-older mean log activity and count. Each has
its own validity mask, giving 204 inputs in total. Native half boundaries remain
fixed; padding is excluded and gaps are never bridged. This tests information
omitted by the original summaries without searching window lengths or thresholds.
Replay checks the sealed temporal arrays against the current clock, membership
and market features, while using the account's current cash and cost inputs.

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B -u research/rl_trading/v7/experiments/entry_temporal_tree.py --output D:/TradingML/runtimes/rl-trading/v7/entry-temporal-tree-v1
python -B -u research/rl_trading/v7/experiments/entry_temporal_tree_replay.py --bundle D:/TradingML/runtimes/rl-trading/v7/entry-temporal-tree-v1 --forecast unused --output D:/TradingML/runtimes/rl-trading/v7/entry-temporal-tree-dev --dates 2026-07-01 2026-08-10
```

`entry_fast_replay.py` accelerates these frozen policies by skipping only flat,
nonpending, unscheduled WAIT clocks. Equity stays constant across such intervals.
Every held second, action and terminal transition still uses the original
environment step. The acceleration source and rule are bound into replay
authority. This is evaluation-only; it must not replace PPO trajectory collection.

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B -u research/rl_trading/v7/experiments/entry_fast_replay.py --recipe weighted --bundle D:/TradingML/runtimes/rl-trading/v7/temporal-weighted-policy-v1 --forecast unused --output D:/TradingML/runtimes/rl-trading/v7/weighted-fast-all150
```

Omitting `--dates` evaluates all 150 training sessions. Available recipes are
`weighted`, `tree` and `temporal-tree`; validation dates remain rejected.

## Attribution-guided manual weight intervention

`manual_weight_edit.py` measures exact final-head contributions on the first120
fit days only. Within the top10 percent of eligible scores, it identifies an
output neuron whose mean contribution is greater for losing than profitable
examples in both chronological60-day halves, and whose mean contribution is
positive in both. It selects the largest minimum contrast and halves that one
outgoing weight. There is one fixed candidate, no optimizer, no gradient update,
and no random perturbation sweep. This association does not establish a semantic
interpretation of a neuron. An edit can improve fit and still harm development.

The original checkpoint is retained. The edited bundle validates that exactly
the declared scalar was halved and every other state tensor is unchanged.
Development is evaluated separately after sealing the edit; it cannot select
another intervention. `manual_weight_replay.py` uses the parity-checked frozen
account replay, including current account inputs and the original execution rules.

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B -u research/rl_trading/v7/experiments/manual_weight_edit.py --output D:/TradingML/runtimes/rl-trading/v7/manual-weight-edit-v1
python -B -u research/rl_trading/v7/experiments/manual_weight_edit.py --output D:/TradingML/runtimes/rl-trading/v7/manual-weight-edit-v1 --evaluate-development
python -B -u research/rl_trading/v7/experiments/manual_weight_replay.py --bundle D:/TradingML/runtimes/rl-trading/v7/manual-weight-edit-v1 --forecast unused --output D:/TradingML/runtimes/rl-trading/v7/manual-weight-edit-replay
```
