# V18 corrective qualification for the authorized20-epoch full run

V17's eight-arm campaign completed. Full validation improved98/108 all-visible
raw groups, but the price-preserving candidate retained a masked daily ask-count
MAE of3.55e9 and maximum error9.59e16. It is not promoted. User subsequently
authorized20 full epochs once a supported candidate is found. This follow-up is
bounded to two matched15000-update arms, each under60minutes, before that launch.

Both arms retain the exact V17 architecture, frozen16D Phase0 teacher,256D learned
embedding, decoder, dataset identities, calibration and16/64 visibility schedule.
No input data, references, directions or summaries are appended to embeddings.
Both start from V17's price-preserving checkpoint, with fresh AdamW1e-5 and seed559.

The corrective joint objective retains body coefficient0.5 and price0.01. It
supervises activity log levels on visible AND hidden source-valid bars (weights1
and0.1), while activity-change supervision uses visible adjacent pairs only.
Activity level loss uses pre-clamp coordinates, so negative coordinates retain
correction gradients; inference outputs and existing Phase0 constraints are
unchanged. A0.01 fourth-power log-error term penalizes large errors without
clipping valid targets or imposing an output ceiling. These changes jointly
address the confirmed hidden-scale supervision gap and exponential error tails.

`joint_calibrated` differs only by dividing change errors by a training-only
RMS log-change per timeframe/channel. Statistics are fitted to the frozen
training subset only, recorded in each manifest, and never supplied at inference.
Constant groups use scale1, with explicit counts. Training up/down directions are
approximately balanced; quote-size changes have lower mean-square log magnitude
and more flat pairs than volume, so equal log-MSE does not equalize these tasks.
That is motivation for this ablation, not proof of the remaining defect's cause.

Selection requires raw scale/tail improvement and retention of both price and
activity information. Keep all-visible and masked metrics separate. Full training
remains conditional on qualification, with online W&B and the workstation panel.

## Bounded results and full-run engineering

Both15000-update arms completed. On the fixed development subset, `joint`
achieved1s candle/volume/bid-size moving direction88.06/73.15/53.79 percent;
`joint_calibrated` achieved88.19/70.74/55.03 percent. Their1s close MAE was
$0.4047/$0.3827. Masked daily ask-count MAE was83385/74128. These are development
results, not full-population qualification or forecasting evidence. The complete
group comparison rejects the calibrated arm: its masked daily bid-size MAE is
6.40 times the V17 parent, versus1.08 times for `joint`. The partially started
calibrated full evaluation was stopped and preserved, not reported as complete.
`joint` proceeds to full qualification:90/108 masked and94/108 all-visible raw
groups improved; its worst group regressions are8.4% masked and6.5% all-visible.
Masked activity tails remain a weakness requiring full-population measurement.

The full runner uses the original186575722 training windows and47750007 validation
windows per epoch, with the same Phase0 split. It retains the256D bottleneck and
linear263168-parameter decoder (2640896 trainable parameters total), frozen16D
Phase0 teacher, and16-random/64-visible mixture. No fields are appended to the
embedding. The qualified checkpoint initializes a new20-epoch experiment with
fresh AdamW, constant1e-5 learning rate and weight decay0.01. Calibration is frozen.

`run --mode profile` measures the complete streaming/teacher/backward/optimizer
pipeline, including CPU loading, and checks eager/captured loss and every gradient
for both visibility geometries. Batch/worker choices come from these measurements.
Training uses bounded prefetch, two reusable pinned staging buffers, and CUDA
graphs. Full validation captures inference and all metric accumulators after
parity checks. An incomplete evaluation is never treated as full validation.

`run --mode train` requires a checkpoint-hash-matched qualification receipt plus
complete finite full-validation results. It retains rolling last/best checkpoints,
optimizer, RNG, scheduler progress and exact ordered traversal cursor. Engineering
resume preserves the scientific contract and W&B identity. Full validation runs
after every epoch; fixed development validation runs every2000 updates by default.
The first pass must improve the balanced quality score by0.5%; major raw-group
regressions or repeated development degradation checkpoint and stop the run.
The fixed score balances visible raw errors50%, masked raw errors25%, and observed
price/activity direction error25%. Error ratios use log1p to balance native units;
all individual groups and worst regressions remain available, not just this score.

W&B project for the full run is `mehdifaraji/bargpt-phase1-v18-full20`.
`launch` is a finite process controller, not a scheduled automation. `panel` is a
read-only Rich viewer showing train/validation speed and ETA, latest quality,
recent loader wait, GPU telemetry, staleness and terminal state.

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
# Run from the committed, hash-verified workstation snapshot.
python -B -m research.bar_gpt.phase1_v18.run --mode profile --checkpoint CANDIDATE --output PROFILE_RUNTIME --source-revision SOURCE_SHA
python -B -m research.bar_gpt.phase1_v18.launch --mode train --checkpoint CANDIDATE --qualification RECEIPT_JSON --full-reference FULL_VALIDATION_RESULT_JSON --output FULL_RUNTIME --source-revision SOURCE_SHA --epochs 20 --batch-size MEASURED_BATCH --workers MEASURED_WORKERS
# Resume with identical arguments plus --resume FULL_RUNTIME/checkpoint_last.pt.
```

## Full-population qualification and selected launch

`joint` completed all 47,750,007 validation windows in 839.50 seconds
(56,879 windows/s), with no nonfinite reconstruction. Compared with the V17
price-preserving candidate, 96/108 all-visible and 95/108 masked raw-error groups
improved. The geometric raw-error ratios were 0.840 and 0.219 respectively.
Worst regressions were 5.85% for all-visible 1h ask-low and 62.21% for masked
100ms ask-size. Full-validation 1s close MAE fell from $0.9464 to $0.7419;
candle moving direction was 87.30% and volume moving direction 73.65%.
Masked daily ask-count MAE fell from 3.55e9 to 116613. Large masked activity
outliers remain (daily bid-size maximum absolute error 7.26e12).

Frozen 1s development probes, using only exported embeddings, improved volume
direction from V16's 51.20% to 71.85%, and trade-count direction from 51.31%
to 76.88%. The untrained encoder scored 65.93% and 68.01%. Quote-size probes
remain weak: bid/ask 52.28%/51.85%, below the untrained 60.30%/61.11%.
These are same-window retention probes, not future forecasting results.
Exact-flat quote-size recall is almost zero; full three-way bid-size accuracy
is 30.46%, below the always-unchanged baseline's 43.21%. Moving-only direction
must not be described as overall accuracy or complete repair of the embeddings.

Five complete-pipeline profiles tested batches 4096/8192/16384/32768 and
16/32 workers. All passed eager/captured loss and gradient checks. Larger
configurations were slower. Sustained checks over 2.10M and 4.19M windows selected
batch8192/workers16: 98756 windows/s, 0.195 seconds loader wait in 42.47 seconds,
28.20 seconds startup, and 8166 MiB peak allocated GPU memory. The first 50 native
updates reached 98378 windows/s and saved a verified checkpoint for same-run resume.

Pinned training source: `73c165cef4c5d56255e7c92aeeea96650277392c`.
Parent checkpoint: `phase1-v18-joint-20261121/joint/checkpoint.pt`, SHA256
`a6ab30c77d312030cd8b8697c558735c8fbb1a1b3487e16f4a59a7131c837883`.
Encoder-only export: same directory `encoder.pt`, SHA256
`062bb35b6f10dcdb240d315e4574473d2c7536d79d8aa93a25b0ccfee05d5d28`;
exact CPU export parity was checked at both 16 and 64 visible positions.
Runtime: `D:/TradingML/runtimes/bar_gpt/phase1-v18-joint-full20-20261121`.
W&B: https://wandb.ai/mehdifaraji/bargpt-phase1-v18-full20/runs/uig4mxue .
The launch receipts contain the exact command. This is an authorized experimental
20-epoch run with quality guards, not a claim that 20 epochs are complete.
