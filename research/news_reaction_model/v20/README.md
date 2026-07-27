# News Reaction Model V20

V20 is a model-only experiment over the completed V18.3 episode arrays and V15
OpenAI/stock-state arrays. It does not prepare, copy, or mutate either dataset.

## Objective

V20 gives a strategy one coherent forecast:

- `P(neutral)`, `P(upside)`, and `P(downside)`;
- unconditional expected signed return percentage;
- expected upside percentage conditional on upside;
- expected downside percentage conditional on downside.

These values are all derived from one signed-return probability distribution.
There is no independent direction head and no independent regression head that
can contradict it.

## Authoritative target

V18 classifies direction using the dominant event-episode excursion:

- neutral: neither excursion crosses V18's fitted meaningful-move threshold;
- upside: the positive high excursion dominates;
- downside: the absolute negative low excursion dominates.

V20 maps that existing authority to one signed opportunity:

```text
neutral  -> 0
upside   -> V18 high_return_pct
downside -> V18 low_return_pct
```

This preserves every V18 direction label exactly. V20 startup fails if its
bucketed signed return does not reproduce the authoritative direction class for
every training row.

The return distribution contains a dedicated neutral atom and the following
non-neutral percent ranges on both sides:

```text
0–0.5, 0.5–1, 1–2, 2–5, 5–10, 10–20, 20–50, 50–100, 100+
```

Negative ranges are symmetric. Each bucket's numeric representative is the
training-only median return inside that bucket. Empty buckets use a deterministic
interval midpoint or bounded tail fallback. Validation data never affects the
representatives.

Ranges with zero training support remain part of the versioned output schema but
are masked before softmax. They cannot absorb probability mass or become a
silent prediction class.

## Model

The default V20 model is deliberately more expressive than V19:

1. Gated projections independently encode current OpenAI text, point-in-time
   stock state, exchange-time state, current episode state, and price/session
   regime.
2. A four-layer current-feature transformer models interactions among those
   current tokens.
3. A separate two-layer causal prior-news transformer represents up to eight
   preceding episode articles and their already-observable reactions.
4. Two cross-attention blocks let the current article query prior episode state.
5. A sparse top-2 mixture of six learned experts routes each example to
   specialized regime transformations without hard-coding ticker, news family,
   or price bucket behavior.
6. One 19-class return-distribution head produces every strategy output.

Default dimensions:

```text
width                     768
attention heads            12
current transformer layers  4
prior transformer layers    2
cross-attention layers       2
experts                      6
active experts per sample    2
feedforward width         2304
expert hidden width       1536
dropout                    0.12
```

## Objective and selection

Training jointly optimizes:

- effective-number-weighted bucket cross entropy;
- ordered discrete CRPS so near-range errors cost less than distant errors;
- direction NLL derived by aggregating the same bucket probabilities;
- a small expected-return Smooth L1 term derived from the same distribution;
- sparse-expert load balancing.

Checkpoint selection averages:

- direction macro-F1;
- expected signed-return MAE skill over the training-median baseline;
- return-distribution log-loss skill over the training bucket-prior baseline.

Every direction class must retain nonzero validation recall. `latest.pt` is the
resume authority and `best_val.pt` is the inference/evaluation authority.

## Evaluation

Evaluation records:

- direction accuracy, balanced accuracy, macro-F1, per-class metrics and ECE;
- bucket accuracy and within-one-bucket accuracy;
- return-distribution log loss and prior-relative skill;
- expected signed-return MAE/RMSE and median-relative skill;
- mixture-of-experts utilization;
- confidence sweeps;
- price, session, episode structure, node role, root-family and duration cohorts.

The terminal one-share P&L table is descriptive only. It applies the predicted
direction to the observed terminal episode return. It does not treat the
predicted maximum excursion as realized P&L and excludes fills, costs, overlap
and capital constraints.

## Workstation commands

No `run_prepare_data` command is required.

From:

```powershell
cd D:\TradingML\codes\news-reaction-model\v20
conda activate ml4t
```

Profile the 96 GB GPU first:

```powershell
python -m research.news_reaction_model.v20.run_profile_sizes
```

The checked-in default can be trained directly:

```powershell
python -m research.news_reaction_model.v20.run_train
```

Use the profiler recommendation through explicit overrides when it materially
improves throughput:

```powershell
python -m research.news_reaction_model.v20.run_train `
  --d-model <width> `
  --attention-heads <heads> `
  --current-layers <layers> `
  --prior-layers <layers> `
  --expert-count <experts> `
  --batch-size <batch>
```

Resume an interrupted run:

```powershell
python -m research.news_reaction_model.v20.run_train `
  --resume D:\TradingML\runtimes\news-reaction-model\v20\train\single-ticker-episodes\<run>\checkpoints\latest.pt
```

Evaluate a selected checkpoint:

```powershell
python -m research.news_reaction_model.v20.run_evaluate `
  --checkpoint D:\TradingML\runtimes\news-reaction-model\v20\train\single-ticker-episodes\<run>\checkpoints\best_val.pt
```

V20 retains the chronological contract: 2019–2025 training and 2026 validation.
It uses the same W&B project as V19 for direct comparison.
