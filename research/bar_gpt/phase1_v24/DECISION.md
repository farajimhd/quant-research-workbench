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

All arms use seed559, batch256, 4,096 updates, and 1,048,576 sampled-window
exposures. Samples are drawn uniformly with replacement from 114,599 training
windows. The same 11,146 development windows are evaluated at five learning
points. There is no new held-out or forecasting evaluation. Six timeframes,
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
profiled; common batch256 preserves matched updates. GPU model was verified as
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
Training PID21832 and the panel exited; GPU utilization was 0%. The STOP marker
remains in force. Runtime `pause-receipt.json` records the exact state. No
automatic restart. Resume must preserve the same source/W&B identity and account
explicitly for user-requested maintenance downtime in the campaign wall budget.

Final comparison and recommendation will be recorded after the bounded
higher-LR follow-ups and structural audit finish. Initial five-arm evidence
already rules out a monotonic size improvement: continued 7.8M training reaches
$0.1944 1s close MAE versus $0.1850 for 199M, whereas 49.8M leads range at
39.52% and activity balanced direction at 51.02%. Do not attribute improvement
against the uncontinued parent solely to capacity.
