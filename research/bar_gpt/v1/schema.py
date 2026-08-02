from __future__ import annotations

from pipelines.market_sip.events.session_bar_contract import (
    FEATURE_INDEX,
    FEATURE_NAMES,
    FEATURE_SPECS,
    FeatureSpec,
)


SCHEMA_VERSION = 1
FEATURE_VERSION = "bar_gpt_1s_sufficient_stats_v1"
ONE_SECOND_US = 1_000_000
SESSION_TIMEZONE = "America/New_York"
SESSION_START_SECOND = 4 * 60 * 60
SESSION_END_SECOND = 20 * 60 * 60


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
