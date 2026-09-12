# Ticker-specific causal historical-level reaction model, v1

This experiment predicts price interaction with both surrounding historical bands
at each eligible completed SIP second. It does not change bands or strategy orders.

Run from the repository with the installed research Python environment:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
& C:\Users\g835l\miniconda3\envs\ml4t\python.exe scripts/train_level_reaction.py `
  --ticker AAPL --start 2026-07-01 --train-end 2026-08-20 --test-day 2026-08-21 `
  --runtime D:\TradingML\runtimes\reaction-level-model\AAPL-jul-aug2026-v1-batched
```

## Data and time boundaries

- Canonical `market_sip_compact.events_YYYY` is the only market-event source.
  Reuse the certified historical trade-condition policy and canonical codecs.
  Quotes use primary ask, secondary bid and their own price-scale bits. Latest
  quote events provide spread/size context; these are not depth or reconstructed
  consolidated NBBO. Invalid or older-than-five-second quotes become missing.
- Use the exchange calendar to verify every requested session plus the preceding
  seed session. The standard AAPL run seeds June 30; July 1-August 13 fits the
  classifier, August 14-20 calibrates it, and August 21 tests the frozen model.
- Build each day's examples before extracting that day's retrospective levels.
  Only the previous finalized book is visible to the examples. Corporate actions
  adjust visible historical geometry/estimates before feature generation and are
  recorded in partition provenance. New-day evidence enters the next checkpoint.
- Historical centers and role state are frozen from the previous session. V1 has
  no streaming level creation, same-day historical-statistic updates, learned
  importance score, or structural-detector integration. Rolling high/low position
  and returns provide price-structure context; they are not detector outputs.
- One-second grids retain absent OHLC as missing. Feature reference price can use
  the last observed close for up to five seconds, with explicit age and observation
  coverage. Rolling features use elapsed-time windows of 5/30/60/300/1800 seconds.
  Initial warmup is 60 seconds; longer incomplete windows remain missing/partial.
  Volume reflects the existing price-eligible bar dataset: excluded-price-second
  volume is exposed in source audit, not invented in the grid.
- Serving features are `data.feature_rows`; the labeler is separate. A streaming
  adapter can run this same prefix transform, but no service is deployed here.
  This is SIP event-time causality, not a simulation of feed availability latency.

## Input and target contract

Keep levels sorted by their band edges. Select the smallest upper edge at/above
price and largest lower edge at/below price (stable-ID tie breaking). Containing
bands are not skipped. A containing band can be selected for both directional
hypotheses; overlapping bands can be distinct. A missing side produces no target
row, while the other side explicitly receives missing-neighbor features.

Each row contains both bands' normalized boundaries/centers, dispersion, counts,
role, session evidence, weakening status; pair geometry; price/volume/quote context;
and a target-upper flag. IDs and hashes are audit columns, never model inputs.
Price-distance features use bps and trailing range units with a tick floor.

The level pair, target band and reaction margin stay frozen for labeling, even if
subsequent price would select another pair. Horizon is 60 seconds:

1. `not_reached`: no touch during a sufficiently observed horizon.
2. `rejected`: following contact (or already inside at prediction), a close retreats
   past the approached band edge by max(two ticks, twice trailing mean 1s range).
3. `broken`: following contact, two consecutive observed 1s closes clear the far
   edge by one tick. A gap over a band counts as reaching it.
4. `unresolved`: reached but neither first-event rule completes in the horizon.

Earliest qualifying rejection/break wins. Missing future prices for more than
five seconds, or session truncation before resolution, gives censored label -1,
retained but excluded from training/scoring. Results resolved before a later gap
remain valid. Outcomes are close-based OHLC rules, not inferred intrasecond paths.

## Fitting and evaluation

Fixed histogram gradient boosting: 60 iterations, 15 leaves, minimum 200 rows per
leaf; no random early-stopping split. One AAPL model evaluates each side with the
same schema. Multinomial logistic calibration uses only the reserved last five
training-period sessions. Do not tune parameters using August 21. Compare against
the fit-period class-frequency baseline with log loss, multiclass Brier score,
balanced accuracy, confusion matrix, and conditional resolved-contact log loss.
Report target-side/session-period breakdowns plus first-prediction-per-contact
metrics. These do not turn correlated forecasts into independent trades; one
test session cannot establish robust performance or profitable execution.
August 21 was visually inspected in earlier historical-level research, so it is
a held-out date for this fit, not a previously unseen research holdout.

## Persistence and resource bounds

Each input partition contains its canonical revision and content hash. Each
prepared partition pins the prior book, splits, Parquet hash and resulting daily
checkpoint hash. A root manifest pins the feature/label contract, source code,
package version, dates, runtime and Git revision. Source changes fail resume.
Completed daily partitions are reused after integrity checks. Atomic status and
partition manifests expose current progress and interruption/failure information.

Model and calibration are frozen and hashed before opening the test partition.
The model artifact, feature schema, date split, predictions and evaluation remain
under the run directory. Restart verifies the model and prepared partitions.
Queries use two threads and a 512MiB query limit. Dataset creation processes one
session at a time; training matrices are float32 memory maps; fitting uses four
CPU threads. The canonical reader caps candidate counts rather than truncating.
No operational database tables, services, V6 campaign, or live consumer change.
