"""Publish a dated, evidence-labelled float resolution for the tradable universe."""

from __future__ import annotations

import argparse
import json
import os
import re
import uuid
from datetime import UTC, date, datetime

from research.mlops.clickhouse import (
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
    discover_clickhouse_env_files,
    quote_ident,
)
from research.mlops.env import load_env_files


TABLE = "market_security_float_resolved_v1"
VERSION = "reference-float-resolution-v1"
POLICY = "live_market_ssd"
DISK = "live_market_ssd"


def sql(database: str, name: str) -> str:
    return f"{quote_ident(database)}.{quote_ident(name)}"


def scalar(client: ClickHouseHttpClient, query: str) -> str:
    return client.execute(query + " FORMAT TSV").strip()


def verify_storage(client: ClickHouseHttpClient, database: str) -> None:
    policy = scalar(client, "SELECT count() FROM system.storage_policies WHERE policy_name='live_market_ssd' AND volume_name='main'")
    if policy != "1":
        raise RuntimeError("live_market_ssd/main storage policy is unavailable")
    required = (
        "feature_tradable_universe_v1", "id_sec_market_bridge_v3", "sec_xbrl_company_fact_v3",
        "market_security_float_v1", "market_security_market_snapshot_v1", "market_stock_split_v1",
    )
    for db, name in [*((database, name) for name in required), ("market_sip_compact", "daily_session_bars_by_symbol_time_v1")]:
        table_policy = scalar(client, f"SELECT storage_policy FROM system.tables WHERE database='{db}' AND name='{name}'")
        if table_policy != POLICY:
            raise RuntimeError(f"{db}.{name} storage policy is {table_policy!r}, expected {POLICY}")
        misplaced = scalar(client, f"SELECT count() FROM system.parts WHERE database='{db}' AND table='{name}' AND active AND disk_name != '{DISK}'")
        if misplaced != "0":
            raise RuntimeError(f"{db}.{name} has {misplaced} active parts outside main")


def create_table(client: ClickHouseHttpClient, database: str) -> None:
    client.execute(f"""
CREATE TABLE IF NOT EXISTS {sql(database, TABLE)}
(
    resolution_date Date,
    symbol_id String,
    ticker String,
    issuer_id String,
    resolution_kind LowCardinality(String),
    confidence LowCardinality(String),
    float_shares Nullable(Float64),
    float_lower_bound Nullable(Float64),
    float_upper_bound Nullable(Float64),
    shares_outstanding Nullable(Float64),
    shares_outstanding_source String,
    shares_outstanding_as_of Nullable(Date),
    shares_outstanding_evidence_ref String,
    shares_outstanding_content_sha256 String,
    shares_outstanding_conflict UInt8,
    sec_public_float_usd Nullable(Float64),
    sec_period_end Nullable(Date),
    sec_filed_at_utc Nullable(DateTime64(3, 'UTC')),
    sec_accession String,
    sec_content_sha256 String,
    provider_evidence_ref String,
    provider_content_sha256 String,
    price_date Nullable(Date),
    price_close Nullable(Float64),
    split_factor Float64,
    rejection_reason LowCardinality(String),
    calculation_version LowCardinality(String),
    source_fingerprint UInt64,
    run_id String,
    inserted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(inserted_at)
PARTITION BY resolution_date
ORDER BY (resolution_date, symbol_id)
SETTINGS storage_policy = '{POLICY}'
""")
    policy = scalar(client, f"SELECT storage_policy FROM system.tables WHERE database='{database}' AND name='{TABLE}'")
    if policy != POLICY:
        raise RuntimeError(f"{database}.{TABLE} storage policy is {policy!r}")
    client.execute(f"ALTER TABLE {sql(database, TABLE)} ADD COLUMN IF NOT EXISTS confidence LowCardinality(String) DEFAULT '' AFTER resolution_kind")
    client.execute(f"ALTER TABLE {sql(database, TABLE)} ADD COLUMN IF NOT EXISTS sec_content_sha256 String DEFAULT '' AFTER sec_accession")
    client.execute(f"ALTER TABLE {sql(database, TABLE)} ADD COLUMN IF NOT EXISTS provider_evidence_ref String DEFAULT '' AFTER sec_content_sha256")
    client.execute(f"ALTER TABLE {sql(database, TABLE)} ADD COLUMN IF NOT EXISTS provider_content_sha256 String DEFAULT '' AFTER provider_evidence_ref")
    client.execute(f"ALTER TABLE {sql(database, TABLE)} ADD COLUMN IF NOT EXISTS shares_outstanding_source String DEFAULT '' AFTER shares_outstanding")
    client.execute(f"ALTER TABLE {sql(database, TABLE)} ADD COLUMN IF NOT EXISTS shares_outstanding_as_of Nullable(Date) AFTER shares_outstanding_source")
    client.execute(f"ALTER TABLE {sql(database, TABLE)} ADD COLUMN IF NOT EXISTS shares_outstanding_evidence_ref String DEFAULT '' AFTER shares_outstanding_as_of")
    client.execute(f"ALTER TABLE {sql(database, TABLE)} ADD COLUMN IF NOT EXISTS shares_outstanding_content_sha256 String DEFAULT '' AFTER shares_outstanding_evidence_ref")
    client.execute(f"ALTER TABLE {sql(database, TABLE)} ADD COLUMN IF NOT EXISTS shares_outstanding_conflict UInt8 DEFAULT 0 AFTER shares_outstanding_content_sha256")


def projection(database: str, day: date, run_id: str) -> str:
    db = quote_ident(database)
    day_sql = day.isoformat()
    cutoff = f"least(now64(3), toDateTime64('{day_sql} 23:59:59.999', 3, 'UTC'))"
    # A regular-session close is used only as a labelled estimate.  One issuer
    # may own several tradable classes; those cases never receive issuer float.
    return f"""
WITH
    toDate('{day_sql}') AS resolution_day,
    {cutoff} AS cutoff,
    universe AS
    (
        SELECT symbol_id, any(ticker) ticker, any(issuer_id) issuer_id, any(currency_code) currency_code
        FROM {db}.feature_tradable_universe_v1 FINAL
        WHERE universe_date = resolution_day AND is_tradable = 1 AND inserted_at <= cutoff
        GROUP BY symbol_id
    ),
    class_count AS
    (
        SELECT issuer_id, count() tradable_classes FROM universe GROUP BY issuer_id
    ),
    bridge AS
    (
        SELECT symbol_id, any(cik) bridge_cik, uniqExact(cik) cik_count
        FROM {db}.id_sec_market_bridge_v3 FINAL
        WHERE mapping_status = 'active' AND ambiguity_status = 'unique'
          AND symbol_id IS NOT NULL AND symbol_id != '' AND inserted_at <= cutoff
        GROUP BY symbol_id
    ),
    reported AS
    (
        SELECT symbol_id,
          nullIf(argMaxIf(free_float, tuple(effective_date, inserted_at), free_float > 0), 0) reported_float,
          argMaxIf(source_evidence_ref, tuple(effective_date, inserted_at), free_float > 0) provider_ref,
          argMaxIf(source_content_sha256, tuple(effective_date, inserted_at), free_float > 0) provider_sha
        FROM {db}.market_security_float_v1 FINAL
        WHERE effective_date <= resolution_day AND inserted_at <= cutoff
        GROUP BY symbol_id
    ),
    sec_float AS
    (
        SELECT toString(cik) cik,
          argMax(value, tuple(period_end_date, filed_at_utc, recorded_at_utc)) sec_value,
          argMax(period_end_date, tuple(period_end_date, filed_at_utc, recorded_at_utc)) sec_date,
          argMax(filed_at_utc, tuple(period_end_date, filed_at_utc, recorded_at_utc)) filed,
          argMax(accession_number, tuple(period_end_date, filed_at_utc, recorded_at_utc)) accession,
          argMax(source_content_sha256, tuple(period_end_date, filed_at_utc, recorded_at_utc)) sec_sha
        FROM {db}.sec_xbrl_company_fact_v3 FINAL
        WHERE tag = 'EntityPublicFloat' AND unit_code = 'USD' AND value > 0
          AND period_end_date BETWEEN resolution_day - INTERVAL 520 DAY AND resolution_day
          AND filed_at_utc <= cutoff AND recorded_at_utc <= cutoff
        GROUP BY cik
    ),
    sec_shares AS
    (
        SELECT toString(cik) cik,
          nullIf(argMaxIf(value, tuple(period_end_date, filed_at_utc, recorded_at_utc), value > 0), 0) shares,
          argMaxIf(period_end_date, tuple(period_end_date, filed_at_utc, recorded_at_utc), value > 0) shares_date,
          argMaxIf(accession_number, tuple(period_end_date, filed_at_utc, recorded_at_utc), value > 0) shares_accession,
          argMaxIf(source_content_sha256, tuple(period_end_date, filed_at_utc, recorded_at_utc), value > 0) shares_sha
        FROM {db}.sec_xbrl_company_fact_v3 FINAL
        WHERE tag IN ('EntityCommonStockSharesOutstanding', 'CommonStockSharesOutstanding')
          AND unit_code = 'shares' AND period_end_date <= resolution_day
          AND filed_at_utc <= cutoff AND recorded_at_utc <= cutoff
        GROUP BY cik
    ),
    shares_sources AS
    (
        SELECT symbol_id, toFloat64(shares_outstanding) value, effective_date as_of,
          'provider_share_class' source, source_evidence_ref evidence_ref,
          source_content_sha256 content_sha, 4 priority
        FROM {db}.market_security_float_v1 FINAL
        WHERE shares_outstanding > 0 AND effective_date <= resolution_day AND inserted_at <= cutoff
        UNION ALL
        SELECT symbol_id, toFloat64(share_class_shares_outstanding), toDate(observed_at_utc),
          'provider_snapshot_share_class', snapshot_evidence_ref, source_content_sha256, 3
        FROM {db}.market_security_market_snapshot_v1 FINAL
        WHERE share_class_shares_outstanding > 0 AND observed_at_utc <= cutoff AND inserted_at <= cutoff
        UNION ALL
        SELECT s.symbol_id, toFloat64(s.weighted_shares_outstanding), toDate(s.observed_at_utc),
          'provider_weighted_single_class', s.snapshot_evidence_ref, s.source_content_sha256, 2
        FROM {db}.market_security_market_snapshot_v1 s FINAL
        INNER JOIN universe u ON u.symbol_id = s.symbol_id
        INNER JOIN class_count cc ON cc.issuer_id = u.issuer_id AND cc.tradable_classes = 1
        WHERE s.weighted_shares_outstanding > 0 AND s.observed_at_utc <= cutoff AND s.inserted_at <= cutoff
        UNION ALL
        SELECT u.symbol_id, toFloat64(sh.shares), sh.shares_date,
          'sec_issuer_single_class', sh.shares_accession, sh.shares_sha, 1
        FROM universe u
        INNER JOIN class_count cc ON cc.issuer_id = u.issuer_id AND cc.tradable_classes = 1
        INNER JOIN bridge b ON b.symbol_id = u.symbol_id AND b.cik_count = 1
          AND u.issuer_id = concat('issuer:cik:', b.bridge_cik)
        INNER JOIN sec_shares sh ON sh.cik = b.bridge_cik
        WHERE sh.shares > 0
    ),
    shares_resolved AS
    (
        SELECT symbol_id,
          argMax(tuple(value, source, as_of, evidence_ref, content_sha),
            tuple(as_of, priority, content_sha, evidence_ref)) supply
        FROM shares_sources GROUP BY symbol_id
    ),
    splits AS
    (
        SELECT symbol_id, groupArray(tuple(execution_date, split_to, split_from)) split_rows
        FROM {db}.market_stock_split_v1 FINAL
        WHERE execution_date <= resolution_day AND inserted_at <= cutoff
        GROUP BY symbol_id
    ),
    candidates AS
    (
        SELECT u.symbol_id AS symbol_id, u.ticker AS ticker, u.issuer_id AS issuer_id, u.currency_code AS currency_code,
          r.reported_float AS reported_float,
          ifNull(r.provider_ref, '') AS provider_ref, ifNull(r.provider_sha, '') AS provider_sha,
          sr.supply.1 AS shares, sr.supply.2 AS shares_source,
          sr.supply.3 AS shares_date, sr.supply.4 AS shares_ref,
          sr.supply.5 AS shares_sha,
          sf.sec_value AS sec_value, sf.sec_date AS sec_date, sf.filed AS filed,
          ifNull(sf.accession, '') AS accession, ifNull(sf.sec_sha, '') AS sec_sha,
          b.cik_count AS cik_count, cc.tradable_classes AS tradable_classes,
          ifNull(sp.split_rows, []) AS split_rows
        FROM universe u
        LEFT JOIN class_count cc ON cc.issuer_id = u.issuer_id
        LEFT JOIN bridge b ON b.symbol_id = u.symbol_id AND u.issuer_id = concat('issuer:cik:', b.bridge_cik)
        LEFT JOIN reported r ON r.symbol_id = u.symbol_id
        LEFT JOIN shares_resolved sr ON sr.symbol_id = u.symbol_id
        LEFT JOIN sec_float sf ON sf.cik = b.bridge_cik AND b.cik_count = 1
        LEFT JOIN splits sp ON sp.symbol_id = u.symbol_id
    ),
    prices AS
    (
        SELECT upper(ifNull(canonical_ticker, source_ticker)) ticker, session_date,
          argMax(trade_close, bar_end_us) close,
          max(available_at_us) last_available_at_us
        FROM market_sip_compact.daily_session_bars_by_symbol_time_v1 FINAL
        PREWHERE session_date BETWEEN resolution_day - INTERVAL 527 DAY AND resolution_day
        WHERE session_kind = 'regular' AND trade_present = 1 AND adjusted = 0
          AND identity_status != 'ambiguous_source_ticker' AND canonical_ticker IS NOT NULL
          AND available_at_us <= toUInt64(toUnixTimestamp64Micro(cutoff))
        GROUP BY ticker, session_date
    ),
    aligned AS
    (
        SELECT c.*,
          if(p.session_date >= c.sec_date - INTERVAL 7 DAY, p.close, 0) AS price,
          if(p.session_date >= c.sec_date - INTERVAL 7 DAY, p.session_date, toDate(0)) AS price_day
        FROM candidates c ASOF LEFT JOIN prices p
          ON p.ticker = upper(c.ticker) AND p.session_date <= c.sec_date
    ),
    calculated AS
    (
        SELECT *,
          arrayFold((acc, x) -> acc * if(x.3 > 0 AND x.2 > 0 AND x.1 > sec_date, x.2 / x.3, 1.0), split_rows, 1.0) split_factor,
          if(price > 0, sec_value / price * split_factor, 0.0) sec_estimate
        FROM aligned
    )
SELECT
    resolution_day resolution_date, symbol_id, ticker, issuer_id,
    multiIf(reported_float > 0, 'reported',
      currency_code = 'USD' AND sec_value > 0 AND price > 0 AND cik_count = 1 AND tradable_classes = 1
        AND (shares IS NULL OR sec_estimate <= shares), 'sec_market_value_implied',
      shares > 0, 'shares_outstanding_upper_bound', 'unavailable') resolution_kind,
    multiIf(reported_float > 0, 'high',
      currency_code = 'USD' AND sec_value > 0 AND price > 0 AND cik_count = 1 AND tradable_classes = 1
        AND (shares IS NULL OR sec_estimate <= shares), 'low',
      shares > 0, 'bound_only', 'none') confidence,
    multiIf(reported_float > 0, toNullable(toFloat64(reported_float)),
      currency_code = 'USD' AND sec_value > 0 AND price > 0 AND cik_count = 1 AND tradable_classes = 1
        AND (shares IS NULL OR sec_estimate <= shares), toNullable(sec_estimate), NULL) float_shares,
    if(reported_float > 0, toNullable(toFloat64(reported_float)), NULL) float_lower_bound,
    if(reported_float > 0, toNullable(toFloat64(reported_float)), if(shares > 0, toNullable(toFloat64(shares)), NULL)) float_upper_bound,
    toNullable(toFloat64(shares)) shares_outstanding,
    if(shares > 0, shares_source, '') shares_outstanding_source,
    if(shares > 0, toNullable(shares_date), NULL) shares_outstanding_as_of,
    if(shares > 0, shares_ref, '') shares_outstanding_evidence_ref,
    if(shares > 0, shares_sha, '') shares_outstanding_content_sha256,
    toUInt8(reported_float > 0 AND shares > 0 AND reported_float > shares) shares_outstanding_conflict,
    toNullable(toFloat64(sec_value)) sec_public_float_usd,
    toNullable(sec_date) sec_period_end, toNullable(filed) sec_filed_at_utc,
    accession sec_accession, sec_sha sec_content_sha256,
    provider_ref provider_evidence_ref, provider_sha provider_content_sha256,
    if(price > 0, toNullable(price_day), NULL) price_date,
    if(price > 0, toNullable(toFloat64(price)), NULL) price_close, split_factor,
    multiIf(reported_float > 0, '', currency_code != 'USD', 'non_usd_listing',
      tradable_classes > 1, 'multiple_tradable_share_classes',
      cik_count != 1, 'missing_or_ambiguous_sec_bridge', sec_value <= 0, 'missing_sec_public_float',
      price <= 0, 'missing_aligned_canonical_price',
      shares > 0 AND sec_estimate > shares, 'estimate_exceeds_shares_outstanding', '') rejection_reason,
    '{VERSION}' calculation_version,
    cityHash64(symbol_id, reported_float, shares, shares_source, shares_date, shares_ref, shares_sha,
      sec_value, sec_date, filed, accession,
      sec_sha, provider_ref, provider_sha, price_day, price, split_factor) source_fingerprint,
    '{run_id}' run_id, now64(3) inserted_at
FROM calculated
"""


def publish(client: ClickHouseHttpClient, database: str, day: date) -> dict[str, object]:
    verify_storage(client, database)
    day_text = day.isoformat()
    expected = int(scalar(client, f"SELECT uniqExact(symbol_id) FROM {sql(database, 'feature_tradable_universe_v1')} FINAL WHERE universe_date=toDate('{day_text}') AND is_tradable=1"))
    if expected == 0:
        raise RuntimeError(f"No tradable symbols published for {day_text}")
    create_table(client, database)
    run_id = uuid.uuid4().hex
    stage = f"{TABLE}_stage_{run_id[:12]}"
    client.execute(f"CREATE TABLE {sql(database, stage)} AS {sql(database, TABLE)}")
    try:
        client.execute(f"INSERT INTO {sql(database, stage)} {projection(database, day, run_id)}")
        actual = int(scalar(client, f"SELECT count() FROM {sql(database, stage)} FINAL WHERE resolution_date=toDate('{day_text}')"))
        unique = int(scalar(client, f"SELECT uniqExact(symbol_id) FROM {sql(database, stage)} FINAL"))
        bad = int(scalar(client, f"SELECT count() FROM {sql(database, stage)} FINAL WHERE (resolution_kind='reported' OR resolution_kind='sec_market_value_implied') AND (float_shares IS NULL OR float_shares<=0)"))
        if actual != expected or unique != expected or bad:
            raise RuntimeError(f"Float stage validation failed: expected={expected} rows={actual} unique={unique} bad={bad}; stage={stage}")
        placement = scalar(client, f"SELECT count() FROM system.parts WHERE database='{database}' AND table='{stage}' AND active AND disk_name!='{DISK}'")
        if placement != "0":
            raise RuntimeError(f"Float staging has {placement} misplaced parts; stage={stage}")
        client.execute(f"ALTER TABLE {sql(database, TABLE)} REPLACE PARTITION ID '{day.strftime('%Y%m%d')}' FROM {sql(database, stage)}")
        final_count = int(scalar(client, f"SELECT count() FROM {sql(database, TABLE)} FINAL WHERE resolution_date=toDate('{day_text}')"))
        if final_count != expected:
            raise RuntimeError(f"Published float count {final_count} differs from {expected}; stage={stage}")
        parts = scalar(client, f"SELECT count() FROM system.parts WHERE database='{database}' AND table='{TABLE}' AND active AND disk_name!='{DISK}'")
        if parts != "0":
            raise RuntimeError(f"Published float has {parts} misplaced parts; stage={stage}")
        summary_text = client.execute(f"SELECT resolution_kind, rejection_reason, count() rows FROM {sql(database, TABLE)} FINAL WHERE resolution_date=toDate('{day_text}') GROUP BY resolution_kind, rejection_reason ORDER BY rows DESC FORMAT JSONEachRow")
        conflicts = int(scalar(client, f"SELECT countIf(shares_outstanding_conflict=1) FROM {sql(database, TABLE)} FINAL WHERE resolution_date=toDate('{day_text}')"))
        return {"date": day_text, "rows": final_count, "run_id": run_id, "shares_outstanding_conflicts": conflicts, "kinds": [json.loads(line) for line in summary_text.splitlines()]}
    finally:
        # A failed stage remains available for diagnosis; completed stages are
        # removed only after the published partition is independently checked.
        if scalar(client, f"SELECT count() FROM {sql(database, TABLE)} FINAL WHERE resolution_date=toDate('{day_text}') AND run_id='{run_id}'") == str(expected):
            client.execute(f"DROP TABLE {sql(database, stage)}")


def main() -> None:
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    load_env_files(discover_clickhouse_env_files(), verbose=False)
    parser = argparse.ArgumentParser(description="Publish the Reference Gateway's dated resolved float authority")
    parser.add_argument("--database", default="q_live")
    parser.add_argument("--date", default="")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", args.database):
        raise ValueError("Invalid database name")
    client = ClickHouseHttpClient(default_clickhouse_url(), default_clickhouse_user(), default_clickhouse_password(), timeout_seconds=600)
    day = date.fromisoformat(args.date) if args.date else date.fromisoformat(scalar(client, f"SELECT max(universe_date) FROM {sql(args.database, 'feature_tradable_universe_v1')}"))
    print(json.dumps(publish(client, args.database, day), sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
