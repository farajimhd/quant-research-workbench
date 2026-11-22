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
not use the audit for training or selection. A preselected finalist and the
assembly control are subsequently evaluated once with `final_audit.py`. Uniform sampling with replacement, unchanged
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

## Observed outcome, 2026-11-22

Training revision: `aaf589aaa5b22c8baf058281c135f7ec72f66d78`.
The six conditions took14.4 minutes summed experiment time; the longest took
3.81 minutes. Cache preparation took8.8 seconds. There were no training crashes.
Gradual unfreezing stopped at2048 updates because1h trade-count MAE rose50.57%;
the other four training arms completed4096 updates each. Assembly had no updates.

The development-selected candidate is **e03-independent-s559**, update2048:
10,523,256 parameters,64x64 exported slots,792 stored decoder parameters.
Development candle direction89.748%, range-bin accuracy36.129%, activity
balanced direction68.538%, moving activity direction99.723%, exact-flat activity
recall6.169%. Raw price/activity MAE geometric means improved2.21%/4.03% over
assembly, with worst-group regressions7.87%/21.37%. The second seed supported
the independent approach. Fusion was not the balanced-error winner.

The preselected candidate's8258-window audit **did not show uniform improvement**:
activity raw MAE improved2.85%, price raw MAE worsened1.58%, and1s trade-close MAE
rose from$0.37556 to$0.40099. Candle direction was89.296% versus89.237% for the
assembly. Audit sub-$0.10 median relative error remained315.16%, versus325.21%
for assembly, across82208 valid price-feature observations. This is not a zero
denominator artifact: nonpositive and below1e-8 price denominator counts were0.
All retained development/audit structure and nonfinite counters were0, and no
embedding coordinates were constant. Hidden8/hidden48 candle direction remains
near52%/51%, so high all-visible direction is not evidence of forecasting skill.

**Recommendation:** retain the independent architecture and both baseline/finalist
checkpoints, but address low-price absolute scale recovery before full training.
This campaign establishes preservation of price/activity strengths, not sufficient
all-scale reconstruction or improved future prediction. No full run was launched.

Pinned checkpoint under workstation runtime root:
`phase1-v26-preserve-20261122/e03-independent-s559/checkpoint_best.pt`.
SHA256: `4200b11d6330db1c3556ee9f3cfb594d3196819f1978cd00cc5785f5260a146b`.
Restore with `phase1_v26.model.restore(torch.load(path, map_location='cpu',
weights_only=False))`; `model.encode(x, valid, positions)` exports the slots and
`model.decode(slots)` reconstructs without raw inputs or target references.

Exact native resume audit at2048→2050 reproduced model parameters, optimizer,
losses, global RNG and traversal RNG with zero differences. All six W&B runs
have the expected validation history points, and contain no model/checkpoint
uploads. W&B automatically creates Parquet history artifacts; these are logs,
not model weights. The workstation training and monitoring processes exited.

Runtime evidence: `phase1-v26-preserve-20261122/comparison-report.md`,
`retained-comparison.json`, `wandb-readback.json`, `independent-activity-parity.json`,
the per-arm curves/profiles/checkpoints, plus sibling
`phase1-v26-final-audit-20261122` and `phase1-v26-resume-audit-20261122`.
Rebuild the report using `report.py --output <campaign> --audit <audit-directory>`.
