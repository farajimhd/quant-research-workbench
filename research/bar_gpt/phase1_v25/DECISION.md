# V25 experiment findings

Eight approved experiments completed on DESKTOP-SAAI85T on 2026-11-22. Smaller
models were explicitly authorized: shared5.434M, standalone price2.743M and
activity2.735M, separate5.478M, fusion5.512M. The shared/separate parameter
difference is below1%. Joint exports64x64; each family exports64x32. Heads have
1170 shared or594 combined separate parameters; no input/reference bypass.

## Controlled comparison

All main runs use1,048,576 window exposures,4096updates,batch256, fresh seeded
weights and unchanged certified114599training/11146development windows. Seed559
is shared by the first seven;1559 repeats the provisional separate-encoder winner.
No pretrained model weights or teacher anchor are used. The frozen V24 training
calibration buffers are retained. Transforms use price log coordinates and fixed
training-reference activity inputs, with direct log1p activity outputs. No local
target references, clipping of valid data, or validation calibration.

Source: `20d4213a44822d04ecc3ab6bc56e1ffa2c0ca286`.
Data hash: `434661f361dbbc228f1bfd287455cb1ab71029c9c9e7d669788cfdbc959c2951`.
Supplement hash: `d9e8bd9a84efd4172caf747b55648b70692167ff4bc3b80a59a1b88ba167fbf3`.
Split hash: `d60f707c91c54b72ec7a11a9119334cc973351be699c8b6d5f3d724957640d63`.
Runtime manifest records calibration-source checkpoint SHA and buffer hash.

All qualified models chose peak LR1e-3, with5%warmup and cosine decay to1e-5.
Three96-update pilots were reset before training; selection used worst-family
relative training-probe loss, finite-gradient gates and family regression limits.
The shared mask arms reuse the same LR. Separate branches reuse standalone LRs,
with independent gradient clipping. Fusion has shared gradient clipping: its
comparison changes coupling/optimization as well as adds an encoder block.

Legacy masking means16visible on3/4updates and64visible on1/4:75%masking on masked
updates,56.25%average masking. Light means56visible on every update. None means64.
All evaluations separately cover all-visible,8-hidden and48-hidden bars.

| Run | Design | Close MAE dollars | Candle direction % | Range accuracy % | Activity balanced direction % | Windows/s |
|---|---|---:|---:|---:|---:|---:|
| e01 | Shared, legacy masks | .2003 | 46.70 | 13.36 | 48.19 | 22080 |
| e02 | Shared,8hidden | .2304 | 52.34 | 12.91 | 47.68 | 16489 |
| e03 | Shared, unmasked | .2012 | 52.39 | 13.55 | 48.96 | 15243 |
| e04 | Price only,8hidden | .2182 | 51.71 | 12.90 | N/A | 17750 |
| e05 | Activity only,8hidden | N/A | N/A | N/A | 68.84 | 19214 |
| e06 | Separate joint,8hidden | .2182 | 51.71 | 12.90 | 68.84 | 9741 |
| e07 | Separate plus fusion,8hidden | .2003 | 52.60 | 38.18 | 59.94 | 9545 |
| e08 | Separate joint, second seed | .2746 | 50.22 | 12.96 | 68.77 | 9773 |

Close MAE means1s trade-close, not an average of all channels/timeframes. Direction
and range use equal groups. Activity uses balanced down/flat/up recall. Full
channel/timeframe and price-band results are preserved, not replaced by this table.

## Conclusions

1. **Dedicated activity encoding is strongly supported.** Its all-visible raw-MAE
   geometric-mean ratio across all36activity groups is0.01862 versus shared-light;
   worst group ratio0.03623. Second seed gives0.01761 and0.03267 respectively.
   This is roughly98%lower geometric-mean raw MAE, not a claim about aggregate
   population dollar/quantity error. Prediction/target activity variance ratios
   are approximately0.960–1.056, compared with severe smoothing in the shared model.
2. **Joint independent branches reproduce standalone learning.** e06 reproduces
   e04price and e05activity endpoint metrics exactly. This confirms separation
   and independent clipping worked as intended. It is a consistency check,
   not another independent replication. e08 provides the second-seed evidence.
3. **The activity direction result needs class context.** e05/e06moving direction
   is99.7165%, down recall99.7178%, up99.7150%, but exact-flat recall7.0952%.
   Small numerical differences are classified as changes by the unchanged
   exact-sign metric. Mean group log1p MAE is0.00880. Do not silently substitute
   a tolerance or claim that all three direction classes are solved.
4. **Lower masking alone does not fix price shape.** Shared range remains near13%
   even unmasked. Heavier masking does improve heavy-occlusion robustness:
   hidden48close MAE is.2354legacy, .5593light,1.1336unmasked. Candle direction
   remains weak, including on the training sample. Thus overfitting or masking
   alone is not a sufficient explanation.
5. **Fusion is a promising range tradeoff, not a universal winner.** Range rises
   to38.1842% and close MAE is.2003, but activity log MAE rises to.32499 and moving
   direction drops to88.16%. Activity raw-MAE group ratio is.3650 versus shared
   light, much worse than independent branches. The extra block, normalization
   and shared gradients confound attribution specifically to price/activity
   information exchange. Fusion has only one seed in this campaign.
6. **Local gradients support an imbalance diagnosis.** A CPU FP32 diagnostic on
   64fixed training windows found price/activity shared-encoder gradient norm
   ratios73.2light and44.9unmasked. Cosines were.0149 and-.0285, respectively:
   strong scale dominance, not evidence of consistently opposing gradients.
   This post-training sample is diagnostic, not a population or causal estimate.

Retain e06/e08 as activity-preserving candidates and e07 as the range candidate.
There is no all-metric winner. All fresh candidates still have weak candle
direction (about50–53%); unlike prior pretrained V24 runs, they do not preserve
roughly90%candle direction. Fresh initialization, anchor removal, architecture,
representation size and optimization differ from V24, so this is not a clean
V24-to-V25 ablation. Sub-$0.10median relative error remains55.7%for fusion and
66.3%for e06. Neither is ready to claim accurate all-feature reconstruction.

Hidden activity balanced direction remains near33–34%, despite excellent
all-visible activity recovery. No future-task probe was run, and reconstruction
does not establish forecasting usefulness. No full-data training was launched.
Next decision: preserve the dedicated activity branch while diagnosing price
level versus shape gradients, and isolate fusion from extra normalization/block
capacity before scaling the model or declaring masking the main cause.

## Engineering and reproducibility

The initial source `2216b4df6feeb961d88c78b6eccd08157a9014c7` failed CUDA-graph
qualification before main update0: list indexing allocated CPU index tensors.
Repair registered device indices/constants and retained the W&B identity,
archived the failure and charged qualification/repair elapsed time. No trained
state was discarded or silently restarted. All eight final runs exited normally.

Local and native four-test suites passed before initial launch; four local tests
passed after repair. Every native profile/capture checked eager/captured loss
and gradients. Additional CPU checkpoint/optimizer continuation was bit-exact.
All final visibility audits have zero nonfinite/negative outputs, zero applicable
OHLC/quote violations and zero constant embedding coordinates. This is structural
validity, not proof of accurate shape.

Batch128/256/512 were profiled. Shared all-visible pipeline measured11.2K/15.9K/
17.9Kwindows/s. Batch256 was retained to hold4096updates and equal exposure across
arms; batch512 is a modest throughput option for a later run, not globally optimal.
GPU-cached data removes per-update disk loads. Full three-mode development
evaluation took1.76–3.14seconds. Actual training speeds are in the table.
Summed experiment durations were973.7seconds, including charged qualification
repair; longest231.5seconds. No experiment approached the60-minute limit.

Runtime root: `D:\TradingML\runtimes\bar_gpt\phase1-v25-family-mask-20261122`.
`REPORT.md`, `report.json`, `learning-curves.png`, `gradient-diagnosis.json`,
`comparison.json` and each arm's manifest/profile/pilots/tiny/curve/evaluation/
checkpoint files preserve the evidence. W&B project:
https://wandb.ai/mehdifaraji/bargpt-phase01-v25-family-mask . No checkpoint uploads.
The Rich panel was verified through an SSH terminal and in85-column redirected
rendering; no claim of a visible workstation desktop window is made.
Post-run reporting fixes mark zero-count inactive-family metrics N/A and retain
consistent plot colors. They do not alter the immutable training source.

Pinned last checkpoints under the runtime root:
- e06-separate-light-s559/checkpoint_last.pt:
  `d3bafd4e6ca2209623280711d1aed8dbc44366608bb51230be7e449e7b7f696f`.
- e07-fusion-light-s559/checkpoint_last.pt:
  `bc403a63422d3131ee72ca58581bf998af1eafab8334ad546c3711163c44ec11`.
- e08-separate-light-s1559/checkpoint_last.pt:
  `c197b1cd5ac7827235bc0fc96df1e74a9aec31ef61979ef14eaefcc1a810a9a1`.

README contains the complete pinned reproduction command. Do not overwrite
existing runs or remove human STOP markers. No old logs needed deletion.
