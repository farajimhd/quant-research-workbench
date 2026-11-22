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
