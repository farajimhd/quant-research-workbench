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
