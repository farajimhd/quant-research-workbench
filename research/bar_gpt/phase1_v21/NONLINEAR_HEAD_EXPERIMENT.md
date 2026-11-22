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
