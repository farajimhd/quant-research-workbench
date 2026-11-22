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

## Completed result (2026-11-22)

Source d7e8316c3; runtime `phase1-v21-new-block-lr-20261122`. Both3000updates,
batch4096, seven validations; tiny fit, native parity and16/64 graph gradients
passed. Control W&Btpswfo1s, candidatee7fg0348. Control192.4s/71755cached
windows/s; candidate188.1s/71833/s. Both remotely finished with final close
metrics read back. Control reproduces the preceding depth experiment.

Candidate improves90/216raw groups versus control, but six regress>5%; worst
masked dailyvolume+32.22%, visible dailyvolume+16.00%, masked quote-counts
about16.5%. No aggregate accuracy group loses>1pp versus control. Against
parent178/216raw groups improve, five regress>5%, six range-accuracy groups
lose>1pp. Masked dailyvolume+42.54%; dailyvolume visible857696 versus729291.
Visible1s close MAE0.20910 versus control0.20668; masked0.19474 versus0.18987.
Masked dailyask range accuracy73.353% versus parent75.830%; per-bin masked1m
ask0.1-0.25% accuracy31.382% to25.070%. Larger updates improve some ranges
but materially damage activity retention; reject full promotion.

The new block did learn substantially more: attention-output weight norm
0.10516 and FFN-output norm0.29340, versus0.001188/0.003268 in the slow-block
run. Actual checkpoint strict-restores exactly, including all four optimizer
group rates. This rejects the tested1e-5 new-block setting, not every deeper
encoder or learning-rate schedule. No further unstructured LR sweep is justified.

Retain checkpoint
`7ef43b2796599a56b55730bd31624a857dc37d63ca14f857e7b19f15dcf16475`.
No V21 process remains, panel Ready; no final price-band/probe audit was run
because raw activity/range safeguards already fail. Checkpoints, source data
and STOP markers remain preserved. No full20 run or downstream claim.
