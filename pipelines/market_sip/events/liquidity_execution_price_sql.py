"""Producer-owned price/volume sidecar for passive 100 ms Backtest fills.

The existing liquidity bucket contains eligible volume and a separate price
range, but cannot prove that eligible volume traded through an arbitrary limit.
This compact child product stores one row per eligible execution price. Only a
producer may insert it, from certified canonical SIP events; Backtest SELECTs
the completed rows and never creates or repairs them.
"""
from __future__ import annotations

from datetime import date

from pipelines.market_sip.events.market_day_sql import (
    POLICY, bounds, canonical_source, condition_expressions, literal,
)
from pipelines.market_sip.events.trade_reporting_flags import DELAYED


TABLE = "arte.liquidity_execution_price_100ms_v1"
COVERAGE_TABLE = "arte.liquidity_execution_price_coverage_v1"


def ddl() -> tuple[str, str]:
    """Normalized price rows and a coverage-last certificate on SSD only."""
    return (
        f"""CREATE TABLE IF NOT EXISTS {TABLE} (
          source_build_id String, session_date Date, ticker LowCardinality(String),
          source_attempt_id UUID, derivation_attempt_id UUID,
          bucket_index UInt32, price_int UInt64,
          execution_volume Float64
        ) ENGINE=MergeTree PARTITION BY toYYYYMM(session_date)
          ORDER BY (source_build_id,session_date,ticker,source_attempt_id,
                    derivation_attempt_id,bucket_index,price_int)
          SETTINGS storage_policy='{POLICY}'""",
        f"""CREATE TABLE IF NOT EXISTS {COVERAGE_TABLE} (
          source_build_id String, session_date Date, ticker LowCardinality(String),
          source_attempt_id UUID, derivation_attempt_id UUID,
          price_row_count UInt64, eligible_bucket_count UInt32,
          total_execution_volume Float64, content_hash FixedString(64),
          certified_at DateTime64(6,'UTC')
        ) ENGINE=MergeTree PARTITION BY toYYYYMM(session_date)
          ORDER BY (source_build_id,session_date,ticker,source_attempt_id,
                    derivation_attempt_id)
          SETTINGS storage_policy='{POLICY}'""",
    )


def insert_sql(source_build_id: str, session_date: date, ticker: str,
               source_attempt_id: str, derivation_attempt_id: str,
               rules: list[dict]) -> str:
    """Aggregate only the broker product's exact execution-valid trades.

    This is a one-time producer query, not a Backtest query. Its classifier is
    intentionally the same as market_day_sql.broker_sql: delayed trades are
    excluded, trade conditions must allow volume, and an as-of NBBO must be
    fresh and contain the execution price. Certification must compare the
    sidecar's sum per bucket with the pinned liquidity bucket before publishing
    coverage. No partial attempt is eligible for Backtest reads.
    """
    form, _last, _high, volume = condition_expressions(rules)
    day = session_date.isoformat()
    source = canonical_source(session_date, ticker)
    window = "ORDER BY sip_timestamp_us,ordinal ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW"
    return f"""INSERT INTO {TABLE}
      WITH decoded AS (
        SELECT *,bitAnd(event_meta,1) AS kind,
          toUInt64(price_primary_int)*if(bitAnd(event_meta,2)!=0,1,100) AS price_int,
          toUInt64(price_secondary_int)*if(bitAnd(event_meta,4)!=0,1,100) AS secondary_int,
          toFloat64(size_primary) AS size,
          fromUnixTimestamp64Micro(toInt64(sip_timestamp_us),'America/New_York') AS local_time,
          toHour(local_time)*3600+toMinute(local_time)*60+toSecond(local_time) AS local_second,
          arrayFilter(t->t>0,[toUInt16(condition_token_1),toUInt16(condition_token_2),
            toUInt16(condition_token_3),toUInt16(condition_token_4),toUInt16(condition_token_5)]) AS tokens,
          ({form}) AS form_ok,
          kind=1 AND bitAnd(event_meta,{DELAYED})=0 AND price_int>0
            AND size>0 AND isFinite(size) AS usable,
          toUInt8(usable AND {volume}) AS volume_valid,
          kind=0 AND secondary_int>0 AND price_int>=secondary_int
            AND isFinite(size) AND isFinite(toFloat64(size_secondary))
            AND size>=0 AND toFloat64(size_secondary)>=0 AS valid_quote
        FROM ({source})
      ), quoted AS (
        SELECT *,argMaxIf(tuple(sip_timestamp_us,secondary_int,price_int,
          toFloat64(size_secondary),size),tuple(sip_timestamp_us,ordinal),valid_quote)
          OVER ({window}) AS q FROM decoded
      ), classified AS (
        SELECT *,toUInt8(q.1>0 AND sip_timestamp_us>=q.1
          AND intDiv(sip_timestamp_us-q.1,1000)<=1000) AS nbbo_valid,
          toUInt8(volume_valid AND nbbo_valid AND
            price_int+greatest(q.2,q.3,price_int)*1e-9>=q.2 AND
            price_int<=q.3+greatest(q.2,q.3,price_int)*1e-9) AS execution_valid
        FROM quoted
      ) SELECT {literal(source_build_id)},toDate({literal(day)}),{literal(ticker)},
        toUUID({literal(source_attempt_id)}),toUUID({literal(derivation_attempt_id)}),
        toUInt32(intDiv(sip_timestamp_us-{bounds(session_date)},100000)) AS bucket_index,
        price_int,sum(size) AS execution_volume
      FROM classified WHERE execution_valid=1
      GROUP BY bucket_index,price_int"""
