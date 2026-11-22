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

## Completed result, 2026-11-22

Source0b21e03e8; runtime `phase1-v21-range-balance-20261122`. Both arms completed
3000updates, batch8192, seven validations, tiny-fit and graph-gradient gates.
Control W&Bcnzhdsbk, candidate tuzwi7ni. Control329.5s/79,211cached windows/s;
candidate327.6s/79,735/s. Cache preparation3.75s; profiled batches256-8192.
Weights0.614-3.055, no empty training bins. Initial metrics match exactly and
control reproduces the earlier slow-temporal run. This is not full-data speed.

Against control,108/216 raw groups improve; only2 exceed5% regression, both
daily volume (+17.55%masked,+7.71%visible). Aggregate direction/range metrics
have no>1pp regression. Against parent,180/216 raw groups improve,4 regress>5%,
and no aggregate accuracy group regresses>1pp. Visible/masked1s close MAE
0.20794/0.19346 dollars. Masked daily ask range accuracy82.604% versus
control72.605%; all-visible84.187% versus parent78.850%.

However, aggregate accuracy hides a bin-level tradeoff: masked1m bid true
0-0.05% range accuracy42.426% to30.534% versus parent (18432 cells). Ask
41.933% to30.637% (18272cells). Zero-range worst decline is only0.08pp, so
the damage is mainly tiny positive ranges, not zero ranges. Daily volume MAE
796642 versus parent729291; masked daily volume worsens26.55%, masked daily
quote counts about6.4-6.5%. These fail the worst-group safeguards.

Frozen actual-checkpoint restore and train/dev/price-band audit completed in
17.33s: `phase1-v21-range-balance-audit-20261122`, W&Bap6lcqvr. Train186/dev180
raw groups improve;24 worsen in both partitions,12 only dev. Masked daily
volume worsens27.21% on train, so it is not only validation overfit. No
collapsed embedding dimensions or zero/tiny close denominators. Dev below$0.10
1s trade close mean relative error2.809% to3.029%, median2.465% to2.764%,
P905.241% to5.506% (4096cells). $0.10-1 mean improves2.520% to2.427%.

Retain research checkpoint SHA
`3025a574f5d613e51df15dfa6b5476b10a9ff1eed15b58575c3dff40a337f298`,
but reject full20 promotion. Bin weighting can recover large ranges without
speed cost, but does not solve simultaneous small-range/activity retention.
Do not call this fixed from aggregate range accuracy. A next change needs to
address that explicit tradeoff; do not repeat the completed gradient audits or
blindly sweep global weights. Original checkpoints and STOPs remain preserved.
