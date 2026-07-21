# News Phrase and Reaction Reference (event-relative contract v2)

This pipeline builds deterministic article-language facts and causal post-news
price-reaction labels. It is reference-data generation, not the frontend news
classifier and not an LLM training job.

## Authority and split

- News text and identities: `q_live.benzinga_news_normalized_v1`.
- Point-in-time ticker links: `q_live.benzinga_news_ticker_v1`.
- Price observations: exact canonical compact events in
  `market_sip_compact.events_YYYY`.
- Exchange schedule: XNYS calendar generated with `pandas_market_calendars`,
  including holidays and early closes.
- Statistics training period: 2019-01-01 through 2025-12-31.
- Held-out evaluation period: 2026-01-01 through 2026-12-31. The default stats
  query cannot consume 2026 labels.

The compact event tables are already cleaned during canonical ingestion: invalid
rows and configured correction/cancel codes do not enter the event authority.
For price eligibility, the reaction query reads `update_last` and
`update_high_low` from `event_condition_token_reference` and applies the same
condition intersection and extended-hours Form-T exception as QMD. It preserves
exact SIP timestamps and ordinal ordering and does not read or build fixed-clock
intraday bars.

Event-table authority is restricted to the years intersecting the configured
publication range. With the default `[2019-01-01, 2027-01-01)` build, preflight
and reaction queries use only `events_2019` through `events_2026`; chunk
lookback/lookahead windows do not introduce `events_2018` or `events_2027` as
prerequisites. Missing observations at the true dataset edges remain explicit
quality states rather than expanding the source contract.

Preflight coverage is evaluated from active ClickHouse part metadata. It checks
that every required yearly table has active data and reports its stored row
count and date range without scanning the event payload.

Language features use one bounded presence predicate per canonical phrase and
source field. The query emits the combined title/body/tag/channel source mask
directly and never retains occurrence counts or global per-needle position
arrays. This keeps memory proportional to the source block rather than to every
position-array alias expansion.

## Output tables

| Table | Grain | Purpose |
| --- | --- | --- |
| `q_live.news_reaction_calendar_v1` | calendar date | Current and next XNYS premarket, open, close, and extended-close boundaries. |
| `q_live.news_phrase_dictionary_v1` | dictionary version + phrase | Canonical concept, variants, family, prior direction/strength, and feature role. |
| `q_live.news_language_features_v1` | article + canonical phrase | One presence fact per article and phrase, with title/body/tag/channel provenance. Repetition counts are not stored. |
| `q_live.news_reaction_labels_v2` | news + ticker + horizon | Exact event-relative anchor, terminal observation, high/low, market-adjusted returns, applicability, overlap, and quality evidence. |
| `q_live.news_phrase_reaction_stats_v2` | phrase + horizon + publication session | Smoothed probabilities and reaction distributions from clean 2019-2025 samples. |
| `q_live.news_reaction_build_status_v1` | stage + semantic version + date chunk | Restart-safe completed-chunk state and timing. |
| `q_live.news_reaction_quality_overlay_v1` | news + ticker + horizon | Split overlap, extreme-return evidence, and the final statistical-eligibility decision without rewriting source reaction labels. |
| `q_live.news_phrase_reaction_stats_v3` | phrase + horizon + publication session | Split-aware probabilities plus raw, median, and 1%-trimmed reaction summaries from 2019-2025. |
| `q_live.news_reaction_finalization_state_v1` | finalized feature month or reaction day | Source signature, source/event watermarks, certified row counts, and the audit that authorized finalization. |

## Exact causal labeling

For each news/ticker pair:

```text
t0 = published_at_utc
p0 = last eligible trade event strictly before t0

window start = t0 (exclusive)
window end   = t0 + fixed horizon, or an exact session boundary (inclusive)
terminal     = last eligible event at or before window end
high / low   = extrema of eligible events inside (t0, window end]
```

The horizons are 1m, 5m, 10m, 30m, 1h, 2h, 3h, end of premarket,
end of the regular session, and end of extended hours. Fixed-duration horizons
never carry through a closed market. Session-boundary horizons use the exact
XNYS schedule.

There is no time-grid resolution in this contract. A publication at
`09:41:20.600` includes an event at `09:41:20.601`; it does not discard the
remaining fraction of that second. The active semantic versions are
`news_reaction_event_labels_v3` and `news_phrase_event_reaction_stats_v3`.
They never resume the obsolete fixed-bar checkpoints.

SPY is processed from the same exact event source. Terminal SPY return is
subtracted from terminal asset return. High and low market adjustments use the
last SPY event at the exact timestamp of the asset high or low rather than
subtracting independently timed SPY extrema. Rows remain auditable when a horizon is
not applicable, an anchor or target is missing or stale, or another same-ticker
story arrives inside the reaction window. Only `quality_status = 'clean'`
contributes to phrase probabilities. Observed-reaction phrases such as “shares
fall” remain language facts but are excluded from statistics because they
describe the outcome.

## Performance and concurrency

Reaction work is partitioned into bounded publication-day chunks. Independent
chunks execute through separate ClickHouse HTTP clients with
`--reaction-workers` workers. `--max-threads` is a total CPU budget: the launcher
divides it across active workers instead of assigning the full thread count to
every query. Each query uses event-date and ticker predicate pushdown, decodes
only the required trade fields, and calculates all horizons in one insert.
Canonical SIP tickers are already uppercase, so the raw event predicate remains
on the stored `ticker` ordering key; applying a case-conversion function there
would prevent primary-key pruning. Publication months are processed
sequentially. For each month, the extractor first materializes the bounded
canonical news/ticker inputs, including publication and availability timestamps,
into a small short-lived cache. Event-cache shards, day workers, and overlap
checks all reuse it instead of independently rebuilding multi-million-row news
joins. The extractor then partitions the distinct requested tickers into 32
deterministic shards and reads every ticker's required event days once into one
shared, short-lived MergeTree cache. SPY is loaded once per month. Event-cache
rows contain compact, sorted tuples by ticker and event date, including the prior
eligible anchor needed at the month boundary.

Bounded day workers then calculate all news-relative anchors, terminal values,
highs, lows, and aligned SPY observations from that read-only monthly cache.
News-link inserts are sharded independently and dynamically: the default target
is 100 links per query with a hard maximum of 64 shards, so sparse days no
longer execute 32 mostly empty queries while heavily covered tickers still have
their articles distributed. Each worker pushes its shard ticker set and the
exact horizon-derived event dates into both current-event and prior-anchor cache
reads; it never decodes the entire monthly cache for one publication day. SPY
retains a continuous causal event spine across the bounded lookahead because it
is the benchmark used at every asset target and extrema timestamp. Each
article/ticker event set is assembled once through its maximum horizon, then
reused with an exact per-horizon timestamp cap. If a genuinely dense shard still
hits ClickHouse's memory limit, that day is cleared and retried with twice as
many deterministic news shards, up to the configured hard bound. Days
with no ticker-linked news are checkpointed
without reading events. This avoids fixed bars, repeated ticker/day event reads,
and the many-to-many news-horizon/event join expansion while keeping memory and
query concurrency bounded. Completed publication-day checkpoints remain
untouched on resume; only an incomplete day is deleted and rebuilt. The shared
monthly cache is transient and is dropped after completion or cancellation.

Defaults are four workers, eight total ClickHouse threads, and a 24 GiB total
reaction-query memory budget. The launcher divides both CPU threads and memory
across active workers. Join blocks are capped at 1,024 rows so array-valued
cache records cannot trigger multi-GiB allocation spikes. Reduce workers when
the ClickHouse host is shared or storage is saturated; increase the day chunk
size only after measuring representative query memory.

## Run

Validate sources, schemas, coverage, chunk plan, and command without writing:

```powershell
python pipelines\news\benzinga\run_news_reaction_extract.py
```

Build the versioned tables:

```powershell
python pipelines\news\benzinga\run_news_reaction_extract.py --execute
```

Example bounded tuning:

```powershell
python pipelines\news\benzinga\run_news_reaction_extract.py --execute `
  --reaction-workers 4 `
  --max-threads 24 `
  --reaction-chunk-days 1 `
  --reaction-ticker-shards 32 `
  --reaction-links-per-shard 100
```

The build resumes completed semantic-version chunks by default. Use
`--replace-existing` only when deliberately rebuilding the selected version.
Missing yearly event tables stop production execution. The
`--allow-partial-event-coverage` option is for explicit development validation;
missing observations remain visible and cannot enter clean statistics.

Run manifests are written below
`D:\market-data\prepared\news_reaction_labels`. They contain configuration,
source tables, coverage, counts, timings, and secret-presence status, but no
secret values.

## Finalization, repair, and holdout evaluation

The extractor checkpoint records durable work, but a completed write is not by
itself proof that mutable news and event sources were complete. Run the
finalizer after an extraction has caught up:

```powershell
# Read-only. Computes source watermarks and writes the exact repair plan.
python pipelines\news\benzinga\run_news_reaction_finalize.py

# Executes the reviewed plan, rebuilds derived tables, audits, and certifies.
python pipelines\news\benzinga\run_news_reaction_finalize.py --execute
```

The stable exclusive publication bound is the minimum of:

- the requested end date;
- the news settlement cutoff (48 hours by default); and
- the last publication date for which the compact-event watermark reaches the
  applicable current or next XNYS extended-session close.

Rows at or beyond that bound are provisional. The default execution removes
their feature/reaction rows and completed checkpoints so a later run can build
them from complete sources. `--keep-provisional-tail` is available only when a
caller explicitly needs those incomplete rows; provisional rows are never
certified or used for statistics or holdout evaluation.

Within the stable range, the repair planner compares current normalized-news
and ticker-link update timestamps, expected news/ticker/horizon key counts,
feature text hashes, output keys, and legacy checkpoint timestamps. The
certification record stores a deterministic source signature for independent
comparison and audit. Only stale months or publication days are passed to the
bounded exact-event extractor with `--replace-existing`. The default safety
limit stops a plan above 62 summed stage-days; inspect `repair_plan.json` before
using `--allow-large-repair`.

### Corporate actions and robust statistics

The raw reaction table remains immutable evidence. Statistical eligibility is
owned by the quality overlay. A label is ineligible when its base quality is
not clean, a known stock split execution date overlaps the anchor-to-target
date interval, or an abnormal target/high/low return exceeds the configured
extreme-return bound. Split data has execution-date rather than execution-time
precision, so same-day overlap is intentionally conservative.

Statistics use one phrase presence per article. Counts and smoothed
negative/neutral/positive probabilities are calculated from eligible rows.
Raw means remain available for audit, while medians and means trimmed to the
1st-99th percentile bounds provide robust summaries. The active finalized
statistics version is `news_phrase_event_reaction_stats_v4`; its training end
is checked against the 2026 holdout boundary before certification.

### Interpretable 2026 classifier

For each held-out news/ticker/horizon, the classifier joins the unique article
phrases to their matching horizon/session probabilities. Each phrase contributes
`positive_probability - negative_probability`, weighted by the square root of
its capped eligible support. The normalized evidence score is classified with
a symmetric configurable threshold. This is an interpretable phrase-probability
classifier, not an embedding model and not an LLM.

The finalizer writes:

- `holdout_evaluation.json`, including confusion matrix, accuracy, balanced
  accuracy, macro F1, and horizon/session breakdowns;
- `human_review_sample.csv`, a deterministic stratified sample with exactly one
  row per canonical news/ticker pair, blank reviewer fields, and no phrase IDs,
  model scores, predictions, horizons, or future price reactions;
- `human_review_sample_answer_key.csv`, containing the hidden phrase/model and
  reaction evidence for every available horizon under the same stable review ID;
  keep it closed until all human labels are locked; and
- `human_review_instructions.json`, the review label contract.

Certification is refused while any stale source slice, key-count mismatch,
duplicate, noncausal timestamp, target-after-boundary row, or invalid high/low
relationship remains.

Feature certification counts canonical source articles and extracted
article/phrase keys in independent monthly aggregates before joining them. This
prevents an unmatched source article from contributing ClickHouse's default
empty tuple to a month's certified output count.
