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
would prevent primary-key pruning. Each day worker splits requested news/ticker
links into 32 deterministic shards. Link-based partitioning also distributes
days where one heavily covered ticker has many articles. It materializes a short-lived MergeTree cache for
one shard at a time, containing compact, sorted event tuples by ticker and event
date. Every news-relative anchor, terminal, high, low, and aligned SPY
observation is selected from those arrays before the cache is dropped. This
avoids both fixed bars and the many-to-many news-horizon/event join expansion
while bounding decoded-event memory.

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
  --reaction-chunk-days 1
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
