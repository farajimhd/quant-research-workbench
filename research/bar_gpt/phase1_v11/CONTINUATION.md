# Fixed-rate continuation, 2026-11-19

Continue the preserved lightweight 256-dimensional model at constant 3e-5,
retaining optimizer moments, random state, global step and data traversal.
Use a new W&B identity in `mehdifaraji/bargpt-phase1-v11-mlp512` because the
scientific learning-rate policy changes. The parent remains stopped and intact.

The parent stopped at logical step 927672, epoch index 20, batch cursor 16493.
Set `--continue-full-state --retain-optimizer --epochs 25 --learning-rate 3e-5
--min-learning-rate 3e-5`: finish the interrupted epoch 21 and validate through
epoch 25, then stop. Preserve batch 4096, workers 16, prefetch 4, shard prefetch
32/256 MiB, validation batch 8192 and eight workers with validation graphs.
The run manifest records the complete reproducible command arguments and hashes.

Before updates, evaluate the unchanged checkpoint on the full validation
population. Save `baseline-validation.json` and `checkpoint_start.pt`. Subsequent
`validation-021.json` through `validation-025.json` are the comparable endpoints.
The new `validation/loss` and `development/loss` are masked training-objective
values, including the existing auxiliary terms. Separate all-visible objective
and objective components are retained. Aggregation is a window-weighted mean
of per-batch objective values; it is not a globally recomputed valid-count mean.
Do not infer historical loss values from old raw reconstruction metrics.

The speed repair uses two reusable pinned staging banks instead of allocating
fresh pinned storage for each loader batch. A same-checkpoint 2000-step profile
measured 171738 windows/s versus 172070/s for a fresh old pipeline. The old
long-lived process had slowed to about 83K/s, with pinning allocation dominating
sampled CPU stacks. The repair bounds that hotspot; sustained recovery remains
a runtime claim to verify, not a proven benchmark result.

Full validation retains every certified window, source-valid native errors,
structure checks and both masked/all-visible modes. The model, transform,
objective and decoder dependency are unchanged. Engineering resumes of this
continuation must retain its W&B identity and exact new contract.
