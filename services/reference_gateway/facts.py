from __future__ import annotations

from dataclasses import dataclass

from research.mlops.clickhouse import ClickHouseHttpClient, quote_ident
from services.reference_gateway.market_publications import mergetree_settings


FACT_TABLES: tuple[str, ...] = (
    "security_tradability_fact_v1",
    "security_routing_fact_v1",
    "security_share_supply_fact_v1",
    "security_news_catalyst_fact_v1",
    "security_sec_filing_event_fact_v1",
    "security_sec_text_signal_fact_v1",
    "issuer_fundamental_metric_fact_v1",
    "security_valuation_fact_v1",
)


@dataclass(frozen=True, slots=True)
class FactTableSpec:
    table_name: str
    owner: str
    alert_families: tuple[str, ...]
    source_tables: tuple[str, ...]
    purpose: str


FACT_TABLE_SPECS: tuple[FactTableSpec, ...] = (
    FactTableSpec(
        "security_tradability_fact_v1",
        "reference_gateway",
        ("tradability_guardrail",),
        ("id_mapping_issue_v1", "feature_tradable_universe_v1", "market_reference_alert_v1"),
        "Historical tradability state and block reasons for issuer/security/listing/symbol entities.",
    ),
    FactTableSpec(
        "security_routing_fact_v1",
        "reference_gateway",
        ("tradability_guardrail",),
        ("id_listing_v1", "id_symbol_v1", "id_source_mapping_v1"),
        "Broker routing evidence such as selected IBKR contract, conid, and ambiguity status.",
    ),
    FactTableSpec(
        "security_share_supply_fact_v1",
        "reference_gateway",
        ("share_supply",),
        ("sec_xbrl_company_fact_v1", "market_security_market_snapshot_v1"),
        "Canonical share-supply observations reconciled from XBRL and provider snapshots.",
    ),
    FactTableSpec(
        "security_news_catalyst_fact_v1",
        "reference_gateway",
        ("news_catalyst",),
        ("benzinga_news_normalized_v1", "benzinga_news_ticker_v1"),
        "Security-centric compact news labels and scores derived from normalized news rows.",
    ),
    FactTableSpec(
        "security_sec_filing_event_fact_v1",
        "reference_gateway",
        ("sec_filing",),
        ("sec_filing_v2", "id_sec_market_bridge_v1"),
        "Security-centric filing event history with accepted time, form, and mapped market entity.",
    ),
    FactTableSpec(
        "security_sec_text_signal_fact_v1",
        "reference_gateway",
        ("sec_filing", "data_quality"),
        ("sec_filing_text_v2", "sec_filing_document_v2"),
        "Compact deterministic or model-derived labels extracted from SEC filing text.",
    ),
    FactTableSpec(
        "issuer_fundamental_metric_fact_v1",
        "reference_gateway",
        ("fundamental",),
        ("sec_xbrl_company_fact_v1", "sec_xbrl_frame_observation_v1"),
        "Curated issuer fundamental metrics from XBRL facts, not a mirror of every XBRL row.",
    ),
    FactTableSpec(
        "security_valuation_fact_v1",
        "reference_gateway",
        ("fundamental", "feature_invalidation"),
        ("issuer_fundamental_metric_fact_v1", "market_security_market_snapshot_v1"),
        "Derived valuation and balance-sheet context tied to a security and source input versions.",
    ),
)


def ensure_fact_schema(client: ClickHouseHttpClient, *, database: str, storage_policy: str = "") -> None:
    client.execute(f"CREATE DATABASE IF NOT EXISTS {quote_ident(database)}")
    settings = mergetree_settings(storage_policy)
    for ddl in fact_schema_ddl(database, settings):
        client.execute(ddl)


def fact_schema_ddl(database: str, settings: str) -> tuple[str, ...]:
    return (
        f"""
CREATE TABLE IF NOT EXISTS {table(database, 'security_tradability_fact_v1')}
(
    tradability_fact_id String,
    issuer_id Nullable(String),
    security_id Nullable(String),
    listing_id Nullable(String),
    symbol_id Nullable(String),
    provider_ticker Nullable(String),
    effective_at_utc DateTime64(3, 'UTC'),
    observed_at_utc DateTime64(3, 'UTC'),
    is_tradable UInt8,
    block_status LowCardinality(String),
    block_reason Nullable(String),
    issue_type Nullable(String),
    issue_id Nullable(String),
    severity LowCardinality(String),
    confidence_score Nullable(Float64),
    source_system LowCardinality(String),
    source_table String,
    source_event_id String,
    source_evidence_ref String,
    source_content_sha256 String,
    source_run_id String,
    inserted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(inserted_at)
PARTITION BY toYYYYMM(effective_at_utc)
ORDER BY (ifNull(symbol_id, ''), effective_at_utc, source_system, tradability_fact_id)
SETTINGS {settings}
""".strip(),
        f"""
CREATE TABLE IF NOT EXISTS {table(database, 'security_routing_fact_v1')}
(
    routing_fact_id String,
    issuer_id Nullable(String),
    security_id Nullable(String),
    listing_id Nullable(String),
    symbol_id Nullable(String),
    provider_ticker String,
    broker LowCardinality(String),
    ibkr_conid Nullable(String),
    contract_symbol Nullable(String),
    sec_type Nullable(String),
    currency_code Nullable(String),
    exchange_code Nullable(String),
    listing_exchange Nullable(String),
    routing_status LowCardinality(String),
    ambiguity_status LowCardinality(String),
    valid_from_utc DateTime64(3, 'UTC'),
    valid_to_utc Nullable(DateTime64(3, 'UTC')),
    confidence_score Nullable(Float64),
    source_system LowCardinality(String),
    source_table String,
    source_event_id String,
    source_evidence_ref String,
    source_content_sha256 String,
    source_run_id String,
    inserted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(inserted_at)
PARTITION BY toYYYYMM(valid_from_utc)
ORDER BY (provider_ticker, broker, valid_from_utc, routing_fact_id)
SETTINGS {settings}
""".strip(),
        f"""
CREATE TABLE IF NOT EXISTS {table(database, 'security_share_supply_fact_v1')}
(
    share_supply_fact_id String,
    issuer_id Nullable(String),
    security_id Nullable(String),
    listing_id Nullable(String),
    symbol_id Nullable(String),
    provider_ticker Nullable(String),
    supply_metric LowCardinality(String),
    unit_code LowCardinality(String),
    value Float64,
    period_end_date Nullable(Date),
    effective_at_utc DateTime64(3, 'UTC'),
    observed_at_utc DateTime64(3, 'UTC'),
    source_priority UInt16,
    confidence_score Nullable(Float64),
    source_system LowCardinality(String),
    source_table String,
    source_event_id String,
    cik Nullable(String),
    accession_number Nullable(String),
    xbrl_taxonomy Nullable(String),
    xbrl_tag Nullable(String),
    source_evidence_ref String,
    source_content_sha256 String,
    source_run_id String,
    inserted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(inserted_at)
PARTITION BY toYYYYMM(effective_at_utc)
ORDER BY (ifNull(symbol_id, ''), supply_metric, effective_at_utc, source_priority, share_supply_fact_id)
SETTINGS {settings}
""".strip(),
        f"""
CREATE TABLE IF NOT EXISTS {table(database, 'security_news_catalyst_fact_v1')}
(
    news_catalyst_fact_id String,
    canonical_news_id String,
    provider_article_id String,
    issuer_id Nullable(String),
    security_id Nullable(String),
    listing_id Nullable(String),
    symbol_id Nullable(String),
    provider_ticker Nullable(String),
    published_at_utc DateTime64(6, 'UTC'),
    observed_at_utc DateTime64(3, 'UTC'),
    catalyst_group LowCardinality(String),
    catalyst_type LowCardinality(String),
    catalyst_subtype LowCardinality(String),
    direction LowCardinality(String),
    event_status LowCardinality(String),
    urgency_score Nullable(Float64),
    impact_score Nullable(Float64),
    confidence_score Nullable(Float64),
    labels Array(String),
    source_system LowCardinality(String),
    source_table String,
    source_event_id String,
    source_evidence_ref String,
    source_content_sha256 String,
    source_run_id String,
    inserted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(inserted_at)
PARTITION BY toYYYYMM(published_at_utc)
ORDER BY (ifNull(symbol_id, ''), published_at_utc, catalyst_group, news_catalyst_fact_id)
SETTINGS {settings}
""".strip(),
        f"""
CREATE TABLE IF NOT EXISTS {table(database, 'security_sec_filing_event_fact_v1')}
(
    filing_event_fact_id String,
    filing_id String,
    accession_number String,
    accession_number_compact String,
    cik String,
    issuer_id Nullable(String),
    security_id Nullable(String),
    listing_id Nullable(String),
    symbol_id Nullable(String),
    provider_ticker Nullable(String),
    form_type LowCardinality(String),
    filing_date Nullable(Date),
    report_date Nullable(Date),
    accepted_at_utc DateTime64(9, 'UTC'),
    event_group LowCardinality(String),
    event_type LowCardinality(String),
    event_status LowCardinality(String),
    text_status LowCardinality(String),
    confidence_score Nullable(Float64),
    source_system LowCardinality(String),
    source_table String,
    source_event_id String,
    source_evidence_ref String,
    source_content_sha256 String,
    source_run_id String,
    inserted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(inserted_at)
PARTITION BY toYYYYMM(accepted_at_utc)
ORDER BY (ifNull(symbol_id, ''), accepted_at_utc, form_type, filing_event_fact_id)
SETTINGS {settings}
""".strip(),
        f"""
CREATE TABLE IF NOT EXISTS {table(database, 'security_sec_text_signal_fact_v1')}
(
    text_signal_fact_id String,
    document_id String,
    filing_id String,
    accession_number String,
    accession_number_compact String,
    cik String,
    issuer_id Nullable(String),
    security_id Nullable(String),
    listing_id Nullable(String),
    symbol_id Nullable(String),
    provider_ticker Nullable(String),
    accepted_at_utc Nullable(DateTime64(9, 'UTC')),
    extracted_at_utc DateTime64(3, 'UTC'),
    signal_group LowCardinality(String),
    signal_type LowCardinality(String),
    signal_subtype LowCardinality(String),
    direction LowCardinality(String),
    event_status LowCardinality(String),
    confidence_score Nullable(Float64),
    impact_score Nullable(Float64),
    evidence_span_start Nullable(UInt64),
    evidence_span_end Nullable(UInt64),
    evidence_text_sha256 Nullable(String),
    extraction_method LowCardinality(String),
    normalizer_version LowCardinality(String),
    labels Array(String),
    source_system LowCardinality(String),
    source_table String,
    source_event_id String,
    source_evidence_ref String,
    source_content_sha256 String,
    source_run_id String,
    inserted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(inserted_at)
PARTITION BY toYYYYMM(extracted_at_utc)
ORDER BY (ifNull(symbol_id, ''), extracted_at_utc, signal_group, text_signal_fact_id)
SETTINGS {settings}
""".strip(),
        f"""
CREATE TABLE IF NOT EXISTS {table(database, 'issuer_fundamental_metric_fact_v1')}
(
    fundamental_metric_fact_id String,
    issuer_id Nullable(String),
    cik String,
    metric_group LowCardinality(String),
    metric_name LowCardinality(String),
    unit_code LowCardinality(String),
    value Float64,
    fiscal_year Nullable(UInt32),
    fiscal_period Nullable(String),
    period_end_date Nullable(Date),
    filed_at_utc Nullable(DateTime64(3, 'UTC')),
    recorded_at_utc DateTime64(3, 'UTC'),
    form_type Nullable(String),
    accession_number Nullable(String),
    source_priority UInt16,
    confidence_score Nullable(Float64),
    xbrl_taxonomy Nullable(String),
    xbrl_tag Nullable(String),
    source_system LowCardinality(String),
    source_table String,
    source_event_id String,
    source_evidence_ref String,
    source_content_sha256 String,
    source_run_id String,
    inserted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(inserted_at)
PARTITION BY toYYYYMM(ifNull(period_end_date, toDate('1970-01-01')))
ORDER BY (cik, metric_group, metric_name, ifNull(period_end_date, toDate('1970-01-01')), source_priority, fundamental_metric_fact_id)
SETTINGS {settings}
""".strip(),
        f"""
CREATE TABLE IF NOT EXISTS {table(database, 'security_valuation_fact_v1')}
(
    valuation_fact_id String,
    issuer_id Nullable(String),
    security_id Nullable(String),
    listing_id Nullable(String),
    symbol_id Nullable(String),
    provider_ticker Nullable(String),
    valuation_metric LowCardinality(String),
    value Float64,
    currency_code Nullable(String),
    as_of_utc DateTime64(3, 'UTC'),
    input_snapshot_id Nullable(String),
    input_fundamental_metric_ids Array(String),
    confidence_score Nullable(Float64),
    source_system LowCardinality(String),
    source_table String,
    source_event_id String,
    source_evidence_ref String,
    source_content_sha256 String,
    source_run_id String,
    inserted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(inserted_at)
PARTITION BY toYYYYMM(as_of_utc)
ORDER BY (ifNull(symbol_id, ''), valuation_metric, as_of_utc, valuation_fact_id)
SETTINGS {settings}
""".strip(),
    )


def table(database: str, name: str) -> str:
    return f"{quote_ident(database)}.{quote_ident(name)}"
