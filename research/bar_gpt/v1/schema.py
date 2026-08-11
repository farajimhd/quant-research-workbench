from __future__ import annotations

from pipelines.market_sip.events.session_bar_contract import (
    FEATURE_SPECS as SESSION_FEATURE_SPECS,
    FeatureSpec,
)


SCHEMA_VERSION = 5
FEATURE_VERSION = "bar_gpt_direct_events_trade_sparse_v5"
ONE_SECOND_US = 1_000_000
SESSION_TIMEZONE = "America/New_York"
SESSION_START_SECOND = 4 * 60 * 60
SESSION_END_SECOND = 20 * 60 * 60


def _bar_gpt_spec(spec: FeatureSpec) -> FeatureSpec:
    """Give trade fields their own causal condition eligibility stream.

    The stored column set stays compact. A zero trade field is authoritative
    absence for that purpose: ``trade_present`` means that at least one trade
    may update high/low or last, while the field itself certifies whether open,
    extrema, close, or volume was eligible.  This lets fixed-bucket rollups
    preserve the database condition categories without adding Python row work.
    """
    validity = spec.validity
    if spec.name == "trade_open":
        validity = "trade_open"
    elif spec.name in {"trade_high", "trade_low"}:
        validity = spec.name
    elif spec.name == "trade_close":
        validity = "trade_close"
    elif spec.name.startswith("trade_size_") or spec.name in {
        "trade_price_size_sum", "trade_event_count",
    }:
        validity = "trade_event_count"
    return FeatureSpec(spec.name, spec.clickhouse_type, spec.reducer, validity)


FEATURE_SPECS: tuple[FeatureSpec, ...] = (
    *(_bar_gpt_spec(spec) for spec in SESSION_FEATURE_SPECS),
    # Separate denominator for price*size. Volume-only condition categories
    # remain in total volume/count without leaking their non-price-authoritative
    # prints into the VWAP input at any rollup level.
    FeatureSpec("trade_price_eligible_size_sum", "Float64", "sum"),
    FeatureSpec("context_eligible", "UInt8", "max"),
    FeatureSpec("origin_eligible", "UInt8", "max"),
    FeatureSpec("origin_event_count", "UInt64", "sum"),
    FeatureSpec("eligible_trade_event_count", "UInt64", "sum"),
    FeatureSpec("eligible_quote_event_count", "UInt64", "sum"),
    FeatureSpec("rejected_trade_event_count", "UInt64", "sum"),
    FeatureSpec("rejected_quote_event_count", "UInt64", "sum"),
    FeatureSpec("unknown_condition_event_count", "UInt64", "sum"),
    FeatureSpec("condition_halt_pause_count", "UInt64", "sum"),
    FeatureSpec("condition_resume_count", "UInt64", "sum"),
    FeatureSpec("condition_news_risk_count", "UInt64", "sum"),
    FeatureSpec("condition_luld_limit_state_count", "UInt64", "sum"),
    FeatureSpec("condition_event_count", "UInt64", "sum"),
)
FEATURE_NAMES: tuple[str, ...] = tuple(spec.name for spec in FEATURE_SPECS)
FEATURE_INDEX: dict[str, int] = {name: index for index, name in enumerate(FEATURE_NAMES)}


IDENTITY_COLUMNS: tuple[tuple[str, str], ...] = (
    ("schema_version", "UInt16"),
    ("feature_version", "LowCardinality(String)"),
    ("local_date", "Date"),
    ("ticker", "LowCardinality(String)"),
    ("bucket_index", "UInt64"),
    ("bar_start_us", "UInt64"),
    ("bar_end_us", "UInt64"),
    ("available_at_us", "UInt64"),
    ("source_first_ordinal", "UInt64"),
    ("source_last_ordinal", "UInt64"),
    ("source_first_timestamp_us", "UInt64"),
    ("source_last_timestamp_us", "UInt64"),
)


def table_columns() -> tuple[tuple[str, str], ...]:
    return (*IDENTITY_COLUMNS, *((spec.name, spec.clickhouse_type) for spec in FEATURE_SPECS), ("built_at", "DateTime64(3, 'UTC')"))
