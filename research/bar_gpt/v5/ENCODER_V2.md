# Encoder v2: all-feature z scores

`encoder_v2.py` duplicates v1's architecture and dimensions. The preprocessing
contract differs: every one of the 18 market features uses `(x-mean)/std`, with
separate frozen population statistics for each of the six timeframes. There is
no market logarithm or sample-relative price reference. The nine UTC features
and 60% whole-column training mask remain unchanged. Full validation observes
all columns and scores every source-valid value from the same forward pass.

Run `python -B -m research.bar_gpt.v5.zscore_calibration_v2 --split SPLIT
--output RUNTIME_SETUP --workers 8` once. This scans only the sealed training
ranges, verifies full source payload hashes, and reduces NumPy Float64 blocks
using parallel Welford merges. Every stored overlapping training occurrence
retains its actual weight; invalid values are excluded and valid zeros included.
Population variance uses `ddof=0`; constant features explicitly use std=1.
An SQLite ledger makes interrupted preparation restartable. The sealed JSON
records counts, means, standard deviations, split binding and complete coverage.
No statistics are fitted during training or validation.

`run_encoder_v2.py` requires that completed calibration and validates its seal
and split before training. It preserves the v1 orchestration, AdamW, scheduling,
guard, checkpoints and reporting, with separately versioned model/preprocessing
contracts. V1 weights are not resumed. All encoder and decoder parameters are
present in each checkpoint. New output folders create independent W&B runs.

The decoder predicts z scores. Raw validation applies `prediction*std+mean`
in Float64 using the same frozen per-frame statistics and original source-valid
targets. It retains the bounded asynchronous CPU reduction, explicit counts,
overflow reporting and no clipping. Physically negative reconstructions remain
visible as errors. New runs use the existing `validation_full` and
`validation_full_raw_mae` namespaces in their independent W&B identity.

The authorized fresh experiment retains corrected-v4 data, exact chronological
split, large latest-bar512, BF16/SDPA, batch4096, 32 workers/prefetch2, 80 total
epochs, cosine minimum5e-5, zero warmup updates, full validation per epoch,
loss10/metrics1000, overfit patience8 and no-learning patience3. Only the peak
learning rate changes to1e-3. Existing v1 evidence remains immutable.
