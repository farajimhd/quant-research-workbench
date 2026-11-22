# Range-bin conditioning experiment

Hypothesis: zero/tiny-range prevalence makes the range objective underemphasize
larger ranges. Prior frozen output-gradient and actual-update audits motivate
testing inverse-square-root target-bin weights, not a blanket loss multiplier.

Compare `joint_basis_prefloor_slow_temporal` with
`joint_basis_prefloor_range_balanced`, both freshly initialized from retained
body-direction parent. Same architecture, seed559, train114599/dev11146, masks,
activity DCT head, pre-floor activity objective, barLR3e-8, temporalLR1e-7,
decoderLR1e-6 and Adam defaults. Only range Huber/ordinal terms are reweighted.
Fit three family-specific six-bin count tables on source-valid training high/low
pairs only. Inverse-sqrt counts are normalized to expected weight1 per family;
keep original loss denominator to avoid per-batch renormalization. Empty bins
have no training observations and weight0; no valid samples are discarded.
Calibration affects training loss only, never inference inputs or reported metrics.
No new decoder input or reference metadata; 512D embedding remains sole input.

Plan3000steps per arm, validation every500, existing bounded profile, tiny-fit,
16/64 CUDA-graph parity and initial native/basis parity gates. Same batch across
arms; record actual throughput, not historical full-data estimates. Existing
50-minute loop deadline leaves evaluation reserve within60minutes per arm.
Compare216raw groups, zero-range and all range bins, direction, price bands,
activity and standalone bar probe against control and parent. Flag>5%raw and
>1ppaccuracy regressions. Do not promote on aggregate loss alone.

Thirteen focused tests passed before launch, including expected calibration
mass, unchanged initial tensors and non-range losses, and finite gradients.
New checkpoint contract stores calibration; restore reconstructs and verifies
it. Original checkpoints and STOPs are preserved. This is a new scientific
identity, not a resume of rejected full training.
