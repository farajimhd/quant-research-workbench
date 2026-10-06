# Laptop teacher bias controls

These candidates diagnose rare-action collapse; none is promoted by default.
Successful TRAIN memorization does not establish development generalization.
Predicting more rare actions alone is not a success criterion.

`run_bias_campaign` compares the existing lag encoder with MLP, causal TCN,
GRU and causal Transformer encoders on authenticated local panels. It checks
balanced TRAIN memorization first, then selects checkpoints using chronological
training-role calibration only. Development is evaluated after selection;
sealed target sessions are excluded; prior feature context remains input-only.
W&B records natural-frequency ENTRY/EXIT
precision, recall, F1, average precision, probability diagnostics, gradient
norms and supervised auxiliary metrics. Accuracy alone is not a success gate.

`bias_panel --valid-price-history` is a separate diagnostic input contract:
the last 120 priced candles strictly before the target, with elapsed gaps
between retained prices and original split-adjusted indicator/V7 values.
It does not regenerate banks, MACD, labels or production ranking. This input
contract is not enabled by the main trainer. Stored-row controls retain the
original history semantics. Panel manifests bind the full bank and label
receipts before the declared ticker/time slice; completed session exports are
hash checked on an exact-source `--resume`.

For the explicit remote-path reader fix, `--plan-only` freezes a new output
binding. `reuse_bias_exports` authenticates original producer bytes and exact
calculation ASTs, then links cached tensors with new receipts and an equivalence
proof. Original exports remain untouched. Unexpected algorithm changes fail
closed. Remote split paths are mapped under the same runtime fence and their
exact split hashes and public role admission remain mandatory.

Main trainer candidates are explicit:

- `--teacher-loss branch-balanced-v3`: balance ENTRY/WAIT and EXIT/HOLD branch
  class masses using fixed TRAIN sample weights.
- `--teacher-heads hierarchical-v3`: independent current and autoregressive
  forecast classification/quality heads, with current plus four actual-candle
  targets. Current heads receive no future ground-truth labels.
- `--teacher-encoder {lag,mlp,tcn,gru,transformer}` and
  `--structured-candle-projection`: select temporal and shared masked V7 slot
  projections. History remains bounded at 120 candles.
- `--auxiliary-loss-weights RATIO FORECAST QUALITY FUTURE_QUALITY`: ablate tasks
  without changing defaults. Zero weights leave those heads untrained.
- `--ticker-regression-loss-weights VALUE BRACKET`: independently weight the
  remaining regressions. A pure action ablation sets all six weights to zero.
- `prepare_candle_normalization`: fit full TRAIN banks and split-adjusted prior
  tails. `--candle-feature-normalization` authenticates every training
  certificate and split receipt and adds no execution-cost inputs.

Normalization and encoder contracts are checkpoint bound. Existing defaults
remain unchanged. These are learning diagnostics, not final training or proof
of trading edge. Local-window experiments use an explicitly declared ticker
subset and market-mean control, not the production ranked attention policy;
`run_laptop_teacher` exercises that actual policy separately.

Example laptop preparation, with repository environment loaded:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
$env:RL_V6_LABEL_AUDIT_RUNTIME='\\DESKTOP-SAAI85T\Workstation-D\TradingML\runtimes'
python -B -m research.rl_trading.v6.bias_panel --output D:/TradingML/runtimes/v6-priced-panel --seconds 14400 --tickers AAPL NVDA MU CYCU SNDK --extended-public-development --valid-price-history
python -B -m research.rl_trading.v6.run_bias_campaign --panel D:/TradingML/runtimes/v6-priced-panel --output D:/TradingML/runtimes/v6-priced-campaign --epochs 20
```

Use fresh output roots. A changed input contract requires a new panel; do not
reuse a stored-row export as if it contained 120 valid-price context candles.
Preserve invalidated experiment artifacts and clearly mark them in W&B;
never use their metrics for model selection.

`compose_bias_exports` can assemble the fixed six-day control from authenticated
per-day exports while the four additional public development sessions prepare.
It copies no new targets into that control and preserves exact source hashes.
After the campaign freezes a calibration-selected checkpoint, use
`audit_bias_campaign --parent CAMPAIGN --parent-panel SIX_DAY_PANEL --panel
TEN_DAY_PANEL --output FRESH_RUNTIME` to evaluate Aug27/Aug28/Sep1/Sep3 without
retraining. This audit requires identical original TRAIN/calibration exports,
normalization and model source, verifies checkpoint identity and replays its
calibration metrics exactly. It rejects role drift or empty admitted days. W&B
records aggregate and per-day rare-action metrics. Previously inspected Aug24
and Aug25 remain exploratory results, not independent validation.
The same frozen audit includes a two-expert control: ENTRY and EXIT checkpoints
are selected independently by their calibration AP, and the known position
state routes inference. Target actions never route predictions. Expert hashes,
calibration replay, probability calibration and thresholds are frozen before
new development evaluation; this adds no training or development-based tuning.
