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
Initial plan: four training runs. Expanded explicitly to six after a matched CPU
one-step diagnostic found roughly 2x/3x larger functional Adam updates in the
widened models. Follow-ups divide temporal learning rates by width/128 while
keeping feature and decoder rates unchanged. They start from the same V22
checkpoint under new identities, not from the unstable larger runs. No full
training automatically follows.

## Reproduction

Training source: `4aa10499bec4cfd993bb8bab79336ad9b52961b9`. Use its verified
workstation snapshot, the existing certified runtime dependencies, and a fresh
output directory. This repeats four bounded experiments, not full-data training:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
Set-Location D:\TradingML\codes\bargpt-phase1-v23-4aa10499b
& C:\Users\Mehdi\miniconda3\envs\ml4t\python.exe -B -m research.bar_gpt.phase1_v23.campaign --output D:\TradingML\runtimes\bar_gpt\phase1-v23-large-reproduction --revision 4aa10499bec4cfd993bb8bab79336ad9b52961b9 --windows 8388608
```

The manifest verifies the V8 subset and V19 training supplement, original split,
V22 starting checkpoint and Phase 0 anchor checkpoint. Uniform sampling with
replacement means 8,388,608 exposures, not that many independent windows.
Calibration is inherited from training only; no sampling weights are used.

Run `python -B -m research.bar_gpt.phase1_v23.compare --output <runtime>` and
`python -B -m research.bar_gpt.phase1_v23.checkpoint_audit --output <runtime>`
from the laptop source to regenerate the comparison, worst groups, learning
curves and evidence of updates in added layers. Set `PYTHONDONTWRITEBYTECODE=1`
for these commands too. Reporting code may be newer than the training snapshot.
`model.restore(torch.load(checkpoint, map_location='cpu', weights_only=False))`
loads a trusted local V23 checkpoint, including its nested architecture template.

`--width-scaled-followup` runs only the two larger configurations, with the
adjusted optimizer, in a NEW runtime directory and from its own pinned source
revision. Do not pass the original training SHA for a changed implementation.
The original snapshot and four original runs remain unchanged. The diagnostic
is reproducible with `python -B -m research.bar_gpt.phase1_v23.step_audit --output
<original-runtime>`; it uses laptop CPU and 64 fixed training windows only.
