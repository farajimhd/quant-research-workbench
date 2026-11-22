# V24: 50M–2B temporal capacity

Initial five bounded runs: approximately 492M (first for budget profiling), 50M, 101M,
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

After the user-requested OS maintenance pause, the user explicitly authorized
one additional extreme arm: approximately **2 billion parameters**, not 2GB of
weights. `--extreme-2b` runs width4608/depth11 (head dimension32), retaining the
same feature encoder, 64x32 slots and 594-parameter decoder. This is a ninth,
separately authorized experiment. The 60-minute bound still applies. Profiled
throughput may reduce its exposure in 262,144-window increments; use
`matched_compare --seen COUNT` against saved smaller-model evaluations at the
same exposure count. Do not compare unequal endpoints as a matched experiment.
All transformations, objective, masks, data, parent and LR-pilot protocol stay
the same. Before launch, checkpoint audits found inherited replicated Q groups
remained identical despite real optimizer updates. The extreme therefore adds
zero-sum noise (10% of each matrix RMS) to every inherited attention/FFN output
projection. A real-window CPU probe preserved outputs within 2.4e-6 and changed
first-layer Q-gradient replica divergence from zero to 2.2%. This initialization
change is explicit: the extreme is a combined size/initialization experiment,
not a pure size ablation. Run `qualify_extreme` on CPU first.

The first extreme qualification exposed WDDM paging: CUDA graph buffers filled
dedicated memory and consumed approximately40GiB shared GPU memory. It exited
its own throughput/deadline guard before any main training update. The same
W&B identity was retried with eager backward; model, optimizer, batch, loss and
sampling are unchanged. Eager profiles measured237 windows/s at batch128 and
260 at batch256, with approximately66GiB peak allocated memory at256. Live
training reached265 windows/s with shared GPU memory reduced to123MiB.
`retry_qualification` preserves the old attempt, refuses any already-trained
checkpoint, checks the scientific-source hashes, and charges repair downtime
against the original experiment deadline. The repaired source is pinned below.

Reproduce the fixed-exposure extreme run from its immutable snapshot:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
Set-Location D:\TradingML\codes\bargpt-phase1-v24-654c9d790
& C:\Users\Mehdi\miniconda3\envs\ml4t\python.exe -B -u -m research.bar_gpt.phase1_v24.campaign --output D:\TradingML\runtimes\bar_gpt\phase1-v24-extreme2b-reproduction --revision 654c9d79011748230713c6578c40243804199fcf --extreme-2b --windows 262144
```

The campaign was explicitly expanded to eight total experiments after the
initial results: all higher LR candidates passed the stability gates, but the
short pilots selected 0.3x for 100M/200M versus 3x for the range-leading 50M.
`--higher-lr-followup` runs 100M, 200M and 500M from the SAME original parent at
3x, under new identities, in a separate runtime root. It reruns the stability
gates and refuses a failed candidate. All data, masks, update counts, decoder,
objective and initialization stay matched. Stop after these eight experiments.

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
$env:PYTHONDONTWRITEBYTECODE='1'
Set-Location D:\TradingML\codes\bargpt-phase1-v24-50689d590
& C:\Users\Mehdi\miniconda3\envs\ml4t\python.exe -B -u -m research.bar_gpt.phase1_v24.campaign --output D:\TradingML\runtimes\bar_gpt\phase1-v24-xl-reproduction --revision 50689d590e3e45c44d49d00fd390b8efa7af28b6
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

The training snapshot is pinned above. Read-only `compare`, `checkpoint_audit`,
`structure_audit` and `plot` commands were added afterward; use the documented
audit revision or current laptop source for reports, without altering the
training snapshot. All generated reports and figures must use runtime outputs.
