# V24: 50M–500M temporal capacity

Five bounded runs: approximately 492M (first for budget profiling), 50M, 101M,
199M, and a matched 7.8M continued control. All start from V23 e03, the range-
leading 384x6 checkpoint. Feature encoder, fixed training-only transformations,
64x32 exported representation, 594-parameter shared decoder, masks and V21
joint_repaired objective are unchanged. No reference or additional decoder input.

Same immutable 114,599-window training pool and 11,146-window development subset
as V23. Each model has three 64-update training-only LR pilots (0.3/1/3 times
base rates), reset between pilots. Choose the lowest fixed training-probe loss
among pilots with finite gradients, loss within 1.1x starting loss, and price-log
MAE within 1.25x its starting value. Development is never used for LR selection.
These pilots are a bounded calibration, not proof of optimal LR.

Base rates: feature 3e-7; inherited/additional temporal blocks 2e-6*(384/width);
query/readout 1e-5*(384/width); decoder 1e-5. The chosen multiplier applies to all.
AdamW, no weight decay, unit global gradient clipping. Fixed selected rates for
the bounded run. Tiny-fit, pilots and profiles are discarded before training.

Profile batch128/256; common batch256 preserves matched optimizer updates.
Largest arm first fixes a common exposure up to 1,048,576 windows, reduced in
262,144-window increments only if its measured throughput requires it. Every
arm gets the same count, seed559, and five full development evaluations. All
training samples and masks are generated deterministically. 16 visible bars
on 3/4 updates and all64 on the fourth, as in V23.

Each arm has a strict 3,500-second deadline with 120 seconds reserved; the total
campaign is bounded to eight hours. No scheduled automation or full-data run.
W&B project: mehdifaraji/bargpt-phase01-v24-xl-capacity. No checkpoint uploads.
Checkpoint-last/best, optimizer, RNG, traversal, source/data identity, LR pilots,
profiles, metrics and conclusions stay under workstation runtime directories.

Use the pushed immutable deployment on the workstation, set
PYTHONDONTWRITEBYTECODE=1, and run:

```powershell
& C:\Users\Mehdi\miniconda3\envs\ml4t\python.exe -B -u -m research.bar_gpt.phase1_v24.campaign --output D:\TradingML\runtimes\bar_gpt\phase1-v24-xl-20261122 --revision <full-deployed-revision>
```

The CLI verifies deployment hashes, workstation ownership, STOP markers, disk,
RAM and all data/checkpoint provenance before claiming the GPU. A read-only
Rich view is available via `python -B -m research.bar_gpt.phase1_v24.panel
--output D:\TradingML\runtimes\bar_gpt\phase1-v24-xl-20261122`.

Interpretation: warm-start temporal expansion only. Native mixed-precision
starting differences are measured explicitly, not assumed equivalent to the
FP32 function-preserving expansion. Added layers must learn; decoder capacity
cannot hide missing embedding information. Report range accuracy together with
all-channel raw MAE, directional accuracy, price bands and worst regressions.
