# News Reaction Model V15

V15 is a controlled extension of the corrected V12 OpenAI-embedding baseline.
It keeps V12's current-news inputs, three-class opportunity targets, horizon
heads, split, loss, optimizer, and scheduler. Its only modeling experiment is
strictly causal prior same-ticker news context.

## Question

Does knowing the last few articles for the same stock, and only the portions of
their market reactions already observable when the current article arrives,
improve prediction beyond the current article alone?

V15 uses:

- the current 3,072-value OpenAI embedding;
- the unchanged point-in-time stock state;
- the unchanged V12 publication-time vector;
- up to four earlier articles for the same ticker from the previous seven
  calendar days, ordered oldest to newest.

Both same-day and prior-session articles are eligible. Articles with the exact
same publication timestamp are not eligible for one another.

## Causal context contract

For each prior article V15 stores a reference to its one canonical OpenAI
embedding rather than duplicating that vector. The context token also contains:

- terminal, high, and low returns for each of the 10 horizons;
- one explicit availability mask per horizon;
- normalized time gap, calendar-day distance, and market-session distance;
- same-market-day and same-publication-session flags;
- prior publication-session one-hot values.

A prior reaction value is visible only when
`news_reaction_labels_v2.available_at_utc` is strictly earlier than the current
article's `published_at_utc`. Unknown future horizons are zero with a false
availability mask. The reaction-session distance is derived from
`news_reaction_calendar_v1`, not from weekday arithmetic.

The resulting context feature is 49 values per prior article:

```text
30 reaction values
+ 10 horizon availability flags
+ 3 time-distance values
+ 2 same-day/session flags
+ 4 prior-session one-hot values
= 49
```

## Model

The current article follows the V12 encoder. Every prior embedding reuses the
same OpenAI text projection as the current article. A context projection encodes
the 49 causal metadata values. The text and metadata are fused into four
ordered prior-news tokens, then a six-head current-to-prior attention layer
updates the current article representation.

If an article has no eligible history, the context update is bypassed exactly;
there is no learned placeholder context and no database fallback.

The outputs remain one three-class head per horizon:

1. no meaningful opportunity;
2. upside dominant;
3. downside dominant.

## Prepared dataset

The source V8 table remains immutable:

```text
market_sip_compact.news_reaction_openai_stock_state_dataset_v8
```

V15 builds a local indexed, memory-mapped dataset at:

```text
D:\market-data\prepared\news_reaction_model\v15\causal_context_v1
```

This keeps each large embedding once and stores prior rows as integer indices.
The build is resumable at completed month boundaries, validates global
chronological ordering, and rejects current/future context indices.

Build it first:

```powershell
python -m research.news_reaction_model.v15.run_prepare_data --execute
```

Use `--restart` only to deliberately discard and rebuild the known V15 prepared
files:

```powershell
python -m research.news_reaction_model.v15.run_prepare_data --restart --execute
```

## Profile, train, and evaluate

```powershell
python -m research.news_reaction_model.v15.run_profile_sizes --real-data
python -m research.news_reaction_model.v15.run_train
python -m research.news_reaction_model.v15.run_evaluate
```

The default split remains chronological:

- train: 2019-01-01 through 2025-12-31;
- validation: 2026-01-01 through 2026-12-31.

The default training configuration remains comparable with V12: `d_model=384`,
four residual blocks, batch 2,048, 50 epochs, cosine scheduling with 49
restarts, peak learning-rate decay 0.98, and W&B project
`news-reaction-model-v3`.

Evaluation reads model inputs from the exact local V15 prepared dataset. It
queries ClickHouse only for anchor prices used by the descriptive one-share
P&L report, so evaluation cannot accidentally drop the prior-news context.

## Live inference

`LiveFeatureEncoder` accepts the current article plus fixed-size
`prior_openai_embeddings`, `prior_context_features`, and `prior_context_mask`
arrays built under the same causal contract. Missing context is a valid
cold-start state represented by an all-false mask. A production context service
must never fill unavailable reaction horizons or use same/future timestamp
articles.

## Limitations

- V15 tests whether short recent article history adds signal; it does not model
  an unlimited news sequence.
- Prior reactions are the existing abnormal, market-adjusted label returns.
- The evaluator's midpoint P&L is descriptive and uses realized extrema. It is
  not an executable exit strategy and ignores path ordering, costs, and fills.
