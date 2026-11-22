# V26: preserve pretrained price and activity paths

This bounded campaign assembles the compact V24 width384/depth6 price path and
the V25 dedicated activity encoder. The V24 path retains its original **all18
feature inputs**; it is not a price-only encoder. Each path exports64x32 slots.
The joint64x64 representation is the decoder's only input. Stored linear heads
have792 parameters: the original594-parameter price head is preserved exactly,
including six unused activity outputs, and a198-parameter activity head replaces
those outputs. There is no input/reference/metadata bypass to the decoder.

Six conditions: no-update assembly, activity-only continuation, independent
joint continuation, identity residual fusion with frozen branches, gradual
unfreezing, and a second seed of the strongest qualified continuation. The last
condition is skipped explicitly if no nonzero update qualifies. Each arm has
a strict60-minute ceiling, including profiling, qualification, and evaluation;
the campaign has an eight-hour ceiling. Quality failures stop early and are
reported with their actual exposure, never as complete matched-budget runs.

## Inputs, objectives and comparison limits

Parent frozen training-only transforms remain unchanged: price log-close,
log-relative body/wicks and activity log1p with fixed training references in
the input, fixed training normalization, and independently recoverable output
scale. Output activity uses log1p units. There are no true local references.
All18 original market features and existing9 time/validity metadata enter the
price interface; activity uses its own original family interface.

The objective uses V25 independently normalized family losses, including
price body/range/direction and activity level/change/direction terms, plus the
original V24 bar-latent teacher anchor with weight1. Removing unused price-path
activity supervision and splitting family reductions changes the V24 joint
objective; this is explicitly **not** a byte-identical continuation of V24.
The teacher is used only as a loss target, not as decoder input or exported
metadata. Direction of exact-flat activity pairs remains a strict metric;
no new tolerance or deadband is introduced to make accuracy look better.

Price observes16 random bars in three updates and64 in the fourth; activity
observes56. Fusion therefore can see activity on positions absent from the
price branch during training. Validation applies common64/56/16 visibility
to both branches and reports all-visible and hidden-only metrics separately.

Data reuses the immutable V8 experiment train/development split and V19
training supplement,114599 training and11146 development windows. It does
not touch the held-out audit. Uniform sampling with replacement, unchanged
training-only calibration, source masks, identities and hashes. Development
is used for retention and selection, so results are development evidence,
not independent audit or forecast-generalization evidence.

## Retention and execution

`gates.py` freezes retention rules before training: macro candle/range and
activity balanced direction cannot lose more than1pp; raw-price geometric
MAE cannot rise more than5%, worst group25%; activity15%/50%; candle group2pp;
price-band median10% plus0.05pp; activity moving direction1pp and flat recall2pp.
Only passing checkpoints can be recommended. Raw family ratios balance the
ranking; noisy activity is not required to reconstruct perfectly.

CPU real-row exact assembly and finite-gradient tests precede deployment.
Native CUDA assembly and eager/graph gradient parity precede GPU training.
Each training arm profiles128/256/512 complete updates and qualifies LR with
reset64-step pilots, then a96-step32-window fit check. Batch256 is fixed for
matched optimizer updates; all prepared inputs and teacher targets are GPU
cached, removing per-step disk/CPU-loader waits. Trials use conservative
pretrained learning rates and5% warmup/cosine decay. Price/activity/fusion
gradients are clipped independently. Validation occurs every256 updates.
Last and best-qualified checkpoints remain local, with optimizer, traversal
RNG, global RNG, scheduler position and stage state for engineering resumes.

Laptop source authority is `D:\TradingCodes\quant-research-workbench`.
Commit and push before deploying with `python -B -m
research.bar_gpt.phase1_v26.deploy --revision <pushed-sha>`. Set
`PYTHONDONTWRITEBYTECODE=1` for all repository Python executions. Workstation
snapshot authority is `D:\TradingML\codes`; runtime authority is
`D:\TradingML\runtimes\bar_gpt`. The preflight verifies source hashes and GPU
ownership, and respects STOP markers.

From the verified workstation snapshot:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
& C:\Users\Mehdi\miniconda3\envs\ml4t\python.exe -B -u -m research.bar_gpt.phase1_v26.train --revision <pushed-sha> --output D:\TradingML\runtimes\bar_gpt\phase1-v26-preserve-20261122
```

Append `--resume` only for unchanged source/data/science. W&B project is
`mehdifaraji/bargpt-phase01-v26-preserve`; log configuration and metrics only,
never checkpoints. `panel.py --output <runtime>` displays Rich status,
validation speed/ETA and quality. No scheduled automation or full training
is launched by this campaign.
