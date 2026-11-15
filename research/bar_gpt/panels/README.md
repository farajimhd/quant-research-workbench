# Phase-two training console

`phase2_training_v1.py` is an independent Rich observer. It never imports Torch,
changes a checkpoint or signals a trainer. Closing it closes only the viewer.
The trainer's immutable source and implementation fingerprint stay unchanged.

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B -m research.bar_gpt.panels.phase2_training_v1 --run D:/TradingML/runtimes/bar_gpt/phase2-latent-v1-20261114 --setup D:/TradingML/runtimes/bar_gpt/phase2-full-pass-correction-20261115
```

Solid block bars show aggregate block traversal over all full dataset passes,
current-pass block traversal, optimizer updates until the next full validation,
and active validation origins. The last completed validation population supplies
the validation denominator; a missing or exceeded population stays unknown.
Legacy validation-cycle epoch labels are never interpreted as full passes.
Completed validations and completed full passes are separate counters.

Tables separate current training-batch metrics from the last complete validation,
best validation MSE, baselines, patience, target-referenced decoded price errors,
and GPU/RAM/disk resources. Unknown values are dashes, never invented zeros.
The latest checkpoint proof supplies its recorded step, not a fresh full hash audit.
W&B is linked from the local identity; the observer never authenticates to W&B
or claims that local status proves remote uploads. Utilization/power telemetry
comes from progress; VRAM used/total/free comes from a bounded device0
`nvidia-smi` query every20seconds and expires after60seconds. It creates no CUDA
context. A viewer reading a UNC path refuses local process/resource verification
instead of displaying the laptop's hardware as the workstation's capacity.

Reads: progress JSON, changed validation results, at most64KiB of metric tail,
latest checkpoint proof, reporting identity, explicit owner ledger and manifest.
Refresh defaults to2seconds. Ownership is verified by PID, creation time and
command. Stale/read failures retain the last useful snapshot with a warning.
Resources are checked against64GiB RAM and150GiB disk reserves.

Use `--once` for one readable snapshot or `--json` for explicit structured output.
Redirected output is a single UTF-8 snapshot without cursor escapes. `NO_COLOR`
retains the distinct solid/shaded bar glyphs. Width/height adapt down to40x12.
`--capture` exports text/SVG and `--observer-state` writes an optional viewer-only
heartbeat; both must be outside the training run under `D:/TradingML/runtimes`.
The requested visible workstation viewer is launched as one interactive scheduled
task with duplicate-instance suppression; it does not register recurring training.
