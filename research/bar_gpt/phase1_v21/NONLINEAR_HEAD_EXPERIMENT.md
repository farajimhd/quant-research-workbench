# Small nonlinear decoder capacity ablation

Range-bin reweighting improved large ranges while harming tiny ranges and daily
activity. Prior decoder-visible-only and joint-separated gradient-routing tests
failed; this tests forward expressivity instead of routing or another loss sweep.
Hypothesis: a small nonlinear embedding readout can separate reconstruction
regimes that one affine map cannot, without additional information or a large
decoder. It does not assume that decoder capacity is the cause.

Control `joint_basis_prefloor_slow_temporal`; candidate
`joint_basis_prefloor_nonlinear32`. Unchanged encoders,512D embedding, loss,
seed559, data, masks, barLR3e-8/temporalLR1e-7/decoderLR1e-6. Fresh initialization
from retained body-direction checkpoint. Candidate single head computes existing
affine output plus Linear512to32, GELU, Linear32to1152 correction. Both terms
receive only the exported embedding. Final correction weights/bias start at0,
preserving initial output and encoder gradients exactly. Added54,432 parameters
versus590,976 affine decoder (~9.2% decoder growth); no input bypass or extra
decoder. Fixed DCT activity parameterization remains in the affine component.

One bounded capacity experiment, not an open-ended decoder expansion. Plan3000
updates per arm, validation500, tiny-fit/native-parity/CUDA-graph gradient gates,
matched profiled batch and existing50-minute deadline plus evaluation reserve.
Twelve focused tests pass, including identical initial function/encoder gradient,
learnable correction, parameter count and exact trained checkpoint restore.
New explicit decoder schema prevents legacy restore from interpreting these
weights as a native affine head. Encoder export adaptation, if promoted, must
preserve the unchanged512D contract and never include decoder weights.

Evaluate216raw groups, range bins including tiny/zero, activity and cheap-price
bands against control and parent; flag>5%raw or>1ppaccuracy regressions. Even
better reconstruction would not prove better forecasting or representation:
frozen probes remain necessary before any full-run recommendation based on a
larger decoder. Do not deploy this full-run without qualification.

## Completed paired result (2026-11-22)

Source d7d9cba20, runtime `phase1-v21-nonlinear32-20261122`, W&B controlm13phxs2
and candidateq2uu31sm. Both3000updates with seven validations; all tiny-fit,
initial-output and16/64 graph-gradient gates pass. Profiling selected4096;
comparison is within this pair, not an equal-sample comparison to prior8192
runs. Control180.7s/76,836cached windows/s; candidate174.7s/76,864/s. Added
54,432parameters have no measurable throughput penalty in this bounded test.

Candidate improves127/216raw groups versus matched control with no>5%raw or
>1ppaggregateaccuracy regression. Worst raw+2.52% masked10s ask-low; worst
accuracy-0.345pp. Visible1s close MAE0.20667 versus control0.20878 dollars.
Against parent171/216groups improve, but masked dailyvolume+7.28% and ten
aggregate range-accuracy regressions>1pp remain; worst masked dailyask-3.221pp.
Per-bin masked1m ask0.1-0.25% accuracy31.382% to24.967% (13546cells).
The nonlinear head helps modestly but does not resolve the range/activity
tradeoff. Do not infer that increasing decoder capacity further will solve it.

Actual saved-checkpoint restore and frozen train/dev audit completed20.62s:
`phase1-v21-nonlinear32-audit-20261122`, W&Ba4xu4pdb. Train185/dev171raw groups
improve,29worsenboth/16devonly. Masked dailyvolume+7.34% on train confirms
that defect is not validation-only. Dev below$0.10 close mean relative error
2.809% to2.997%, median2.465% to2.729%, P905.241% to5.468%; $0.10-1 mean
2.520% to2.421%. No embedding dimensions collapse or zero/tiny close denominators.

Retain candidate SHA
`540b678abe54ba3b95f992beb703a6bcf4a5d5cd24baa8a63404d8ec0c328784`
as a research improvement over its matched control, not a full20 candidate.
Both training runs and audit remotely finished; final training close metrics
read back. All V21 processes exited, panel task Ready. Original checkpoints
and STOPs unchanged. No new downstream-probe claim is made because the
reconstruction safeguards already prevent full promotion.
