# Laptop teacher bias controls

These candidates diagnose rare-action collapse; none is promoted by default.
Successful TRAIN memorization does not establish development generalization.
Predicting more rare actions alone is not a success criterion.

`run_bias_campaign` compares the existing lag encoder with MLP, causal TCN,
GRU and causal Transformer encoders on authenticated local panels. It checks
balanced TRAIN memorization first, then selects checkpoints using chronological
training-role calibration only. Development is evaluated after selection;
sealed sources are excluded. W&B records natural-frequency ENTRY/EXIT
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
