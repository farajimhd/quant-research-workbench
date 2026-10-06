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

`run_balanced_bias_campaign` tests two fixed architectures (lag and structured
TCN) with 64 examples from each action per 256-row minibatch. It samples with
replacement using the original TRAIN weights within each class. Its unweighted
BCE therefore estimates the same equal-class objective as inverse-frequency
loss weighting, with lower class-mass variance; it never applies both methods
at once. The original TRAIN/calibration split and data remain unchanged. Size,
quality and future heads receive no training in this action-only control.
Select checkpoints only on calibration, then use the same frozen audit path.

The fixed v3 diversity panel (`bias_panel --training-diversity
--valid-price-history`) fits July31/Aug3/Aug4/Aug5/Aug6/Aug7 and reserves
Aug10/Aug11 for chronological TRAIN-role calibration. Its six development
dates are explicitly previously inspected and exploratory. It changes no
features or targets. `reuse_diversity_exports` proves feature/target/reader
equivalence and authenticates exact bytes before reusing the nine overlapping
exports under new receipts. Five new public TRAIN-role exports still require
the full bank, split and label audits. Mixed published dataset identities are
rejected. A bounded follow-up compares natural lag, structured TCN and focal
structured TCN for ten epochs; it remains a 19-ticker experiment, not final
full-market training or sealed validation.
# CUDA tree signal control

`run_tree_bias_control` is a bounded alternative-learner diagnostic, not a
replacement for the autoregressive market policy. It admits only the authenticated
six-TRAIN/two-later-calibration valid-price diversity panel. Inputs are strictly
prior last/delta/mean/std summaries over 5/20/120 actual candles, supplied causal
market snapshots, and known held state. Absolute log-price is causally recentered;
relative OHLC bps are not adjusted again. No future target, episode identity, or
listing identity is an input. All development dates are previously inspected and
remain exploratory.

XGBoost 3.1.3 is loaded from an explicitly supplied isolated runtime dependency
directory. The launcher requires a CUDA build and verifies CUDA device configuration
after fitting; CPU fallback fails closed. Three predeclared controls use natural
weights/depth4, equal class mass/depth4, and equal class mass/depth6. Separate ENTRY
and EXIT binary experts use known state routing, chronological calibration-only
aucpr early stopping, 400 maximum rounds, and exact checkpoint prediction replay.
W&B records natural-frequency action metrics, probability calibration and false
positives. This control does not supervise sizing, quality or future heads.

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B -m research.rl_trading.v6.run_tree_bias_control --panel D:/TradingML/runtimes/rl-v6-bias-panel-diverse-v3-20261006 --output D:/TradingML/runtimes/rl-v6-bias-tree-control-20261006 --dependencies D:/TradingML/runtimes/rl-v6-bias-xgboost-deps-20261006
```

## Original-target hierarchy diagnostic

`prepare_hierarchical_bias_targets` authenticates the existing public 1b target
shards and aligns `action_1a`, `reference_action_1a`, and `episode_selected` with
exact panel episode/clock keys. It verifies final copied/suppressed ENTRY and
held EXIT parity. Eligibility is read from full published decisions, never inferred
from a truncated four-hour panel. Inputs and source labels remain unchanged.

`run_hierarchy_bias_campaign` compares a flat structured-TCN control with structured
TCN and GRU hierarchy controls. The flat ENTRY score combines an eligibility head
with a timing head conditional on eligible episodes. The timing auxiliary loss is
masked to selected flat episodes; eligibility uses all flat rows. This is conditional
factorization, not an independence assumption. Known held-state EXIT is unchanged.
Fixed TRAIN class masses balance the joint action loss and each component loss;
component losses have weight0.5 each. Ten epochs, seed17, chronological calibration
AP selection, exact checkpoint replay, and exploratory development evaluation are
fixed. Size, quality and future heads remain untrained in this diagnostic. No
production policy replacement or full workstation training occurs.

### Candidate-specific underfit gate and price autoregression

Every hierarchy diagnostic candidate now runs a TRAIN-only 128-row control (32 per action) with its actual architecture and objective before calibration or development evaluation. All four action F1 scores and supervised hierarchy component F1 scores must reach 0.95 within 200 epochs. A failed candidate stops the campaign; prior base-encoder memorization is not a substitute. This bounded memorization criterion is an engineering diagnostic, not a claim of generalization or absence of all bias.

The optional authenticated exact-clock price sidecar supervises current and next four observed-price returns through a separate GRU decoder. Equal free-running and teacher-forced SmoothL1 losses have weight 0.1; scale is the TRAIN-only 90th absolute-return percentile. Missing boundary targets are explicitly masked. Future prices never enter the current action head. Price MAE and the zero-return persistence baseline are reported separately; source observations, approved labels and production defaults remain unchanged. These are laptop diagnostics, not final teacher training.

### TRAIN-only complete local-head underfit control

`run_full_head_underfit` uses the authenticated diversity panel and a fixed 128-row TRAIN-only subset, with 32 rows per current action. Structured TCN, GRU and Transformer each train their actual combined current-action, quality, ratio and free-running/teacher-forced next-four action objectives for at most 400 epochs. Admission requires every current action and every action at all five forecast horizons to reach F1 0.95, ratio MAE at most 0.02, and current ENTRY/EXIT quality MAE at most 0.02. These regression tolerances are engineering learnability diagnostics, not user-approved financial success criteria. Missing classes or regression supervision fail the gate. No calibration/development evaluation runs in this control. Full production market attention and future quality heads remain outside its certification; passing this local control cannot certify the complete production teacher. Forecast list metrics now flatten into explicit horizon keys for W&B.

### Held forecast contract v2

The complete local-head experiment exposed an intentional v1 limitation: only flat decisions carried forecasts, and validation rejected held forecasts. Contract v2 adds held supervision from saved reference actions and exit-quality fields. Conditional actions follow the existing held rule (EXIT when reference action is EXIT, otherwise HOLD), with probabilities [0, 0, 1-exit_quality, exit_quality]. Targets stop at the same-listing/pair boundary or missing exit coverage and retain actual timestamps. No cash, fill, future observation or new hindsight calculation is introduced. Existing immutable panels remain unchanged and require fresh forecast-target preparation before certifying all heads. The old all-head experiment is incomplete due to missing held coverage, not evidence that every architecture failed to learn.

`prepare_complete_forecast_targets` binds a separate flat/held action, quality and timestamp sidecar to the original panel hash and current published shard receipts. It requires exact original flat-forecast parity and held current-action parity. The local all-head runner now requires this sidecar and validates class coverage before training. Future ENTRY/EXIT quality is supervised in both free-running and teacher-forced decodes and is included in the underfit MAE gate; this does not change production observations or published labels.

### Complete-head chronological generalization admission

`run_complete_head_generalization` admits only the TCN with a completed, passing all-head TRAIN gate bound to the exact panel/forecast sidecar hashes. It verifies the underfit checkpoint and compares model, objective, encoder and hierarchical-head source to the underfit commit before starting. It trains from seed 17 with fresh AdamW for ten fixed epochs, records natural-frequency TRAIN and chronological calibration metrics for every supervised head, selects by calibration mean ENTRY/EXIT AP, and evaluates previously inspected development dates only after checkpoint selection and exact calibration replay. Failed GRU/Transformer candidates are not run. This remains a local 19-ticker diagnostic; full production market attention is not certified by it.

### Larger TRAIN-only fitting controls

The complete-head underfit runner accepts bounded 32/128/256/512 examples per current action, retains deterministic per-horizon forecast coverage, and can run selected architectures. Architecture, optimizer, loss and pass thresholds remain unchanged. The next TCN control uses 512 total examples rather than 128; it evaluates TRAIN only. Future-quality metrics are mandatory for admission, so an incomplete report cannot pass. A small-sample pass is not proof that the larger TRAIN distribution is fitted or that the model generalizes.
