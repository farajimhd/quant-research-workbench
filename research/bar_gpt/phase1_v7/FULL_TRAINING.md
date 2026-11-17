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
`mehdifaraji/bargpt-v7-full`, run `sx629rcz`. Its immutable training source is
`f1b259994ec0e8f6d13c3ef274874a98da88756e`. A manually started, triggerless task
keeps the controller alive independently of SSH; no recurring automation exists.
The source snapshot and checkpoint contract remain unchanged during training.

Launch diagnosis found Task Scheduler's default priority 7 had inherited Below
Normal CPU and Low I/O priority into the controller, trainer, and 16 workers.
Those 18 exact PID/creation-time owners were changed to Normal CPU and Normal I/O
priority in place, without restart or optimizer/traversal changes. Runtime receipts
are `priority-correction.json` and `io-priority-correction.json` inside the run.
The task is now priority 4; the launcher explicitly sets both classes for future
launches. This launcher-only fix does not rewrite the active immutable snapshot.
