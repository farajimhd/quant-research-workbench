"""Bounded larger-sample loader for notebook 07; same eligibility as notebook 01.

Separate count/result limits preserve the earlier demonstration loader unchanged.
Canonical reads only; no database writes or raw-file fallback.
"""
from datetime import datetime, timedelta
from hashlib import sha256
import json
from zoneinfo import ZoneInfo

from research.mlops.env import load_env_files
from research.mlops.clickhouse import (
    ClickHouseHttpClient, discover_clickhouse_env_files,
    default_clickhouse_url, default_clickhouse_user, default_clickhouse_password,
)
from research.level_book.v7.campaign_source import (
    literal, reporting_coverage_sql, require_reporting_coverage,
)
from pipelines.market_sip.events.trade_reporting_flags import EVALUATED, DELAYED


def load_pressure_sample(ticker="AAPL", day="2026-08-20", count=50000):
    """First N eligible trades in 09:30-16:00 ET; fail if unavailable.

    count=None reads the complete regular session, capped at 2 million eligible
    trades with an explicit error rather than truncation. Integer counts retain
    the original sample behavior.
    Order is SIP availability time then canonical ordinal, not execution time.
    Differences describe adjacent *eligible* trades. Excluded records are audited.
    """
    if not ticker.isascii() or not ticker.isalnum() or (count is not None and not 2 <= count <= 100000):
        raise ValueError("Expected an ASCII ticker and 2..100000 trades or None for a full session")
    result_cap = 2000000 if count is None else count+5000
    start = datetime.fromisoformat(day + "T09:30:00").replace(tzinfo=ZoneInfo("America/New_York"))
    end = start + timedelta(hours=6, minutes=30)
    load_env_files(discover_clickhouse_env_files(), verbose=False)
    client = ClickHouseHttpClient(
        default_clickhouse_url(), default_clickhouse_user(), default_clickhouse_password(),
        timeout_seconds=90, persistent=True,
        default_query_params=dict(readonly=1, max_threads=2, max_memory_usage=1073741824,
                                  max_result_rows=result_cap, max_result_bytes=512000000 if count is None else 64000000,
                                  result_overflow_mode="throw"),
    )
    def query(sql):
        return [json.loads(line) for line in client.execute(sql + " FORMAT JSONEachRow").splitlines()]
    metadata_sql = ("SELECT * FROM market_sip_compact.events_ordinal_continuity FINAL "
                    f"WHERE ticker={literal(ticker)} AND source_date={literal(day)}")
    rules_sql = ("SELECT token_id,modifier_int,update_last,update_volume FROM "
                 "market_sip_compact.event_condition_token_reference "
                 "WHERE source_family='trade_conditions' AND is_join_canonical=1 ORDER BY token_id")
    reporting_sql = reporting_coverage_sql(day, day)
    try:
        metadata, rules, reporting = query(metadata_sql), query(rules_sql), query(reporting_sql)
        require_reporting_coverage([day], reporting)
        if len(metadata) != 1 or not rules:
            raise ValueError("Missing unique certified source-day metadata or condition rules")
        m = metadata[0]
        stop = int(m['next_ordinal']); first = stop - int(m['event_count'])
        if first < 0 or stop != int(m['last_ordinal']) + 1 or stop <= first:
            raise ValueError("Invalid certified ordinal range")
        known = [int(r['token_id']) for r in rules if 0 <= int(r['modifier_int']) <= 65535]
        eligible = [int(r['token_id']) for r in rules
                    if 0 <= int(r['modifier_int']) <= 65535 and r['update_last'] == r['update_volume'] == 1]
        # Canonical non-condition sentinel tokens are removed using the shared V7 mapping.
        terms = ','.join(f'condition_token_{i}' for i in range(1, 6))
        with_sql = (f"WITH arrayFilter(x->has({known},x),[{terms}]) AS tokens, "
                    f"arrayAll(x->has({eligible},x),tokens) AS condition_ok, "
                    f"bitAnd(event_meta,{EVALUATED})!={EVALUATED} AS unknown_clock, "
                    f"bitAnd(event_meta,{DELAYED})!=0 AS delayed, "
                    "(price_primary_int<=0 OR size_primary<=0 OR NOT isFinite(size_primary)) AS invalid")
        source = (f"FROM market_sip_compact.events_{start.year} WHERE ticker={literal(ticker)} "
                  f"AND ordinal>={first} AND ordinal<{stop} "
                  f"AND sip_timestamp_us>={int(start.timestamp()*1000000)} "
                  f"AND sip_timestamp_us<{int(end.timestamp()*1000000)} AND bitAnd(event_meta,1)=1")
        audit = query(with_sql + " SELECT count() candidate_trades, "
                      "countIf(unknown_clock) unknown_clock_count, countIf(delayed) delayed_count, "
                      "countIf(invalid) invalid_count, countIf(NOT condition_ok) condition_rejected, "
                      "countIf(NOT unknown_clock AND NOT delayed AND NOT invalid AND condition_ok) eligible " + source)
        expected = int(audit[0]['eligible']) if count is None else count
        if expected < 2 or expected > result_cap:
            raise ValueError(f"Eligible session size {expected} outside bounded read capacity {result_cap}")
        sql = (with_sql + " SELECT ordinal,toInt64(sip_timestamp_us) AS time_us, "
               "toInt64(price_primary_int)*if(bitAnd(event_meta,2)=2,1,100) AS price_units, "
               "toFloat64(size_primary) AS volume " + source +
               f" AND NOT unknown_clock AND NOT delayed AND NOT invalid AND condition_ok "
               "ORDER BY sip_timestamp_us,ordinal" + (f" LIMIT {count}" if count is not None else ""))
        rows = query(sql)
        if len(rows) != expected:
            raise ValueError(f"Required {expected} eligible trades, found {len(rows)}")
        if (query(metadata_sql), query(rules_sql), query(reporting_sql)) != (metadata, rules, reporting):
            raise ValueError("Canonical metadata changed during read")
        keys = [(int(r['time_us']), int(r['ordinal'])) for r in rows]
        if keys != sorted(keys) or len({r['ordinal'] for r in rows}) != expected:
            raise ValueError("Non-unique or unordered trade coordinates")
        provenance = dict(ticker=ticker, day=day, requested_count=count,
                          source_table=f"market_sip_compact.events_{start.year}",
                          window_start=start.isoformat(), window_end=end.isoformat(),
                          metadata=metadata, condition_rules=rules, reporting_coverage=reporting,
                          audit=audit, query=sql, price_units_per_dollar=10000,
                          audit_note="Rejection reasons overlap; audit covers the regular-session source window.")
        provenance['sample_sha256'] = sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
        return rows, provenance
    finally:
        client.close()
