from __future__ import annotations

from dataclasses import dataclass


SCHEMA_VERSION = 1
FEATURE_VERSION = "bar_gpt_1s_sufficient_stats_v1"
ONE_SECOND_US = 1_000_000
SESSION_TIMEZONE = "America/New_York"
SESSION_START_SECOND = 4 * 60 * 60
SESSION_END_SECOND = 20 * 60 * 60


@dataclass(frozen=True, slots=True)
class FeatureSpec:
    name: str
    clickhouse_type: str
    reducer: str
    validity: str = "always"


def _family_specs(prefix: str) -> tuple[FeatureSpec, ...]:
    validity = f"{prefix}_present"
    return (
        FeatureSpec(validity, "UInt8", "max"),
        FeatureSpec(f"{prefix}_open", "Float32", "first", validity),
        FeatureSpec(f"{prefix}_high", "Float32", "max", validity),
        FeatureSpec(f"{prefix}_low", "Float32", "min", validity),
        FeatureSpec(f"{prefix}_close", "Float32", "last", validity),
        FeatureSpec(f"{prefix}_size_sum", "Float64", "sum"),
        FeatureSpec(f"{prefix}_size_open", "Float64", "first", validity),
        FeatureSpec(f"{prefix}_size_high", "Float64", "max", validity),
        FeatureSpec(f"{prefix}_size_low", "Float64", "min", validity),
        FeatureSpec(f"{prefix}_size_close", "Float64", "last", validity),
        FeatureSpec(f"{prefix}_size_squared_sum", "Float64", "sum"),
        FeatureSpec(f"{prefix}_price_size_sum", "Float64", "sum"),
        FeatureSpec(f"{prefix}_event_count", "UInt64", "sum"),
    )


def _relation_specs(prefix: str) -> tuple[FeatureSpec, ...]:
    validity = "quote_pair_present"
    return (
        FeatureSpec(f"{prefix}_open", "Float32", "first", validity),
        FeatureSpec(f"{prefix}_high", "Float32", "max", validity),
        FeatureSpec(f"{prefix}_low", "Float32", "min", validity),
        FeatureSpec(f"{prefix}_close", "Float32", "last", validity),
        FeatureSpec(f"{prefix}_sum", "Float64", "sum"),
        FeatureSpec(f"{prefix}_squared_sum", "Float64", "sum"),
    )


FEATURE_SPECS: tuple[FeatureSpec, ...] = (
    *_family_specs("trade"),
    *_family_specs("bid"),
    *_family_specs("ask"),
    FeatureSpec("quote_pair_present", "UInt8", "max"),
    FeatureSpec("quote_pair_count", "UInt64", "sum"),
    *_relation_specs("spread"),
    *_relation_specs("midpoint"),
    *_relation_specs("microprice"),
    *_relation_specs("queue_imbalance"),
    FeatureSpec("locked_quote_count", "UInt64", "sum"),
    FeatureSpec("crossed_quote_count", "UInt64", "sum"),
    FeatureSpec("condition_nonzero_count", "UInt64", "sum"),
    FeatureSpec("source_event_count", "UInt64", "sum"),
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
