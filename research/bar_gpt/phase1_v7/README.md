# Lightweight decoder reconstruction campaign

V7 preserves V6 experiments and their exposed audit. A new immutable audit excludes
every source-job group previously audited; train, development and tiny sets are
byte-identical to the prior corrected experiment subset. Runtime root:
`D:/TradingML/runtimes/bar_gpt/encoder-campaign2-20261117`.

The bounded campaign allows eight identities, eight hours total and sixty minutes
per identity including profiling/evaluation. No full-dataset training is authorized.

The exported vector contains eighteen visible-input scale coordinates, three quote
validity fractions and learned coordinates. A residual dense encoder sees only
supplied bars and their positions/validity/time features. The decoder is one affine
map followed by fixed algebra: open and body in log units; squared upper/lower
wick roots enforce OHLC; squared activity roots enforce nonnegative log1p activity.
There is no sorting, clipping, hidden reference, target statistic or input bypass.
All scale coordinates are part of the fixed-size exported vector.

Canonical input uses centered log prices and centered sqrt(log1p activity).
Geometry input uses log open, signed log body and square-root log wick lengths.
Partial OHLC bars retain every valid canonical input; added geometry features
are available only for complete OHLC groups. Complete source ordering violations
fail qualification and are never silently repaired. Prices use percent-like log units in the loss,
activity uses log1p units; valid channel means and price/activity families receive
equal weight. Fixed input units are 100 for price displacement/body and 10 for wick
roots; these are design constants, not fitted target metadata.

Calibration remains training-only. Sampling gives equal mass to observed
timeframe/last-close-price-band cells; calibration and metrics are unweighted.
Selection is 50 times balanced price-relative mean plus activity log MAE plus
0.05 times worst-band relative mean. Every group, including rare groups, remains
reported. Structural violations and nonfinites are hard failures.

Initial hypothesis: all-visible training with a stronger residual encoder and a
single linear structured decoder improves input reconstruction and fixes the prior
independent-head OHLC defect. Planned ablations vary input coordinates, bottleneck,
encoder capacity and masking; conclusions and exact identities live in the runtime
ledger. All-visible and masked reconstruction are separately reported. Masked
evaluation is a stress test for all-visible-trained candidates.

Run `python -B -m unittest research.bar_gpt.phase1_v7.test_contract` with
`PYTHONDONTWRITEBYTECODE=1`. Use the versioned launcher for an immutable deployment;
it requires dataset and campaign receipts and enforces bounded identities.
