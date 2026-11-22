# Joint single-head development trials

These are bounded development experiments, not a full-training qualification or
evidence of forecasting profitability. All reported window metrics below use
the unchanged 11,146-window development partition, all bars visible. The paired
masked metrics, all 18 channels and six timeframes are retained in each run's
evaluation JSON and W&B records.

The architecture has 5,330,400 parameters, including 590,976 in its only decoder:
one FP32 linear 512-to-1152 projection. Feature attention produces a standalone
16D bar embedding; temporal attention produces a 512D window embedding. The
decoder receives only the latter. Tests cover hidden-input isolation, both
encoders receiving gradients, affine initialization, source-valid loss masks,
and decoder-free encoder exports.

## Matched first trials

Source: `9b9e75a88a18fa308bea8769f7241ee756620202`.
Runtime: `D:/TradingML/runtimes/bar_gpt/phase1-v20-joint-singlehead-20261121`.
Each arm used 3,000 updates, batch 8,192, temporal/head LR 1e-6 and bar LR 1e-7.
Training sampled uniformly from 67,606 original training windows plus 46,993
sealed real training windows. This representative sample is not an unbiased
full-population estimator. Fixed activity-reference calibration uses only that
training pool; it changes inputs, not output coordinates.

| Arm | 1s close MAE, dollars | 1s candle direction, % | 1s volume direction, % | Daily bid-count MAE | Seconds |
|---|---:|---:|---:|---:|---:|
| Frozen bar encoder | 0.27820 | 91.17 | 79.35 | 35,319 | 239.86 |
| Joint, unchanged inputs | 0.24859 | 91.40 | 79.31 | 35,889 | 320.36 |
| Joint, median activity reference | 0.20357 | 93.57 | 78.19 | 218,924 | 320.61 |
| Joint, square-root-median activity reference | 0.25184 | 92.36 | 80.04 | 24,799 | 319.00 |

For context, the older V18 development checkpoint had 1s close MAE $0.40473,
candle direction 88.06%, volume direction 73.15%, and daily bid-count MAE 32,628.
It is not a matched initialization control. The V19 initialization already had
close MAE $0.31385 and daily bid-count MAE 28,018. Do not attribute all gains over
V18 to joint training.

The median-input arm is rejected for count errors. The square-root arm improves
several window metrics but loses about 4.35 percentage points of daily bid-range
bin accuracy relative to V18, and some 10s count MAEs regress about 7.5%.
No arm passes the full non-regression gate.

## Standalone bar information check

Source: `c6b8b2ffc19ad290e8963629054110d4069a3dd1`.
Runtime: `D:/TradingML/runtimes/bar_gpt/phase1-v20-bar-probes-20261121`.
A linear readout was refitted on training bars only for each frozen encoder,
using identical ridge and observed-target masks. This diagnostic readout is
not part of the joint model or its training. It avoids treating an embedding
basis change under the old decoder as proof of lost information.

| Frozen encoder | 1s close probe MAE, dollars | Daily bid-count probe MAE |
|---|---:|---:|
| Original epoch-19 Phase0 | 0.01654 | 16,471 |
| Joint, unchanged inputs | 0.01638 | 16,425 |
| Joint, square-root activity reference | 0.01604 | 312,406 |

The square-root arm is therefore not suitable as the current recommendation
when standalone bar information retention is required. The unchanged-input arm
is the safer research parent, but still needs its window-level regressions
resolved. A linear probe establishes linear recoverability only, not exhaustive
information content or forecasting utility.

The unchanged-input checkpoint SHA256 is
`fb30d0de8165fb33dfdec54901b0bc77f203d08281b729f1626e5c06aa68de13`.
Its `encoder-exports/manifest.json` pins separate 16D and 512D exports with no
decoder weights. Parent checkpoints and stopped full runs remain preserved.

## Engineering and next gate

The initial eager cached profile selected batch 8,192 at about 79,036 windows/s
and 12,145 MiB. The later CUDA-graph profile with the optional visible-only head
gradient policy reached about 85,318 windows/s and 12,599 MiB. These profiles
also differ in gradient policy, so they are not a pure graph speedup ablation.
Every captured loss component, norm and parameter gradient passed eager parity
for both 16- and 64-visible paths. Full-loader throughput remains unqualified.

The visible-only head-gradient ablation runs under its own identity at
`phase1-v20-visible-head-20261121`, source c6b8b2ffc. Both encoders still train
on masked batches. Both arms completed 3,000 updates:

| Input | All-visible 1s close MAE | Masked 1s close MAE | Candle direction | Daily bid-count MAE | Daily bid-range bin accuracy |
|---|---:|---:|---:|---:|---:|
| Unchanged | $0.24997 | $0.30149 | 90.87% | 33,305 | 69.42% |
| Square-root activity reference | $0.25228 | $0.31978 | 92.12% | 26,642 | 70.31% |

Reject this gradient-policy change for now: although range accuracy improves
relative to the corresponding first trials, masked close MAE worsens from
$0.25562 to $0.30149 (unchanged inputs), and $0.27761 to $0.31978 (square-root
inputs). Candle direction also declines. It does not solve the tradeoff.
The complete reproducible `v20-comparison.json` report includes the ten worst
raw-MAE ratios and direction/range deltas versus V18 for every arm.

Retain joint unchanged-input training as the safer research parent, not an
approved full-run winner. The next scientific issue is simultaneous preservation
of range and activity scale; simply changing activity inputs or suppressing
masked decoder gradients did not solve it. Six focused CPU tests passed and
captured GPU gradient parity passed; all six trial checkpoints are preserved.
No full 20-epoch V20 training has been launched. That requires resolving the
remaining regressions and qualifying complete validation and resumable loading.
