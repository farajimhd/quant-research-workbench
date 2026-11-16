# Phase-one V5: bar-token MAE with reference reconstruction

V5 is a fresh, separate experiment. Existing V4 and phase-two models, stopped
runs, dataset releases, reference policies and checkpoints are not changed.
User-authorized scope: implement, profile and optimize; full training is not
automatically started. Architecture and performance qualification are separate
from learned representation quality.

## Data and model

Use the exact repaired, balanced phase-one V4 cohort and its frozen chronological
80/20 split: `D:/TradingML/runtimes/bar_gpt/phase1-v6-cohort-balanced-v4/split.json`.
Its18 market fields, source-valid masks,64 ordered observed bars, common share
basis, six timeframes and nine UTC/timeframe fields are unchanged. No shards
are regenerated. Receipt/file SHA verification, split purge, worker ordering,
bounded prefetch, worker partial tails and deterministic epoch seeds are reused.

Reference construction and normalized targets use V4's shared Float64 median
authority: shared trade/bid/ask close median for prices and individual activity
medians. Prices become100*ln(P/reference), activity becomes ln1p(Q)-ln1p(reference).
References are computed before augmentation and are explicitly visible side
information, not a causal forecast target or an independently hidden reference
prediction task. Invalid activity references remain masked, not reported as zero.

Each bar is one token. During training, sample16 of64 bars independently per
example, retain their original0..63 position IDs, and physically gather only
those16 rows. Add5-10% optional market-feature dropout (default7.5%, configurable
0..10%): one independently drawn missing-column mask per example, shared across
its16 visible bars. This augmentation is marked unavailable rather than treated
as a genuine observed zero. It does not drop time/reference fields and does NOT
reduce attention length further. It is disabled in validation and extraction.

Each visible token combines27 values (18market+9time),27 missing indicators and
a learned original-bar position. A separate reference token projects seven
standardized log/log1p references plus availability flags. Encoder attention sees
17 tokens at training and65 at all-visible validation/extraction. There are no
hidden-bar placeholders or extra summary tokens. Default encoder:512 width,
six pre-LN blocks,eight heads of64,FF2048,GELU,zero residual/attention dropout.
Mean pooling and LayerNorm export one512-dimensional embedding.

The decoder receives ONLY the512 embedding and requested bar position IDs:
512->128 shared projection, learned position queries,LayerNorm/Linear128/GELU/
Linear18 per requested bar,plus a separate small128-wide reference MLP. Training
decodes48 masked bars; full validation decodes64. No visible-bar encoder states,
raw references, full target time arrays or raw bars bypass the bottleneck. No
V4 column projection or optimizer state is implicitly transferred.

The user subsequently approved profiling internal widths128 and256 as well.
These keep six layers/eight heads,FF4x internal width,and add a single pooled
projection plus LayerNorm to export512. This changes model capacity; a wider
export does not restore information already compressed internally. Default width
remains512. Select alternatives explicitly with `--encoder-width 128` or
`--encoder-width 256`. Compare them against the original512-wide V4,
not a silently narrowed V4 baseline. Representation quality remains unqualified.
The user also approved exported embedding width256. Select it with
`--embedding-width 256`; default remains512. The decoder's only input follows
that exported width. This does not change raw shards, masks or reference labels.
Existing phase-two512-wide teachers/students are not automatically replaced.
Absolute log-reference precision is retained in FP32 through the reference-token
projection,pooled export and reference decoder branch. Attention/FF and bar-head
work remains under BF16 autocast. Standardized log references rounded to BF16 can
impose percent-level raw-price error floors;this is different from ordinary
relative-price bar outputs. There is still no reference-to-decoder skip. This
precision fix is not a claim that early learning error is entirely quantization.

## Loss and validation

Freeze V5-specific statistics from a bounded, distributed sample of TRAINING
split rows only. Calibration records each file SHA and exact row indices and is
bound to the complete split hash. No validation refitting. References are
standardized in log/log1p space; market target values and encoder input formulas
remain V4-exact. Loss residuals are divided by per-channel training standard
deviations, with explicit0.01 floors to prevent infinite weights. SmoothL1(beta1)
is averaged by observed channel,then within price/activity/reference families;
the three family losses have equal weights. Bar loss uses only the48 masked
positions and source-valid labels. Dropped features of retained bars are not
additional supervised targets. References reconstruct through the bottleneck.

Validation evaluates BOTH a fixed-seed masked view (16+1tokens, no feature dropout)
and all-visible view (64+1tokens) of each source batch. Masked objective and the
three family components are separate from all-visible components. Full raw-unit
MAE for all18 channels/six frames, reference MAE, price-only MAPE/median/P90 and
price bands use inverse-transformed predictions with PREDICTED references. They
never substitute actual references into decoding. Raw metrics are diagnostic,
not gradients. Nonfinite inverse results are reported as failures/unknown errors,
not clipped predictions. Positive limits mark bounded training/validation smokes;
zero step limits mean complete dataset traversals, never validation-count epochs.

Calibration version3 mixes training-row-weighted source selection with rare-frame
coverage, requires all six frames,and balances up to256 windows per frame. This
fixes the early profiling-only shard-uniform sample,which overrepresented coarse
files and missed100ms. Every selected source payload is hashed; exact row IDs and
frame coverage remain in the seal. Older calibration files are not silently
migrated or reused for full training. No full V5 run has been launched.

## Execution, checkpoints and profiling

Runtime outputs belong under `D:/TradingML/runtimes/bar_gpt`. Launcher default is
PROFILE. Full training requires `--mode train --execute`; use a fresh output or
an explicit proof-verified resume. Human STOP files are never cleared. Defaults:
batch1024,BF16,8 CPU workers,2prefetch,16shard lookahead/64MiB per lane,one CPU
thread per worker,50full epochs,cosine3e-4->5e-5,1000initialwarmup,clip1,AdamW.01.
W&B training project is `bargpt-bar-mae-v5`, run name is the output directory name;
resume uses its original saved ID. Current/epoch cursor,model,AdamW,RNG,schedule
position,history and split/calibration/implementation contracts are preserved.
Keep only rolling last/best checkpoints,overwrite recovery every1000updates and
at validation boundaries. Profiles create no training checkpoints or W&B run.

```
python -B -m research.bar_gpt.phase1_v5.run --output D:/TradingML/runtimes/bar_gpt/v5-profile-NEW --mode profile --batches 256,1024,4096 --workers 8 --steps 24 --warmup 4 --source-revision COMMITTED_SHA
python -B -m research.bar_gpt.phase1_v5.panel --run D:/TradingML/runtimes/bar_gpt/v5-profile-NEW
```

The launcher sets PYTHONDONTWRITEBYTECODE. Native execution must use the required
environment and a committed,pushed,SHA-verified immutable source snapshot. GPU
profiles refuse an active BarGPT trainer; preserve64GiB RAM/150GiB free disk.
All preparation workers close at the end of every profile/evaluation.
CUDA capture initialization finishes before loader pinning threads start; pinned
host allocation may synchronize the device and must not run during capture.

Profile compute-only repeated real batches separately from actual-loader wall
time. Compare fresh V4/V5 models with matched512/6layer/zero-dropout dimensions,
their respective objectives and the same balanced source order. Narrow variants
explicitly change width/FF capacity; the V4 comparison stays512-wide. Optional CUDA
replay is accepted only after model/AdamW/CPU+CUDA RNG parity with eager updates.
Default deterministic algorithms and process-local `CUBLAS_WORKSPACE_CONFIG=:4096:8`
make repeated position-embedding gradient reductions reproducible. Profiling found
that ordinary eager repeats differed without this setting; enabling determinism
made the bounded eager/captured comparison exact. Capture requires this mode;
it does not weaken or silently replace parity checks. Performance must be measured
under the actual selected setting,not extrapolated from nondeterministic trials.
Attention-pair16x savings compare V5's64 versus16 BAR tokens. V4 instead uses
feature-column tokens (about10/11 during masked training,21 all-visible); V5 is
not automatically faster than V4,particularly all-visible inference. Report
measured rates,source/startup scope and limits rather than extrapolating pair counts.

## Read-only Rich terminal

The V5 viewer uses the accepted phase-two solid Unicode bars and compact metrics
tables with horizontal row separators. Training shows full epochs,current epoch
windows and validation windows; profiles show overall trials and current update
counts with explicit configuration labels. Unknown totals stay unknown. Metrics
separate price/activity/reference losses from raw-unit trade/bid/ask close MAE.
GPU capacity comes from the native run,not laptop hardware. Stale/failed states
are visible; short terminals prioritize health/progress/losses,not hidden scrolling.
Redirected output is a single snapshot,Ctrl+C closes only the viewer,captures are
runtime-only. No terminal starts/stops a model or clears STOP markers.
