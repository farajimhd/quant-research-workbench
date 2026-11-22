# V23 larger temporal encoder campaign

New bounded follow-up to V22:128wide x2blocks control,256x4,384x6; repeat the best
configuration with another sampling seed. Feature encoder remains width16,
four heads,16D output. Every arm exports64x32; decoder is the SAME learned shared
Linear32->18 with594 parameters. No target reference or extra decoder input.

Start every arm from V22 e07 checkpoint, hash verified against its result receipt.
Widen trained temporal attention by replicated heads preserving head dimension32;
add identity-initialized residual blocks before final normalization. Readout
zero-sum perturbations break head symmetry without changing the initial FP32
function. Verify FP32 parity; quantify BF16 initial evaluation differences.
Function equivalence is not optimizer-step equivalence: replicated parameter
coordinates and Adam updates differ. This is a warm-start capacity comparison,
not proof of asymptotic capacity or a from-scratch architecture comparison.

Use same frozen114,599 training /11,146 development windows, calibration, masks,
V21 joint_repaired/prefloor objective and V22 LR groups (bar3e-7, temporal
stem/blocks1e-5, query/readout/decoder3e-4), fresh optimizer per arm. Profile
complete graph-qualified updates, then use a common batch4096 for equal samples
AND optimizer steps.8,388,608 windows /2048steps per arm, five development points.
Each arm has a60minute wall limit including profile/evaluation; reserve evaluation
time. No checkpoints uploaded to W&B. Last/best remain under runtime roots.

Selection reports raw errors across all18channels/sixframes and moving candle,
activity balanced direction, range, low-price bands and speed. V22 failures in
low-price scale/range are explicit promotion vetoes, not hidden by aggregate loss.
At most four training runs; no full training automatically follows.
