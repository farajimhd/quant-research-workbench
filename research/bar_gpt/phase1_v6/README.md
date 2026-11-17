# Independent-scale encoder campaign V6

Experimental research, separate from production BarGPT and all previous runs.
The immutable v6 balanced quote-corrected phase-one shards remain the authority.
Each example retains all 64 bars, 18 values, masks, UTC clocks and timeframe.

`data.py` verifies the original split seal, completed cohort dependencies, selected
receipt seals and payload hashes. Discovery stratifies source entries by timeframe
and manifest position. Selection reduces overlapping contexts within each source
job/frame. The original train/validation time boundary is preserved; validation
is split at 2025-01-01 04:00 UTC and crossing windows are purged. Frozen rows and
source identities are hashed. This is a research subset, not a probability sample
of the market. Discovery coverage must be inspected before freezing an experiment.

V6.1 compares pooled token encoding with explicitly exported visible scale slots.
Both use training-only fixed log(price)/log1p(activity) mean/std, existing nine time
features, source-valid masks and a compact 256-vector. FP32 model readout and FP64
inverse avoid BF16 output quantization. The decoder accepts only that vector;
no target references, per-example target statistics or raw skip are available.
Scale slots, when enabled, consume 18 coordinates of the same vector and contain
visible-input normalized log means. The remaining coordinates describe shape.
These are input summaries, never full-target references during masked training.

Training uses 16 visible bars with 48 physically removed. Full evaluation uses
all 64. No feature dropout in this initial controlled comparison. The decoder is
a fixed-position MLP of two hidden layers, with capacity held constant across the
initial ablation. Loss is equal price/activity-family smooth absolute error in
unstandardized log coordinates (Huber transition 0.01). No valid values are clipped.
Training samples equal mass per populated timeframe/last-close-price-band cell;
calibration and metrics use uniform selected rows/valid observations. Price bands
are below 0.1, 0.1–1, 1–5, 5–20, 20–100 and 100+. Empty groups are reported.

Selection combines equal-frame/channel price relative mean, 0.1 times the worst
price-band relative mean, and 0.1 times equal-frame/channel activity log1p MAE.
The final recommendation must also inspect group regressions, structure errors
and frozen ridge probes. Within-window probes measure scale, return, range,
volatility, spread, count and coverage; they are not future or trading labels.
The raw-summary control directly contains those statistics and is an information
ceiling for these probes. Untrained encoders and train-derived constant and
visible-context median reconstruction are recorded separately.

All artifacts live below `D:/TradingML/runtimes/bar_gpt/encoder-campaign-20261117`.
Prepare on the laptop, validate, commit/push, deploy an immutable hash-verified
snapshot, then execute on the workstation only after verified GPU handoff:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B -m research.bar_gpt.phase1_v6.launch --root D:/TradingML/runtimes/bar_gpt/encoder-campaign-20261117 --name e01-pooled -- --source-revision COMMITTED_SHA --variant pooled --steps 6000 --minutes 20 --hypothesis "Fixed log scaling improves native scale recovery"
python -B -m research.bar_gpt.phase1_v6.panel --root D:/TradingML/runtimes/bar_gpt/encoder-campaign-20261117
```

`campaign.json` pins the campaign wall-clock deadline and experiment ledger.
One GPU process runs at a time; the trainer checks actual CUDA owners. Per-run
deadlines reserve end evaluation. Timed-out evaluation never produces a completed
result. Rolling `last.pt` and selected `best.pt` include optimizer, schedule, RNG,
IID traversal state and immutable experiment identity. Engineering resume uses
the same train command plus `--resume` and its original deadline; changed science
requires a new identity. Human STOP files are never removed.

Run integrity tests with `python -B -m unittest research.bar_gpt.phase1_v6.test_contract`.
Actual candidate conclusions, native metrics, profiles, checkpoints, source hashes,
coverage limitations and audit access belong in the runtime comparison report.

V6.2 adds `--full-probability 0.5`: half of training batches reconstruct all-visible
inputs, half reconstruct the 48 removed bars. The architecture and decoder remain
unchanged. This tests the visibility/objective mismatch identified by V6.1's weak
shape probes and failure to beat a visible-context median on masked reconstruction.
Profiling exercises the selected mixture. Per-cell sampling probabilities are
persisted explicitly. Native metrics also expose variance of per-window channel
means across examples, in addition to pooled observation variance.
