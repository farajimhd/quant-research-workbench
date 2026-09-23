# Compact trade reporting flags

`market_sip_compact.events_YYYY.event_meta` keeps bits 0–5 unchanged. Bit 6
(`0x40`) means this trade's reporting evidence was evaluated under
`trade_reporting_v1_conditions_5_13_30_31_32_33_prior_date_lag_gt10s`.
Bit 7 (`0x80`) means the trade was identified as delayed or out of sequence.
Thus `0x00` in the high bits is legacy/unknown, `0x40` is evaluated without
delayed evidence, and `0xC0` is delayed. `0x80` alone is invalid. Quote high
bits stay zero. Keep original condition tokens; a flag is a derived summary.

The v1 rule marks vendor sale conditions 5 (bunched sold), 13 (extended hours
sold out of sequence), 30/31 (sold last), and 32/33 (sold out of sequence),
even when the participant clock is absent. Otherwise it marks a valid
participant timestamp on an earlier New York date than SIP receipt, or a
participant-to-SIP gap **greater than** 10 seconds. Missing, zero, or later
than SIP participant timestamps are unknown unless an explicit condition is
present. Form T (12), odd lot (37), and correction status alone do not imply
delay. Reasons are counted separately; the `0x80` bit does not distinguish
them. This is a market-data exclusion policy, not a FINRA compliance finding.

New source days are encoded by the canonical `download_update_events.py`
insertion path. For previously certified days, use the ingestion-owned
`scripts/backfill_trade_reporting_flags.py`. It reads retained flatfiles only
for this explicitly versioned canonical migration, normalizes trades with the
same ingestion SQL, reconciles every trade's original compact fields by
ticker and order, and fails closed on missing rows or ambiguous ordering. The
canonical mutation changes `event_meta` only. Original trade tokens, quote
rows, and canonical ordinals are preserved.

For a multi-day range, the script stages and verifies each day separately,
copies only delayed and unknown ordinal keys into one SSD-backed map per month,
then submits one ClickHouse mutation for that month. This avoids rewriting a large
monthly source part once for every day. Each day is marked `complete` only
after the monthly mutation finishes and that day's flags pass exact
verification. A mutation interrupted on the client continues in ClickHouse;
the next run reuses its map and waits for its result.

The coverage authority is
`q_live.historical_trade_reporting_coverage_v1` on `live_market_ssd`. Each
source date records revision, source certificate digest, state, temporary
table names, and reason/reconciliation counts. `complete` means the ClickHouse
mutation finished and every targeted trade's flags were verified. `started`,
`staged`, and `mutating` do **not** certify a day. Temporary raw and mapping
tables stay on `live_market_ssd` until verification, then are dropped. The
canonical yearly table stays on `sip_raw_ssd`. A new run resumes incomplete
days and skips completed ones with matching provenance. Missing source days
remain explicitly reported, never marked complete.

From the source checkout with the configured workstation connection and
flatfiles available:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B scripts/backfill_trade_reporting_flags.py --start-date 2026-08-01 --end-date 2026-09-30 --plan-only
python -B scripts/backfill_trade_reporting_flags.py --start-date 2026-08-01 --end-date 2026-09-30 --hydrate-missing-trades
```

Run `--stage-only` first for a large new range to validate source mapping
without a canonical mutation. Repeating the command resumes from its coverage
state. Query coverage with `FINAL` and the exact revision, and inspect
`system.mutations` before treating a `mutating` day as usable. Bar and Backtest
consumers must check this coverage and the flag contract when they are cut
over. Existing bar artifacts should be rebuilt after a completed source day
is accepted; a flag mutation does not rewrite already persisted bars.

The certified August 2026 trade gzips are retained on the workstation G
archive, while ClickHouse `file()` is restricted to the D flatfile mount.
`--hydrate-missing-trades` copies only missing trade gzips from the G archive
to the D cache. Each archive copy must match the certified byte length and
nanosecond modification time; the staged D copy is additionally compared by
SHA-256 before atomic publication. Both roots have explicit CLI overrides.

References: [Massive trade fields](https://www.massive.com/blog/insights-from-trade-level-data),
[Massive sale-condition eligibility](https://www.massive.com/blog/understanding-trade-eligibility).
