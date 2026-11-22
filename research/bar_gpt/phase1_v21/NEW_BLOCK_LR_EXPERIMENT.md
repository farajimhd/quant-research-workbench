# Learning rate for newly initialized attention parameters

The extra-block run produced small but nonzero output projection norms at1e-7.
Hypothesis: the pretrained encoder's protective learning rate undertrains new
parameters. Compare `joint_basis_prefloor_temporal_extra` against
`joint_basis_prefloor_temporal_extra_fast`: identical extra-block architecture,
initial tensors, parent, decoder, transforms, loss, masks, seed and data. Only
the132480 new block parameters use1e-5 instead of1e-7. Pretrained temporal
parameters stay1e-7, bar3e-8, decoder1e-6. No global LR change or loss reweighting.

Fresh optimizers and identities, not continuation of prior scientific trials.
Candidate uses four disjoint exhaustive optimizer groups, explicitly stored
new-block LR multiplier10relative to base1e-6. Restore preserves the contract.
Thirteen focused tests passed: exact initial tensors, group coverage/rates,
restore, forward/gradient parity and prior behavior. Floating-point LR products
are compared at1e-14relative tolerance; no scientific tolerance was relaxed.

Run3000updates per arm, same measured batch, periodic500validation, existing
tiny-fit/native/graph gates,50-minute loop deadline and evaluation reserve.
Compare all raw groups, range bins and activity against paired slow-new-block
control and retained parent. Flag>5%raw/>1ppaccuracy. Any apparent capacity gain
still requires price-band and frozen-encoder qualification; no automaticfull20.
This single new-parameter optimization test does not authorize an unbounded LR
sweep or progressively enlarging the decoder.
