# News Reaction Model V16

V16 is a controlled extension of V15. It preserves the V15 current-news
embedding, point-in-time stock state, publication-time vector, four-item
same-ticker context, opportunity targets, heads, loss, optimizer, scheduler,
chronological split, and evaluation. The isolated experiment adds causal
cross-market attention.

## Experiment

At each current single-ticker article, V16 asks whether the state of market
attention helps predict that ticker's reaction. It adds:

- the current ticker's completed pre-news price and activity state;
- the latest 100 strictly earlier single-ticker articles across the market;
- each earlier article's own pre-news state and only the post-news reaction
  observable by the current timestamp;
- up to 20 anonymous point-in-time market leaders;
- explicit current-ticker gainer, loser, volume, dollar-volume, and
  relative-volume ranks and top-10/top-20 memberships.

Ticker identity is not embedded. A context item exposes only a same-ticker flag.
This prevents the market branch from becoming an issuer lookup table.

## Causal market contract

Historical publication time is the V16 modeling clock because the normalized
historical corpus does not contain a trustworthy original feed-arrival
timestamp. Production must use the gateway's received/available timestamp.

For a current article at `t0`:

- equal-timestamp articles are excluded;
- market-news context contains the latest 100 articles with
  `published_at_utc < t0`, bounded to the current and three prior exchange
  sessions;
- pre-news windows are 1m, 5m, 10m, and 30m;
- only complete one-minute bars whose end is no later than `t0` are visible;
- the publication minute is excluded from post-news activity because a
  one-minute aggregate cannot separate pre- and post-publication events;
- fixed prior-reaction horizons are visible only when their authoritative
  `available_at_utc < t0`; unfinished horizons are zero and masked;
- an as-of reaction is capped at `t0`;
- market ranks use only state observable at the same completed-minute boundary.

The event query applies the same canonical SIP trade-condition `update_last`
and `update_high_low` rules used by the news reaction label builder. It does not
substitute every reported trade for an eligible price event.

## Market features

The current ticker and every leader use one schema:

```text
4 pre-news windows x 9 fields
+ session-to-date x 9 fields
+ 12 cross-sectional rank and membership fields
= 57 values
```

Each window contains terminal/high/low return, volume, dollar volume, trade
count, quote count, VWAP distance, and availability. Volume/count values are
log encoded. Every market return is encoded as
`sign(r) * min(log1p(abs(r)), 8)`: this is approximately identity for ordinary
small returns while keeping genuine extreme moves and malformed source prints
finite in float16. Relative volume is causal session-to-date volume divided by
the mean daily trade volume from up to 20 prior daily macro sessions.

The typed minute-bar transfer contract is explicitly:

```text
ticker, minute_end_us, open, high, low, close,
volume, dollar_volume, trade_count, quote_count
```

The original `market_attention_v1` preparation emitted SQL fields as
`open, close, high, low` while the typed `DayMarketData` consumer read the tuple
as `open, high, low, close`. Consequently, its V16-only current-market,
market-news, and leader features are not trustworthy. Event-derived reaction
labels and the inherited V8 article embedding and stock-state inputs are not
affected. The corrected transfer is deliberately versioned as
`news_reaction_openai_market_attention_dataset_v16_v2` under
`market_attention_v2`; the old complete manifest cannot be silently reused or
migrated because the original OHLC values cannot be reconstructed from the
prepared feature vectors.

Each prior market-news token contains its 57 pre-news values, four completed
post-news windows, one current as-of window, age, same-ticker,
same-exchange-session, and exchange-session distance. A leader token adds
whether the ticker appears in the latest-100 news context and its news count.

Leader selection is the stable deduplicated union of top gainers, losers,
volume, dollar-volume, and relative-volume names, capped at 20. Current-ticker
memberships are always carried in its own 57-value vector even when it is not
one of the 20 leader tokens.

## Data build

The immutable article/embedding source remains:

```text
market_sip_compact.news_reaction_openai_stock_state_dataset_v8
```

The full-period intraday bar and scanner tables are incomplete, so V16 does not
use them. It derives bounded daily completed-minute state directly from
`market_sip_compact.events_YYYY`. The preparation pipeline submits four exchange
sessions concurrently, consumes them in chronological article order, and keeps
at most five consumed sessions plus four bounded look-ahead results in memory.
Each ClickHouse request is capped at four threads and 16 GiB, so the default
aggregate concurrency claim is 16 threads and 64 GiB rather than an accidental
four-times multiplication of the former per-query limits. This is a targeted
news-timestamp build, not a global intraday-bar backfill.
One bounded month of article rows is materialized before session prefetch begins
so article paging does not add hidden ClickHouse work above that market-query
budget.

ClickHouse returns typed tab-separated minute rows. The Python loader no longer
creates one JSON dictionary per bar or re-sorts the already ordered result.
Completed fixed-horizon reactions for prior articles are memoized once their
authoritative availability time passes, and a prior session's final as-of
reaction is memoized after that exchange date closes. Same-session as-of
reactions remain dynamic. This removes repeated bar searches without freezing
information that was not yet observable.
Event eligibility, completed-minute boundaries, market ranks, and causal
ordering remain unchanged. The market-return representation is explicitly
versioned as `signed_log1p_scale1_clip8_v2`.

Prepared arrays default to:

```text
D:\market-data\prepared\news_reaction_model\v16\market_attention_v2
```

OpenAI embeddings remain unique. Both V15 same-ticker and V16 market-news
contexts store earlier row indices. Large market metadata arrays use float16;
model batches convert them to float32. The builder checkpoints only completed
months and reconstructs the bounded causal histories when resuming.

If a pre-v2 build reached its final audit, rerunning the same command performs
a resumable array migration rather than repeating ClickHouse extraction. It
reads the completed raw-return arrays, writes each return-log array to a
separate temporary memmap, atomically promotes it, and only then removes that
array's legacy source. The migration refuses NaNs, converts representational
positive/negative infinity to the documented `+8/-8` saturation, updates the
representation hash, and reruns the complete integrity audit.

```powershell
python -m research.news_reaction_model.v16.run_prepare_data --execute
```

The defaults can be overridden explicitly when profiling a different
ClickHouse host:

```powershell
python -m research.news_reaction_model.v16.run_prepare_data `
  --market-prefetch-workers 4 `
  --market-max-threads 4 `
  --market-max-memory-usage 16G `
  --execute
```

`market-prefetch-workers * market-max-threads` is the intended aggregate CPU
budget. Increase one only after measuring the ClickHouse host; completed
futures also retain one in-memory market day per worker.

Use `--restart` only when intentionally discarding the known V16 prepared
files:

```powershell
python -m research.news_reaction_model.v16.run_prepare_data --restart --execute
```

## Model

V16 first runs the unchanged V15 current-article and same-ticker-context path.
It then creates:

- 100 market-news tokens from the shared OpenAI projection plus market metadata;
- 20 leader tokens from anonymous market-state features.

Token type and position embeddings distinguish news from leaders. A six-head
current-to-market attention layer updates the article representation before
the unchanged horizon encoder and three-class opportunity heads. Rows without
market tokens use an exact bypass; no learned placeholder is injected.

## Commands

```powershell
python -m research.news_reaction_model.v16.run_profile_sizes --real-data
python -m research.news_reaction_model.v16.run_train
python -m research.news_reaction_model.v16.run_evaluate
```

The split remains train 2019-2025 and validation 2026. W&B remains
`news-reaction-model-v3` for direct comparison with V15.

## Systematic 2026 error study

After exporting the best-checkpoint predictions, run:

```powershell
python -m research.news_reaction_model.v16.run_error_study `
  --checkpoint D:\...\checkpoint_best_val.pt `
  --predictions D:\...\evaluation_predictions.jsonl.gz `
  --output-dir D:\...\error_study_2026
```

The launcher discovers the latest conventional V16 run when all three paths
are available. Explicit paths remain the reproducible authority.

The study:

- creates one article-level hard-vote audit across every authoritative
  applicable horizon;
- assigns false-long, false-short, missed-upside, missed-downside, and
  false-opportunity error classes;
- records two-sided outcomes, horizon conflicts, and timing mismatches as
  orthogonal diagnostic attributes;
- produces confidence calibration and statistically guarded slices by
  publication session, price, source metadata, deterministic news kind/topic,
  V16 context availability, market leadership, ticker frequency, and nearby
  same-ticker news;
- selects 100 cases from each of six fixed review strata;
- retrieves approximate OpenAI-embedding candidates from 2019-2025 and reranks
  them with exact cosine similarity;
- reconstructs condition-aware eligible-trade minute paths for the review
  cases using the same shared query as V16.

Outputs are written under one run directory:

```text
manifest.json
error_study_summary.json
error_study_report.md
article_audit.jsonl.gz
error_taxonomy.csv
confidence_calibration.csv
slice_metrics.csv
human_review_sample.csv
embedding_neighbors.jsonl.gz
price_paths.jsonl.gz
```

`--no-news-enrichment`, `--no-embedding-neighbors`, and `--no-price-paths`
support bounded diagnostic runs without pretending those stages completed.
The manifest records the actual enabled stages, input hashes, representation
hash, configuration, and output inventory. See
[`ERROR_STUDY_GUIDE.md`](ERROR_STUDY_GUIDE.md) for label semantics and the
review protocol.

## Live inference contract

`LiveFeatureEncoder` accepts the current article plus fixed-size V15 and V16
context tensors. The live market-context provider must maintain the same
completed-minute/rank state from QMD events, use the gateway receive timestamp,
exclude equal/future news, and mask incomplete reactions. All market tensors
may be omitted only for an explicit cold start.

## Limitations

- Completed-minute activity intentionally trades sub-minute precision for a
  causal, bounded market context. Authoritative price targets remain the exact
  event-derived reaction labels.
- Relative volume uses prior full-day volume rather than a minute-of-session
  seasonality curve.
- The existing labels are abnormal, market-adjusted returns. Evaluation's
  midpoint P&L remains descriptive, not an executable fill/exit simulation.
