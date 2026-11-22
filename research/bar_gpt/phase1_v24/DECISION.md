# V24 capacity experiment record

## Scope and comparison contract

Requested classes: 50M, 100M, 200M and 500M parameters. Exact sizes are
49,811,906 (768x10), 101,242,946 (1152x9), 198,705,602 (1536x10), and
492,176,066 (2688x8). The matched continued control is 7,788,482 (384x6).
All export 64x32 learned slots to the same shared 594-parameter linear decoder.
The decoder receives no reference, raw input, or auxiliary bypass.

Every arm starts from the same trained V23 e03 checkpoint, SHA256
`a6fd395beab1a4deaaebb53c54c4b29498180429fe26427825c71965d673f023`.
Temporal head replication and identity-initialized added blocks preserve the
initial FP32 function within measured maximum absolute error 4.8e-6. Native
mixed-precision initial evaluations are recorded separately. This is warm-start
width/depth expansion, not independent training from scratch. The feature
encoder remains unchanged; this does not test increasing feature capacity.

The first eight arms use seed 559, batch 256, 4,096 updates, and 1,048,576 sampled-window
exposures. Samples are drawn uniformly with replacement from 114,599 training
windows. The same 11,146 development windows are evaluated at five learning
points. The later 2B arm uses 1,024 updates; its matched comparison is below.
A post-selection audit is recorded below; there is no forecasting evaluation. Six timeframes,
source validity, transforms, activity calibration and objective are unchanged.
Training physically selects 16 bars on three updates out of four; every fourth
update is all-visible. Hidden-position and all-visible metrics remain separate.

Data manifest SHA256:
`434661f361dbbc228f1bfd287455cb1ab71029c9c9e7d669788cfdbc959c2951`.
Training supplement:
`d9e8bd9a84efd4172caf747b55648b70692167ff4bc3b80a59a1b88ba167fbf3`.
Original split:
`d60f707c91c54b72ec7a11a9119334cc973351be699c8b6d5f3d724957640d63`.
These hashes are checked at runtime; all generated evidence stays outside source.

## Learning-rate qualification

AdamW uses four groups: feature, temporal, query/readout, decoder. Base LRs are
`3e-7`, `2e-6*(384/width)`, `1e-5*(384/width)`, and `1e-5`.
Three reset 64-update pilots test multipliers 0.3, 1 and 3 using only training
examples. Finite-gradient and scale-error gates precede lowest pilot-loss
selection. These are short stability pilots, not evidence of an optimal LR.
All pilot updates are discarded before the matched main run.

The first five arms selected multipliers 3 (50M), 0.3 (100M/200M/control), and
1 (500M). Because all higher-rate candidates passed stability but the short
pilot could favor a different task tradeoff, three additional matched arms
test multiplier3 at 100M, 200M and 500M. They restart from the same V23 parent,
under new identities. Total: eight experiments; no further runs are authorized
automatically by this campaign.

## Engineering and provenance

Original training snapshot: `50689d590e3e45c44d49d00fd390b8efa7af28b6`.
Higher-LR training snapshot: `1307293bc5ea24e986b0bc5dcad674879d8ab310`.
The latter changes experiment selection and Windows desktop ownership
classification, not the scientific forward/backward path.

The first higher-LR launch failed before preparation/training: Windows WDDM
listed ApplicationFrameHost and SystemSettings as compute owners. Exact paths
and valid Microsoft Windows signatures were verified. V24's ownership guard
now recognizes those exact desktop executables; no unrelated process was
stopped. Failed-launch logs and the admission receipt were preserved. The retry
uses the existing empty runtime directory; no optimizer or scientific state
existed to resume.

Local and native focused tests passed. Actual parent/data FP32 expansion parity,
finite gradients, CUDA-graph/eager gradient parity, LR-group membership and
higher-LR eligibility guards were checked. Tiny-fit gates require a 10% loss
reduction; they are a learning sanity check, not near-perfect memorization.
All data is GPU cached, eliminating per-update disk reads. Batch128 and256 were
profiled; common batch 256 preserves matched updates. GPU model was verified as
RTX PRO6000 Blackwell Server Edition with approximately 96GiB, not the laptop.

W&B: https://wandb.ai/mehdifaraji/bargpt-phase01-v24-xl-capacity
Metrics/configuration only; checkpoints remain on the workstation. The Rich
panel was verified in an SSH terminal; a visible desktop window was not verified.

Runtime roots:
`D:\TradingML\runtimes\bar_gpt\phase1-v24-xl-20261122` and
`D:\TradingML\runtimes\bar_gpt\phase1-v24-xl-lr3-20261122`.
Detailed comparison, checkpoint audit, learning curves and W&B readback are
generated into the first root. Individual receipts retain all-channel and
price-band metrics, profiles, LR pilots, initialization and final checkpoints.

## Results

Campaign explicitly paused by the user for OS maintenance on 2026-11-22.
Six runs completed. The 100M higher-LR arm finished with close MAE $1.4356,
range accuracy 39.21%, candle direction 87.69% and activity balanced direction
50.51%; reject that LR for unstable price recovery. The 200M higher-LR arm was
checkpointed at step47 (12,032 exposures); 500M higher-LR has not started.
The checkpoint includes optimizer and RNG/traversal state; SHA256
`59fcf4d412c81a0052cdfdaeb3eb6ef775f66721159be5a1a10ebb99a6523a3b`.
Training PID21832 and the panel exited; GPU utilization was 0%. Runtime
`pause-receipt.json` records the exact state. The user subsequently authorized
resumption after the OS update. The checkpoint hash was reverified; the STOP
was archived as `STOP.os-maintenance.resolved-20261122`, not discarded. PID7284
resumed the same source, optimizer/RNG state and W&B identity. The original
campaign wall guard was retained because even including the approximately
12-minute maintenance pause it remained within budget. The 200M higher-LR run
then completed at step4096: close MAE $0.4787, range39.39%, candle90.57%,
activity50.94%. It does not outperform the faster 50M arm on these aggregates.

The user separately authorized one approximately 2B-parameter extreme, bringing
the bounded campaign to nine total experiments. Exact model size is
1,955,104,706 parameters (width4608/depth11), with the same 594-parameter decoder.
CPU qualification on real windows measured maximum FP32 expansion errors of
2.86e-6 (16 visible bars) and 3.81e-6 (64 visible bars). Source:
`aa7dcd044c7ae7f281a4cbc1d00a4da1e5940955`. The model is not yet claimed trained.

### Completed matched full-budget arms

All rows below have 1,048,576 exposures, batch 256 and 4,096 updates. Close MAE is
all-visible 1s trade-close in dollars. Candle direction excludes unchanged
candles; activity is balanced down/flat/up recall. Direction/range are equal
timeframe/stream group means. See runtime comparison JSON for all 18 channels,
both visibility modes and worst groups.

| Parameters | LR multiplier | Close MAE | Candle direction | Activity direction | Range accuracy | Cached windows/s |
|---:|---:|---:|---:|---:|---:|---:|
| 7.8M control | 0.3 | $0.1944 | 89.66% | 49.04% | 35.98% | 11,563 |
| 49.8M | 3 | $0.3881 | 90.58% | 51.02% | 39.52% | 4,456 |
| 101.2M | 0.3 | $0.1871 | 89.66% | 48.95% | 36.10% | 2,940 |
| 101.2M | 3 | $1.4356 | 87.69% | 50.51% | 39.21% | 2,938 |
| 198.7M | 0.3 | $0.1850 | 89.57% | 48.91% | 35.95% | 1,749 |
| 198.7M | 3 | $0.4787 | 90.57% | 50.94% | 39.39% | 1,745 |
| 492.2M | 1 | $0.1971 | 89.76% | 49.21% | 37.32% | 893 |
| 492.2M | 3 | $0.2659 | 90.59% | 50.49% | 39.14% | 897 |

The 50M model leads range and activity direction; the higher-LR 500M offers a
price/range tradeoff but is about 5x slower. The continued control explains much
of the price improvement relative to the uncontinued parent. The short LR
pilot was insufficient to guarantee later stability: the high-LR100M run had a
late price-error jump. No monotonic capacity advantage is established.

### Confirmed initialization limitation

The audited inherited Q-weight replica slices remained effectively identical
(relative RMS divergence 0 to 6.5e-8). This was not missing optimizer updates:
nearly all audited weights changed and Adam step counters reached 4,096. A real
CPU backward probe found identical original Q gradients. Distributing zero-sum
noise across inherited attention/FFN output projections preserved forward
outputs within 2.4e-6 but yielded 2.2% Q-gradient replica divergence. This is
implemented only in the new extreme arm. Its real GPU objective measured 1.96%
initial gradient divergence. Added random blocks in the older arms did learn;
the finding does not mean every parameter was frozen or all extra capacity was
unused. It limits what the older width-scaling results establish.

### Extreme engineering record

The first 2B graph qualification consumed approximately 40 GiB of shared GPU memory
and exited its own throughput/deadline guard at 473.6 seconds, before main update 0.
No force-stop was executed. Evidence and STOP were archived under the extreme
root's `qualification-attempts/c665810a3`. Retry source is
`654c9d79011748230713c6578c40243804199fcf`; W&B identity remains `1iaqyqt3`.
Only campaign execution and the guarded retry utility changed; scientific
module hashes were checked unchanged. The retry charges both prior compute and
repair downtime to the original deadline. Eager batch 256 profiles at 260
windows/s and 66 GiB peak allocated memory. Live speed was 265 windows/s with
123 MiB shared GPU memory, eliminating the paging bottleneck.

The extreme completed 262,144 exposures (1,024 updates), selected before
training from profiling, in 2,556.7 seconds including qualification and repair.
It has 1,955,104,706 parameters (width 4,608, 11 temporal blocks), with the same
64x32 export and 594-parameter decoder. Training throughput was 260 windows/s.
It changes initialization as well as size; this is not a pure capacity ablation.

### Final decision and matched extreme comparison

At 262,144 exposures, the 7.8M control versus 2B respectively achieved:
close MAE $0.2465 versus $0.2489; candle direction 89.00% versus 88.95%;
activity direction 48.83% versus 48.86%; range accuracy 33.70% versus 33.82%.
The 216 raw-error group geometric mean was 1.6% lower for 2B, but throughput
was about 44 times slower. This bounded result does not justify full 2B training.
It does not establish that a longer, differently initialized 2B run cannot help.
The final inherited Q replica divergence was 1.17e-5 to 7.47e-5, confirming
that symmetry was broken, although the divergence remains small.

Retain 50M as the practical range/activity candidate; retain the 500M high-LR
checkpoint as a slower price/range alternative. Neither dominates the small
control: 50M loses substantial price accuracy, and 500M costs about five times
more than 50M. Reject the unstable high-LR 100M arm. Do not launch a full-data
run from these findings automatically. Sub-$0.10 median percentage error still
exceeds 128% even for the best of these full-budget candidates. Masked candle
direction remains near 51%, and masked activity balanced direction near 33%.
No downstream forecasting improvement has been demonstrated.

All nine checkpoints passed the full 11,146-window structural audit in both
visibility modes: zero nonfinite, negative, OHLC-structure or quote-crossing
outputs, and zero constant embedding coordinates among 2,048 coordinates.
These structure checks do not imply accurate activity magnitudes; the saved
per-channel prediction/target variances expose substantial underdispersion.

Runtime evidence under `D:\TradingML\runtimes\bar_gpt`:
`phase1-v24-xl-20261122/MATCHED-262144.md`, `matched-262144.json`,
`checkpoint-audit.json`, `learning-curves.png`, and
`phase1-v24-final-audit-20261122/results.json`.
The 2B checkpoint is `phase1-v24-extreme2b-20261122/e09-w4608-d11-s559/checkpoint_last.pt`,
SHA256 `6ed7bc53b2f583a9d2c946e53cf6574f6e27dc4d1b9335e8b3f1804c5ceddd9c`.
The practical 50M checkpoint is `phase1-v24-xl-20261122/e02-w768-d10-s559/checkpoint_last.pt`,
SHA256 `c8e6f9aa0816c29de9da0198e7aeb6e45e190e16e6beb389528ca7f8eae90ba9`.
Use the pinned reproduction command in README; local checkpoints include
optimizer state, while W&B receives metrics only.

### Post-selection audit

Audit revision `f4c1398b64555e8c6acf4cad4e1d887b2c07a7ac` evaluated 8,258
frozen audit windows once, without fitting or calibration. This partition is
not a pristine external test: the manifest records prior full-validation
aggregate exposure. Results are in `phase1-v24-selection-audit-20261122`.

| Final checkpoint | Close MAE | Candle direction | Activity direction | Range accuracy |
|---|---:|---:|---:|---:|
| 7.8M control | $0.3756 | 89.24% | 49.33% | 36.49% |
| 50M | $0.5565 | 90.14% | 51.79% | 39.22% |
| 500M higher LR | $0.4388 | 90.08% | 51.08% | 39.07% |
| 2B, quarter exposure | $0.5400 | 88.58% | 49.34% | 34.78% |

The same practical tradeoff persists. These are final checkpoints with
different total exposure for 2B, not an exposure-matched capacity comparison.
The audit does not establish forecasting usefulness or statistical significance.
