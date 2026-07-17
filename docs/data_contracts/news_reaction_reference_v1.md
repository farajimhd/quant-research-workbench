# News Phrase and Reaction Reference v1

This pipeline builds deterministic article-language facts and causal post-news
price-reaction labels. It is a reference-data build, not the frontend news
classifier and not an LLM training job.

## Authority and split

- News text and identities: `q_live.benzinga_news_normalized_v1`.
- Point-in-time ticker links: `q_live.benzinga_news_ticker_v1`.
- Price observations: canonical 1-second quote-bid, quote-ask, and eligible-trade
  families in `market_sip_compact.intraday_base_bars_by_time_ticker`.
- Exchange schedule: XNYS calendar generated with `pandas_market_calendars`,
  including holidays and early closes.
- Training period: 2019-01-01 through 2025-12-31.
- Held-out evaluation period: 2026-01-01 through 2026-12-31. The default stats
  query cannot consume 2026 labels.

The build refuses incomplete ticker-month bar coverage. The
`--allow-partial-bar-coverage` flag exists only for explicit development smoke
tests; missing prices stay visible and do not enter statistics.

## Output tables

| Table | Grain | Purpose |
| --- | --- | --- |
| `q_live.news_reaction_calendar_v1` | calendar date | Current and next XNYS premarket, open, close, and extended close boundaries. |
| `q_live.news_phrase_dictionary_v1` | dictionary version + phrase | Canonical concept, variants, family, prior direction/strength, and feature role. |
| `q_live.news_language_features_v1` | article + canonical phrase | One presence fact per article and phrase, with a title/body/tag/channel source bitmask. Repetition counts are not stored. |
| `q_live.news_reaction_labels_v1` | news + ticker + horizon | Anchor, terminal observation, window high/low, market-adjusted returns, applicability, overlap, and quality evidence. |
| `q_live.news_phrase_reaction_stats_v1` | phrase + horizon + publication session | Smoothed negative/neutral/positive probabilities and terminal/high/low return distributions from clean 2019-2025 samples. |
| `q_live.news_reaction_build_status_v1` | stage + version + date chunk | Restart-safe completed-chunk state and timing. |

## Causal labeling

For each news/ticker pair, `p0` is the last clean observation strictly before
`published_at_utc`. A synchronized valid bid/ask midpoint is preferred; an
eligible trade is the fallback. Each applicable horizon records the last price,
high, and low observed after publication through the horizon boundary.

The horizons are 1m, 5m, 10m, 30m, 1h, 2h, 3h, end of premarket, end of the
regular session, and end of extended hours. Fixed-duration horizons never carry
through a closed market. Session-boundary horizons use the exact XNYS schedule.
SPY labels are calculated in the same window and subtracted to produce abnormal
returns.

Rows remain auditable when a horizon is not applicable, the anchor or target is
missing or stale, or another same-ticker story arrives inside the reaction
window. Only `quality_status = 'clean'` contributes to phrase probabilities.
Observed-reaction phrases such as “shares fall” remain useful language facts but
are excluded from training because they describe the outcome.

## Run

First build the required canonical 1-second bars. Both date bounds are
start-inclusive and end-exclusive:

```powershell
python pipelines\market_sip\events\run_build_intraday_base_bars.py `
  --start-date 2019-01-01 `
  --end-date 2027-01-01 `
  --resolutions 1s
```

Then validate the extraction plan and coverage without writing:

```powershell
python pipelines\news\benzinga\run_news_reaction_extract.py
```

After the preflight reports complete coverage, build the tables:

```powershell
python pipelines\news\benzinga\run_news_reaction_extract.py --execute
```

The build is date-chunked and resumes completed chunks by default. Use
`--replace-existing` only when deliberately rebuilding the selected versions.
Run manifests are written under
`D:\market-data\prepared\news_reaction_labels`; they contain configuration,
coverage, counts, timing, and secret-presence status but no secret values.
