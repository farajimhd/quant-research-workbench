# V6 phase-1b teacher sequence contract

This architecture uses the published copied/suppressed 1b labels. It does not
recalculate bars, indicators, 1a or 1b labels. The new checkpoint contract is
`rl-v6-1b-autoregressive-five-candle-v1`. Legacy checkpoints require the explicit
legacy architecture (`--teacher-forecast-steps 1`); incompatible continuation
is rejected by action version, manifest configuration and strict state loading.

## Inputs and outputs

The existing strict-prior observation remains 120 **actual** bank candles per
listing: 37 scalar features and 10 V7 slots with 11 fields (147 total). Masked
price rows remain in the bank history. MACD remains the approved observed-candle
calculation. Future clock coordinates, gains, groups, scores, ratios and labels
are targets/metadata only, never market features. The local ResNet diagnostic
now uses the same strict-prior cutoff as the main chronological trainer.

The existing 128-dimensional candle encoder and bounded market attention are
retained. The new decoder adds a projected causal full-market mean to each
listing representation, including listings outside the attention rank. Rank
limits attention cost, not action eligibility, consistently in teacher and
PPO. All other causal eligibility/OMS masks remain in force.

- The conditional current-action head predicts ENTRY/WAIT when flat and
  HOLD/EXIT when held. Rejected episodes supply flat WAIT examples; they do
  not manufacture held-state examples. The complete suppressed label sequence
  is nevertheless supervised by the four-class forecast head below.
- The sigmoid sizing head predicts the saved 1b relative allocation ratio,
  only on selected ENTRY rows with `allocation_loss_mask`. The optimized local
  teacher path and the PPO path share exactly the same sizing parameters.
- The autoregressive forecast head predicts the saved four-class combined
  chart-label distribution for the current price candle and the next four
  **observed price candles**, within the same ticker/session. These are not
  necessarily consecutive seconds. Missing targets at session end are omitted,
  rather than padded with invented WAIT labels. This forecast is an auxiliary
  sequence output; its HOLD/EXIT predictions do not bypass flat-state OMS masks.
- Existing opportunity-value regression retains its declared price-bps target,
  not the fee-adjusted selection score or the PPO state value. Legacy bracket
  heads are present for compatibility but have no targets in current 1b data.

The forecast decoder shares the existing action GRU. Its first state is the
causal listing/context representation. Later states use that representation,
an embedding of the **previous** label distribution and three zero execution
channels. Training uses previous-label teacher forcing; evaluation and
`policy.forecast_labels()` roll out model probabilities with no label inputs.
This pretrains GRU weights without inventing fills, cash ledgers or realized
P&L. The execution action embedding and response to fills/P&L still need PPO.
This is supervised sequence pretraining, not supervised broker execution.

## Objectives and validation

Action loss is the existing soft cross-entropy and optional class balancing.
Sizing uses Smooth L1 with beta 0.1 on ratios in [0,1]. Forecasting uses soft
cross-entropy averaged over the available horizons of each sequence. Task
weights are one. Sizing/forecast task masses have separate fixed session
normalization, so WAIT and unavailable targets do not dilute sizing loss.

Report allocation ratio MAE/count and forecast cross-entropy/count at each of
five horizons. Development forecasts are **free-running**. Checkpoint selection
prioritizes exact ENTRY F1, then ENTRY-class F1, then lower allocation MAE, then
lower existing label loss. All admitted development days must supply sequence
and sizing evidence. Sealed dates remain unavailable to this evaluation.

Ratios are independent preferences over active hindsight opportunities, not a
session-wide softmax or guaranteed portfolio ledger. At entry i the saved target
is `score_i / sum(active_scores)`. They may reference competitors whose future
teacher selection cannot be known causally. Prediction is therefore an estimate,
not an exact reconstruction of the teacher denominator. PPO currently interprets
the output as its fraction of available cash; sequential decisions may change
the remaining cash. These targets do not impose a simultaneous cash allocation.

## Bounded laptop validation

Use the public training split only, verify source/current/prior bank bytes and
label receipts, and retain diagnostic artifacts under the laptop runtime:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
& C:/Users/g835l/miniconda3/envs/ml4t/python.exe -B -m research.rl_trading.v6.probe_teacher_sequence `
  --output D:/TradingML/runtimes/rl-v6-teacher-sequence-20261006/published-probe `
  --day 2026-07-31 --tickers AAPL NVDA --seconds 60 --epochs 3
```

The probe uses the real loader and chronological training core, but explicitly
rekeys a bounded ticker subset. Its metrics are same-training-slice optimizer
checks, not development generalization, portfolio performance or final training.
It verifies updates of the encoder, shared GRU and sizing head, plus exact
checkpoint reload. The workstation GPU is not used.

The main launcher defaults to five-candle supervision. Existing dataset, source,
LULD, normalization and sidecar gates remain intact. Final training is deferred
until workstation GPU availability is confirmed; this change starts no final
teacher training or PPO run.
