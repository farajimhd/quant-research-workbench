# Identity-initialized temporal depth ablation

Hypothesis: more nonlinear processing before the fixed512D bottleneck can
improve simultaneous scale/shape retention more than the modest nonlinear
decoder gain. This is an encoder-capacity test, not a loss/LR sweep or a repeat
of failed decoder-visible-only/joint-separated gradient routing.

Control `joint_basis_prefloor_slow_temporal`; candidate
`joint_basis_prefloor_temporal_extra`. Fresh retained body-direction parent,
same data, masks, seed559, barLR3e-8/temporalLR1e-7/decoderLR1e-6 and objectives.
Append one128-wide four-head attention block after the existing final temporal
normalization and before token alignment/readout. Attention output projection
and FFN output projection start atzero, giving exact identity at initialization.
All132480 added encoder parameters are trainable; output projections receive
gradients immediately. No added input, target reference or decoder path. Single
affine590976-parameter decoder and512D exported embedding are unchanged.

Plan3000updates per arm, matched profiled batch, validation500, tiny fit and
16/64 graph-gradient/native parity gates. Existing50-minute loop deadline plus
evaluation reserve applies. Twelve focused tests passed after a local editing
error in the protected-row verifier was caught and corrected before deployment.
Tests cover initial outputs/input gradients, unchanged decoder, learnable new
projections and exact trained checkpoint restore. Contract explicitly records
the extra block without changing legacy configs; restores attach it before
strict state loading. Full runner/export must support this contract explicitly
before any promotion.

Compare216raw groups, all target range bins, cheap prices, activity and bar
retention against control and parent; flag>5%raw/>1ppaccuracy. Any finalist
needs frozen-embedding evidence before claiming better downstream information.
No full-run launch based solely on reduced training loss or larger capacity.

## Completed result (2026-11-22)

Sourcec59d1be35, runtime `phase1-v21-temporal-depth-20261122`, W&B control
xc24lhp3/candidate2p963rv3. Both3000updates,batch4096,seven validations; tiny fit,
native parity and16/64 graph gradients pass. Control180.4s/77037cached windows/s;
candidate185.7s/71957/s (~6.6% lower throughput). Matched control reproduces the
previous4096 run. Candidate111/216raw groups improve versus control, no>5%raw
or>1ppaggregateaccuracy regressions; worst raw+2.84%, accuracy-0.324pp.

Against parent175/216raw groups improve, but masked dailyvolume+7.81% and ten
range-accuracy groups regress>1pp (worst masked dailyask-3.368pp). Visible/masked
1s close MAE0.20668/0.18987 dollars. Daily volume visible739371 versus parent
729291. Masked1m ask target range0.1-0.25% accuracy31.382% to24.767% across
13546cells. Added attention did not resolve simultaneous retention in this test.

Actual saved checkpoint strict-restores exactly on CPU. Added132480parameters
are present and learned: formerly zero attention-output weight norm0.001188,
FFN-output norm0.003268. Decoder remains590976parameters. Those small norms
do not prove the added capacity was adequately optimized; this is a bounded
3000-step test at temporalLR1e-7, not a general rejection of deeper encoders.
A separate LR for newly initialized parameters is a possible next hypothesis,
with pretrained rates unchanged; it is not yet tested or shown to work.

Reject full promotion; retain checkpoint
`32efe87b76fb6c81d64335a0b13753206082d349ec5144eded64c9ca33a9e88d`.
Both remote W&B runs finished and final close metrics read back. NoV21 process
remains, panel Ready. Price-band/frozen-probe finalist evaluation was not run
because the existing range/activity safeguards already fail. No downstream
improvement claim. Original source data, checkpoints and STOP markers preserved.
