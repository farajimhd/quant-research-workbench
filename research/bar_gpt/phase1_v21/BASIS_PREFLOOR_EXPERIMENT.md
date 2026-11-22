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

## Initial result

Runtime `phase1-v21-basisprefloor-20261122`, source
`eaebdbeb6f8e92bfcca3e2a684a65f2beb65adc2`. W&B control `oqromiax`, candidate
`xn9izr38` in `mehdifaraji/bargpt-phase01-v21-repair`. This profile selected4096
for both arms; earlier8192-batch trials are not matched by update count.
Real initialization code error was at most2.29e-5 with bit-identical prices.
Candidate tiny loss121.68 to48.72; both graph modes qualified.

At1,000, candidate improves169/216raw groups versus control; worst raw regression
0.377%, worst direction/bin regression0.265pp. Throughput75,594 vs76,225cached
windows/s, durations66.5 vs68.5seconds. Candidate/control close MAE is
$0.23679/$0.25074 all-visible and$0.28362/$0.29261 masked. Daily-volume MAE
744,375/745,363; activity-volume direction79.249%/79.210%.

Against the retained parent,49raw groups regress>5% and2accuracy groups>1pp;
worst masked quote+52.8%. At500,43raw groups regress>5% against control. Therefore
retain only for a longer paired qualification, not full training. A new3,000-step
horizon starts from the same parent; neither1,000-step checkpoint is overwritten
or described as an exact resume. Keep its batch and sample exposure explicit.

Comparison command: `python -B -m research.bar_gpt.phase1_v21.compare_prefloor
--run RUN --control joint_prefloor --candidate joint_basis_prefloor` (one line).
It requires the basis initialization receipt and records every initial metric
delta, plus comparisons against control and parent. The initial price/function
gate remains authoritative; floating-point activity differences are not hidden.

## Longer result: partial gain, not full-training qualification

Runtime `phase1-v21-basisprefloor3000-20261122`, same sourceeaebdbeb6. This profile
selected8192 rather than4096; both arms use the same batch and sample sequence.
Do not describe it as an exact resume. Control W&B`t6hwggmi`, candidate`pkubjxhl`.
Durations328.1/323.1seconds; cached speed79,690/80,050windows/s. All3,000updates
completed. The control reproduces the earlier8192-batch pre-floor trajectory.

Candidate improves104/216raw groups versus control, but28regress more than5%;
worst all-visible10s ask-low MAE is+19.04%. Worst accuracy regression is0.299pp.
Selected candidate/control results:

| Metric | Combined candidate | Pre-floor control |
|---|---:|---:|
| All-visible1s close MAE | $0.21176 | $0.21605 |
| Masked1s close MAE | $0.21210 | $0.28347 |
| 1s candle moving direction | 90.301% | 90.330% |
| 1s volume moving direction | 79.282% | 79.253% |
| Daily-volume raw MAE | 789,467 | 811,312 |
| Daily bid-count raw MAE | 24,363 | 24,205 |

Against the retained parent,150/216raw groups improve,12regress>5%, and5accuracy
groups regress>1pp. Worst raw regression is20.72% for masked daily volume;
worst accuracy regression is2.369pp for masked daily ask-range bins. This is a
meaningful reduction in masked-price degradation relative to the isolated loss
change, but does not satisfy full-training safeguards. Retain as a research
candidate; do not launch20epochs or claim activity-direction repair. Earlier
points remain noisy (e.g.1,500-step close MAE0.2488 vscontrol0.2168).

Next useful diagnosis is matched raw-unit train/development generalization gaps,
with source-valid counts and price/activity groups. Training loss alone does not
distinguish overfit from objective/readout tradeoffs. Avoid another arbitrary
LR/weight sweep or capacity change without that evidence.

Control SHA`b205f29c78e83c4d0a79f575db411f5c17b5d938bbf65c5926e7add1c9f27a98`;
candidate SHA`824f1341db42e46324b9543ecbaa2609d1687a05f48d888d61e3277e6ec4c351`.
