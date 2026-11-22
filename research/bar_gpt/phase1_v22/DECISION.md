# V22 capacity decision, 2026-11-22

Eight bounded runs completed, each2048updates /8,388,608 sampled windows, using
the same114,599 training and11,146 development windows. No full training follows.
The active campaign took23.91minutes including the reporting repair; individual
arms took130–213seconds. Finalist batch qualification took38.43seconds separately.

## Decision

The best fresh-capacity candidate under the declared balanced raw-error/direction
ranking is **e07,64x32 with width16 feature attention**, with368,066 parameters
and only594 decoder parameters. The exported representation has2,048 values,
four times the global512 representation and more than the1,152 raw market values;
this is a smaller model, not stronger compression of raw market values.

The second sampling seed reproduces the main behavior: all-visible candle moving
direction76.98–77.15percent versus52.65 for fresh global512. The matched512-value
64x8 arm reaches59.74percent, supporting a benefit from position structure within
this budget. Global256 does not improve balanced performance. Wider feature
attention improves candle direction but reduces throughput substantially.

**Do not promote the candidate to full training.** The trained V21 parent remains
the stronger overall reconstruction reference. It reaches88.01percent macro
candle direction and0.2277-dollar1s close MAE, versus77.15percent and0.9410dollars
for e07. Candidate range-bin accuracy is12.93percent versus32.86 for the parent.
Masked candle direction remains approximately51percent. Activity balanced
direction is45.00percent, below the narrow64x32 arm46.87 and parent46.19.

The full CPU-FP32 development price-band audit found133,330 valid sub-$0.10 price
fields. Median relative error is196.87percent for e07 and191.29 for its repeat,
versus14.95 for fresh global512. These are field-price bands, not independent
security counts. No valid examples were clipped or dropped. This worst-group
regression vetoes promotion despite the better average candidate ranking.

Shape-coordinate diagnostics show severe amplitude compression: recovered signs
do not imply recovered body/wick magnitudes. The frozen legacy Phase0 readout
still recovers1s candle direction around96.9percent from e07's individual16D bar
embeddings, so total Phase0 direction collapse is not the sole explanation.
These observations do not prove joint Phase0/Phase1 training is impossible.

The next decision should target absolute-scale / shape-amplitude learning and
optimization before another encoder-size increase. This campaign does not
establish forecasting improvement or a fully converged architecture ranking.

## Qualification and limitations

Models share warm feature/temporal parents; readouts are fresh. Width expansion
preserves the feature encoder's initial function with symmetry-breaking readout
perturbations that sum to zero. Fresh-head random draws are architecture-dependent;
this is not an exactly matched full-model initialization ablation.

All capacity arms usedbatch4096 after measured512/1024/2048/4096 profiles. Finalist
large-batch measurements:44,342windows/s at4096,44,971 at8192,44,073 at16384.
The1.4percent measured advantage of8192 is modest and not a replicated speedup.
Peak allocated memory at8192 was18,616MiB. These are cached-pool measurements,
not full-shard throughput claims. Graph/eager gradients passed for16/64 visible.

The200step tiny check demonstrated loss reduction, not near-perfect fitting.
Extending the finalist's fresh-readout check to2000steps reduced loss301->11.23;
meaningful residual errors remained. It should not be described as a successful
near-zero overfit qualification. Ten focused CPU tests and native model tests
passed; actual workstation profiling/training/evaluation completed.

The sample is reused development, not a sealed audit. Direction metrics are
macro means across timeframe/stream groups; candle accuracy excludes unchanged
candles, activity balanced accuracy averages down/flat/up recall. CPU controls
use independently seeded masking with the same distribution, not identical CUDA
draws. Nearest-visible masked1s close MAE is0.04186dollars; it is within-window
reconstruction, not a causal forecast. Raw MAE gaps on the first2048 training
windows are not distribution-matched overfitting tests.

Initial cache preparation was9.16seconds. A legacy resume bookkeeping field
measured235.18seconds from campaign start; this is NOT loader waiting. The source
now measures per-invocation preparation. Runtime receipts remain preserved.

## Reproducibility and retained identity

Workstation root: `D:/TradingML/runtimes/bar_gpt/phase1-v22-capacity-20261122`.
Detailed generated report: `COMPARISON.md`, `summary.json`, `worst-groups.json`;
full price/variance audits: each arm's `diagnostics-11146.json`.
W&B: https://wandb.ai/mehdifaraji/bargpt-phase01-v22-capacity
All8 remote runs finished; no checkpoint files or artifacts were uploaded.

Research checkpoint, NOT a promoted replacement:
`e07-slots32-wide16-s559/checkpoint_last.pt`
SHA256:`a9c018fc4a37c6cf01a50021d68c459abe26dad90adb43534ab9d2854c63f6ee`.
Use the README reproduction command and `phase1_v22.model.restore`.

Training source88f206014; terminal W&B API repair9893dd687. Large-batch/tiny
qualification source2fac8c0e7. The first deployment lacked a transitive pipelines
import and failed before training; a fresh verified snapshot corrected it. The
first completed run's W&B terminal API error was repaired without retraining or
changing its identity/steps. Checkpoints, optimizer/RNG state and logs remain local.

Frozen dataset hash:`434661f361dbbc228f1bfd287455cb1ab71029c9c9e7d669788cfdbc959c2951`.
Supplement:`d9e8bd9a84efd4172caf747b55648b70692167ff4bc3b80a59a1b88ba167fbf3`.
Split:`d60f707c91c54b72ec7a11a9119334cc973351be699c8b6d5f3d724957640d63`.
Parent:`a5279ed6fd2fd6ca44cf5ab14573a4383f9f66a589ed3d8c37c64caad28300d5`.
Frozen source split and data-file hashes, teacher/calibration provenance, counts,
normalization and loss rules are recorded in manifests. No new event reads occurred.
