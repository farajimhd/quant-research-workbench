# Combined activity basis and pre-floor change objective

Hypothesis: the basis audit showed almost no average-level drift from an
unconstrained change-only update in DCT coordinates, but the output floor broke
that property. Pre-floor loss alone did not repair full validation. Test their
interaction, comparing native `joint_prefloor` against `joint_basis_prefloor`.
Only the candidate's six activity decoder streams change optimizer coordinates;
both arms use pre-floor auxiliary changes. Output semantics, level penalties,
prices, source masks, 512D embedding and encoder architecture remain unchanged.
Same parameter count, one affine decoder plus a fixed inverse basis. No data
statistics, reference tokens, hidden targets or input bypass enter the decoder.

Bounded first check:1,000updates each, same retained body-direction parent,
seed559, LR1e-6/bar3e-8, train114599/dev11146, existing profiler and tiny-fit gate.
New W&B identities; no rejected checkpoint resumes. Compare all216raw groups and
direction/range metrics against both matched control and retained parent; flag
>5%raw or>1pp accuracy regression. A final-point advantage is insufficient.
Existing50-minute loop deadline reserves evaluation time within60minutes/arm.

Ten local tests passed, including FP64 affine/embedding-gradient parity, zero
DC gradient for unconstrained differences, checkpoint restoration, identical
next Adam step after optimizer-state restoration, and legacy restore rejection.
The checkpoint declares `activity_dct64_v1` and uses distinct parameter keys;
restore with `temporal_basis.restore_basis`, not the legacy native-linear loader.
Fixed basis and channel-index buffers are checked exactly before loading.

Before candidate training, a real512-training-window gate checks16/64 visibility:
price codes must be bit-identical and activity codes within3e-5absolute/relative
tolerance. This accounts for FP32 basis conversion; it does not redefine raw
validation metrics. The gate preserves the subsequent RNG stream. Initial
validation differences must be reported, not assumed exactly zero. No change to
the full-training launcher is authorized by a passing diagnostic alone.
