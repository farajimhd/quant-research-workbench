# V7 full-data training

The selected geometry_joint encoder keeps width 2048, three residual blocks,
768 exported values, and one 768-to-1152 affine decoder. Training is all-visible,
with the selected equal-family log-coordinate objective. The user selected 50
epochs. A fresh seed-17 model is used; subset training is not resumed as full-data
training. All certified windows and worker tails are retained.

`full.py` implements precision qualification, compute profiling, real certified
stream profiling, and full training. `full_data.py` performs deterministic source
validation and feature construction in 256-row CPU chunks using the model's
shared preparation function. BF16, if qualified, affects only the dense backbone;
explicit scale slots, exported projection, decoder, and objective stay FP32.
Native inversion uses FP64. No fitted population calibration is required.

Source snapshots must include `research/__init__.py`, `research/bar_gpt`,
`research/mlops`, `pipelines/market_sip`, and `pipelines/certified_research`: the existing checkpoint integrity
utilities transitively import canonical pipeline modules. Importing these modules
does not read events or run a writer. Training reads certified shards only.
Commit and push before creating each immutable snapshot; pass its file-hash
deployment receipt to every invocation. Generated output belongs under runtime.

Full training retains rolling last/best checkpoints with optimizer, RNG,
epoch/batch traversal cursor, and scheduler position. Resume requires the same
contract and W&B identity. The ordered worker prefix is replayed without optimizer
updates. Human STOP files are never removed. Source geometry violations are fatal,
not filtered or clipped. Per-epoch validation must cover the full frozen partition.

Measured full-pipeline profiles (4,194,304 windows, excluding startup): CPU-built
98-wide inputs reached 58.7K windows/s with 32 workers (that first profile used
2,097,152 windows). Compact 27-wide transfer plus captured GPU feature expansion
reached 117.3K/s with 32 workers, 155.2K/s with 16, and 83.1K/s with eight. These
use batch 8192, prefetch four, one preparation thread per worker, 256-row CPU
chunks, BF16 backbone, and FP32 export/readout. The 525K/s compute-only result is
not a full-pipeline rate. Startup measured 42/22/12 seconds for 32/16/eight workers.

The subsequent batch-4096 / 16-worker profile reached **160.5K windows/s** over
4,194,304 windows, with 21.3-second startup. This is the launch default, with the
same BF16/FP32 split and CUDA graphs. It is the fastest measured configuration,
not a claim of a global optimum. Native validation uses four bounded CPU metric
reducers; 2,233 price metrics agree with serial reduction to 1.98e-13 relative
difference and all 108 raw MAEs agree exactly. A 131,072-window validation profile
reported zero nonfinite, negative, and structural violations and approximately
80.6K windows/s after warmup. This short prefix is not a population quality result.

The pinned finalist's development price error was 0.73363% FP32 and 0.73407%
BF16 with GPU preparation. No nonempty price band exceeded the qualification
tolerance (5% relative increase plus 1e-5 absolute relative error); aggregate
price/activity/worst-band tolerances are 2%. Eight laptop contract tests pass.
The real GPU path passed captured/eager optimizer-update parity and verified
checkpoint serialization/resume parity. Tiny-set objective fell from 0.69093 to
0.00023989 after 800 additional updates (engineering qualification only).

Every 5,000 updates, frozen development diagnostics report all-visible and
48-masked reconstruction separately. Every epoch validates all 47,750,007 frozen
validation windows, with all 18 native MAEs, price-relative histogram quantiles
and bands, activity log errors, and structural/nonfinite counts. Full training
contains 186,575,722 windows per epoch. No validation subset is called a full pass.

Runtime profiling evidence and the final launch configuration are recorded under
`D:\TradingML\runtimes\bar_gpt\phase1-v7-full-setup-20261117` on the workstation.

The full run is `phase1-v7-full-e08-b4096-50ep-20261117`, W&B project
`mehdifaraji/bargpt-v7-full`, run `sx629rcz`. Its initial immutable source was
`f1b259994ec0e8f6d13c3ef274874a98da88756e`. A manually started, triggerless task
keeps the controller alive independently of SSH; no recurring automation exists.
Snapshots remain immutable; explicit engineering transitions require pinned
parent checkpoints and hashed qualification receipts.

Launch diagnosis found Task Scheduler's default priority 7 had inherited Below
Normal CPU and Low I/O priority into the controller, trainer, and 16 workers.
Those 18 exact PID/creation-time owners were changed to Normal CPU and Normal I/O
priority in place, without restart or optimizer/traversal changes. Runtime receipts
are `priority-correction.json` and `io-priority-correction.json` inside the run.
The task is now priority 4; the launcher explicitly sets both classes for future
launches. This launcher-only fix does not rewrite the active immutable snapshot.

## Qualified raw-input GPU preparation

After the live run exposed continued CPU feeding limits, source
`625895d83f4e42ed4998b1b70194fbeffd70942d` moved FP64 log transforms and the shared
UTC-feature arithmetic onto the GPU. Workers retain all source, clock, timeframe,
price/activity, OHLC, and paired-quote validity checks. CPU and GPU time paths
share `BarInputTransformV1.assemble`; no alternative calendar semantics exist.
Raw arrays shared between input/target bookkeeping transfer only once.

The new path reached **225,618 windows/s over 8,388,608 windows**, with 8.81 seconds
of loader wait in 37.18 seconds and 20.87-second startup. That exceeds the prior
160,512/s profile by 40.6%. Pinned-model precision, tiny overfit, eager/captured
updates, checkpoint resume, and native metric parity passed. All 108 raw MAEs
matched the previous validation path exactly; histogram summation differed only
at floating-point reduction precision. Twenty-seven laptop tests passed.

The active run resumed under the same W&B identity at update **26,943**, with
**110,358,528** training windows consumed. The preserved parent checkpoint is
`checkpoint_before_raw_gpu_step-26943.pt`, SHA256
`9b7b31b200203362ac79e9004081f3a6ebc7ffe47b23e01e9bfef6a398e24b15`.
`engineering-raw-gpu-receipt.json` binds the parent state, qualification evidence,
and new source. Resume rejects any architecture, objective, optimizer, schedule,
batch-size, worker-count, or traversal change. Only source revision and equivalent
preparation backend change. Optimizer/scheduler/RNG and sample cursor persist;
the ordered source prefix is replayed without optimizer updates.

## Completed-pass throughput tuning (2026-11-17)

The user authorized larger batches, worker/prefetch tuning, faster full validation,
and the V5 raw style progress panel. This transition is allowed only after a
complete training pass, with checkpoint phase `validation` and its exact training
population count. Architecture, data, loss, optimizer state, RNG, learning-rate
schedule and W&B identity remain fixed. A larger batch changes the number of
optimizer updates per subsequent epoch; this is an explicitly authorized change,
not a claim of bitwise-equivalent training.

The preserved epoch-1 parent is `checkpoint_before_throughput_step-45561.pt`,
SHA256 `2b5a7e03c0d5ce76cd13d9025ce5648e13b99b7f1b3c4c89cdf854683408cb37`.
It contains all 186,575,722 training windows. Its interrupted validation must
restart in full; partial validation is never presented as complete.

Engineering adds independently selectable validation batch size, bounded shard
read-ahead depth/bytes, captured inference/native inverse, deferred validation
failure-counter synchronization, and startup-separated loader telemetry. Price
relative histograms retain their existing CPU Float64 definitions and masks.
The V5-style read-only panel shows training/validation progress, phase-specific
speed/ETA, completed full-validation quality, and loader wait separately from
startup/replay. Missing values stay unavailable; stale training rates are never
shown as validation throughput. Profiles and qualification receipts reside under
`D:/TradingML/runtimes/bar_gpt/phase1-v7-throughput-20261117`.

### Selected settings and measured results

Workstation source `27740a981f0572fa538449823bae1ad45682e256` resumed the same
`sx629rcz` run. Training uses batch 8,192, 24 workers, four ready batches per
worker, and verified shard read-ahead depth 32 with a 256 MiB budget per worker.
Validation independently uses batch 4,096 and eight workers with captured
inference. BF16 backbone, FP32 exported scale/readout and FP64 native inverse
remain unchanged. No learning-rate scaling was applied.

| Training batch / workers | Profile windows | Windows/s | Loader wait | Startup |
| --- | ---: | ---: | ---: | ---: |
| 8,192 / 16 | 8,388,608 | 201,325 | 20.36 s | 21.43 s |
| 8,192 / 32 | 8,388,608 | 227,429 | 8.07 s | 43.71 s |
| 16,384 / 24 | 8,388,608 | 223,141 | 3.44 s | 32.26 s |
| 32,768 / 24 | 8,388,608 | 218,112 | 0.03 s | 33.52 s |
| **8,192 / 24** | **16,777,216** | **230,895** | **16.33 s** | **31.91 s** |

These are bounded-prefix steady rates, not promises about whole-epoch speed.
Loader waiting overlaps queued GPU work; it is not a measurement of GPU idle
seconds. The largest batch removed virtually all loader wait but was slower,
so filling GPU memory was not the selection criterion.

Validation with batch 4,096 improved from 98,973/s eager to 114,168/s captured
on the same 1,048,576 windows. All 2,245 compared metric fields matched exactly,
including all 108 raw MAEs, validity counts, and price-band quantiles. A longer
2,097,152-window check with eight workers reached 114,515/s. Batch 16,384
validation was rejected at 83,777/s. The first capture attempt failed because
advanced indexing constructed an unpinned CPU index tensor; fixed channel
slices resolved this with exact inverse and captured/eager prediction parity.

Eleven laptop integrity tests passed. After resume, every tensor and field in
model, optimizer, RNG, scheduler, epoch, step, traversal cursor, population
counters and prior history matched the preserved parent exactly. The hashed
`throughput-resume-receipt.json` records the source transition and evidence.
`comparison.json` under the throughput runtime root retains every successful
profile and identifies the rejected/failed trials. Runtime `resume.ps1` and
`resumed-task.xml` record the complete reproducible launch; no recurring trigger
was added. The full dataset and 50-epoch schedule remain in effect.

The resumed run completed all 47,750,007 epoch-1 validation windows in
437.28 seconds (steady 109,839 windows/s), with zero nonfinite, negative, or
OHLC/paired-quote violations. Balanced price MAPE was 0.238019%; activity log
MAE was 0.093366. These are full-population reconstruction metrics, not evidence
of trading usefulness. Epoch 2 advanced beyond step 46,400; a new checkpoint at
step 46,000 confirmed continued saving under the new contract.

The initial live epoch-2 observation was 216,671 windows/s including checkpoint
overhead. A 20-second sample measured 63.2% average GPU busy time, 97% peak,
and 5,112 MiB peak device memory use. These observations are recorded in
`epoch2-live-verification.json`, alongside the initial/final counters and every
GPU sample. Do not substitute the 230,895/s bounded profile for sustained
whole-epoch throughput. The read-only panel uses snapshot `fa76fef64`; the
trainer remains on immutable snapshot `27740a981` without another interruption.
