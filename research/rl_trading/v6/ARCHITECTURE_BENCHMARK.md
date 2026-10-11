# Matched temporal architecture benchmark

This laptop experiment compares scalable TCN, GRU and Transformer teachers on
the same authenticated, split-adjusted observations and copied 1b targets. It
does not replace the full ranked-market policy or the default training launcher.
Each candidate has the same own-channel projection, reference-position input,
hierarchical current action, conditional quality, ENTRY allocation and separate
five-step autoregressive forecast decoder. Width can be enlarged in multiples
of 16. Default temporal depths remain six TCN residual blocks, two GRU layers
and three Transformer layers.

Capacity is selected without outcomes: nearest to one million parameters,
maximum 15% discrepancy between candidates. Initialization seed, optimizer,
row order, microbatches, losses and sample exposure are identical. There is one
seed per architecture; the result cannot establish seed robustness.

The initial sample contains 128 approved TRAIN decisions, balanced at 32 per
current action across July 31 and August 3–7. Every forecast horizon has complete
class coverage. Windows are the last 120 observed bank candles strictly before
the decision; gaps retain elapsed time. Missing seconds are not filled. Prior
session scalar values come from the certified split-adjusted bank view; V7
structural values remain unchanged. Normalizers are authenticated TRAIN-only
artifacts. Target, episode identity and future outcomes never enter the model.

The default budget is 256 joint updates at AdamW learning rate 0.001, followed
by 384 frozen-representation head updates at 0.003. Every update visits all 128
current targets with microbatch eight, so every candidate sees 81,920 current
training samples. Forecasts and both forced/free supervision have the same
exposure across candidates. Conditional losses use minibatch target masks;
this is an experimental equal-row objective, not population-weighted reporting.
The representation cache is rebuilt after an authenticated resume.

Only the fixed final checkpoint is admitted to validation. It must pass every
current and future class F1 >= 0.95, allocation and current/future conditional
quality MAE <= 0.02, with nonempty target coverage and exact prediction replay.
Failure is reported; validation is not opened for that candidate. Do not select
an earlier checkpoint using validation or tune thresholds on validation.

Validation is a deterministic label-blind sample of at most 2,048 decisions
per day in the first ten minutes of August 24 and 25, using the same target
ticker scope and frozen TRAIN normalizers. Sampling preserves natural class
prevalence in expectation and reports actual coverage. Both dates have been
inspected in previous experiments: this is exploratory validation, not a new
independent holdout. No sealed session is admitted. The local-window benchmark
omits ranked cross-market attention and is not full-policy acceptance.

Run from the repository with PYTHONDONTWRITEBYTECODE=1:

```powershell
python -B -m research.rl_trading.v6.run_architecture_benchmark --output D:/TradingML/runtimes/v6-matched-architectures-20261011
```

Use `--prepare-only` for authenticated data/capacity preflight. An interrupted
run requires `--resume` with the same source, manifest, data, budgets and roots.
Atomic state receipts bind optimizer/model updates to the manifest. The process
uses two CPU threads, BelowNormal priority, 50% synchronized GPU duty, and
4 GiB VRAM / 8 GiB host-memory reserves. Candidates run sequentially.

Per-label precision, recall, F1 and average precision, allocation MAE,
conditional quality MAE and each of the five forecast horizons are logged to
W&B under one comparison group. Runtime snapshots, checkpoints, prediction
arrays, progress and comparison results remain outside the repository.
