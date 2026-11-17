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

V6.3 adds a positional dense encoder (`flat_scale_slots`) as an architectural
ablation of token attention plus mean pooling. Visible centered values and their
masks are scattered into a fixed 64-position grid; absent bars have zero value
and zero evidence. A two-layer dense encoder exports the same 256 coordinates,
including the same 18 scale slots. Decoder size and reconstruction objective are
unchanged. This tests whether fixed positional access better retains ordering
and high-frequency activity than pooled token features. Parameter count changes
are reported; this is not a parameter-matched comparison.

V6.4 allows the dense learned trunk to grow with a 512-coordinate exported vector.
The 256-vector path is unchanged. The capacity experiment uses embedding 512 and
decoder hidden width 512 together, because leaving a 256-wide first decoder layer
would immediately compress the wider representation again. A companion decoder-256
ablation measures whether the wider decoder improves reconstruction without
mistaking decoder capacity for improved frozen-embedding information.

V6.5 optionally reserves three additional exported coordinates for visible bid-close
validity, ask-close validity and joint bid/ask-close validity fractions. These are
computed exclusively from supplied visible input masks. They replace three learned
coordinates, keeping export size fixed; decoder capacity stays unchanged. This
targets the consistently poor quote-coverage probes while explicitly separating
architectural information preservation from improvements learned by optimization.

The November 17 bounded campaign used eight experiment identities (one excluded
for a mistyped source revision and repeated with verified provenance). The final
source admission gate checks the complete revision and every deployed file hash.
E08 is the compact primary finalist; E06 is the activity-reconstruction alternative.
See runtime `comparison.txt`/`comparison.json` for the authoritative held-out outcome,
limitations, pinned hashes and all 18 raw-channel errors. The scientific budget is
exhausted; this source does not authorize an automatic full-data run.

`finalize.py --root CAMPAIGN_ROOT --names e08-token-validity,e06-wide-embedding`
freezes finalist identities before opening audit values, evaluates each once,
pins checkpoints, and verifies decoder-free export parity. It refuses to overwrite
an existing audit. `report.py --root CAMPAIGN_ROOT --recommended e08-token-validity`
reproduces the comparison from recorded evidence.

```python
from research.bar_gpt.phase1_v6.export import load_encoder
encoder = load_encoder("PINNED_ENCODER_ONLY.pt", device="cpu")
embedding = encoder.encode_raw(raw_values, source_valid, end_us, timeframe_us)
```

Inputs retain `[B,64,18]` market values/masks, `[B,64]` UTC microsecond clocks and
`[B]` timeframe microseconds. The export includes fixed training-only transform
buffers and encoder weights, but no decoder weights. It never accepts references
or statistics computed from hidden targets.

Final qualification: E06 is the balanced reconstruction research recommendation;
E08 remains the compact price/validity alternative. Held-out frozen probes improved
some shape information, not every metric. Neither is qualified for market-valid
all-feature reconstruction: independent OHLC readouts frequently violate ordering,
and activity outputs can be negative. Target OHLC validation found no corresponding
source violations. The eight-experiment limit has been reached. A constrained
readout/validity follow-up and full-authority streaming qualification are required
before approving the proposed full-data training configuration; no full run was
launched. The runtime report distinguishes the pre-audit primary finalist from
the final recommendation and retains all failures and tradeoffs.

For independent reconstruction without training or reopening the held-out audit,
run `python -B -m research.bar_gpt.phase1_v6.reproduce --checkpoint PINNED.pt
--data FROZEN_DATASET --output NEW_RUNTIME_REPORT.json --device cpu`.
This evaluates the development partition. CPU/CUDA mask draws differ; the
all-visible population is identical. `report.py --plot` also regenerates the
standalone learning-curve figure from immutable per-run logs.
