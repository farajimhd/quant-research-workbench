from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Iterable, Mapping

from research.mlops.clickhouse import parse_size_bytes, quote_ident, sql_string
from research.mlops.data.contracts import BAR_FAMILY_FEATURE_KEYS, BAR_FAMILY_KEYS
from research.mlops.rolling_loader.daily_index_context import query_polars

SESSION_TIMEZONE = "America/New_York"
SESSION_START_US = 4 * 60 * 60 * 1_000_000
SESSION_END_US = 20 * 60 * 60 * 1_000_000
DEFAULT_SCANNER_GROUPS = (
    "top_gainers",
    "top_volume_large_cap",
    "top_volume_mid_cap",
    "top_volume_small_cap",
    "top_volume_penny",
)
DEFAULT_SCANNER_HORIZONS = ("1s", "5s", "30s", "1m")


@dataclass(frozen=True, slots=True)
class DirectScannerQueryConfig:
    database: str = "market_sip_compact"
    events_table_base: str = "events"
    scanner_resolution_us: int = 1_000_000
    horizons: tuple[str, ...] = DEFAULT_SCANNER_HORIZONS
    max_threads: int = 4
    max_memory_usage: str = "32G"


def query_direct_market_scanner_frames(
    *,
    client_opts: Mapping[str, str],
    config: DirectScannerQueryConfig,
    local_dates: Iterable[str],
) -> Any:
    dates = tuple(sorted({str(item)[:10] for item in local_dates if str(item).strip()}))
    if not dates:
        return _empty_polars_frame()
    query = direct_market_scanner_sql(config=config, local_dates=dates)
    return query_polars(client_opts, query)


def direct_market_scanner_sql(*, config: DirectScannerQueryConfig, local_dates: tuple[str, ...]) -> str:
    table = events_source_table(config.database, config.events_table_base, local_dates)
    date_sql = ", ".join(f"toDate({sql_string(item)})" for item in local_dates)
    min_event_date = min(date.fromisoformat(item) for item in local_dates)
    max_event_date = max(date.fromisoformat(item) for item in local_dates) + timedelta(days=1)
    scanner_resolution_us = max(1, int(config.scanner_resolution_us))
    resolutions = sorted({scanner_resolution_us, *(_intraday_resolution_us(horizon) for horizon in config.horizons)})
    resolution_sql = ", ".join(str(int(item)) for item in resolutions)
    final_columns = [
        "b.source_date",
        "b.ticker",
        "b.ticker_id",
        "b.scanner_bucket",
        "b.scanner_timestamp_us",
        f"toInt64({scanner_resolution_us}) AS scanner_resolution_us",
        "ifNull(g.top_gainers_rank, -1) AS top_gainers_rank",
        "ifNull(g.top_gainers_score, 0.0) AS top_gainers_score",
        "ifNull(g.top_gainers_percentile, 0.0) AS top_gainers_percentile",
    ]
    for group_name in ("top_volume_large_cap", "top_volume_mid_cap", "top_volume_small_cap"):
        final_columns.extend(
            [
                f"ifNull(v.top_volume_rank, -1) AS {group_name}_rank",
                f"ifNull(v.top_volume_score, 0.0) AS {group_name}_score",
                f"ifNull(v.top_volume_percentile, 0.0) AS {group_name}_percentile",
            ]
        )
    final_columns.extend(
        [
            "ifNull(p.top_volume_penny_rank, -1) AS top_volume_penny_rank",
            "ifNull(p.top_volume_penny_score, 0.0) AS top_volume_penny_score",
            "ifNull(p.top_volume_penny_percentile, 0.0) AS top_volume_penny_percentile",
        ]
    )
    horizon_joins: list[str] = []
    for horizon in config.horizons:
        token = _scanner_column_token(horizon)
        resolution_us = _intraday_resolution_us(horizon)
        first_family = True
        for family in BAR_FAMILY_KEYS:
            alias = f"h_{family}_{token}".replace("-", "_")
            join_bucket_expr = (
                f"greatest(intDiv((b.scanner_bucket + 1) * toInt64({scanner_resolution_us}), "
                f"toInt64({resolution_us})) - 1, toInt64({SESSION_START_US // int(resolution_us)}))"
            )
            horizon_joins.append(
                f"""
LEFT JOIN bars AS {alias}
    ON {alias}.source_date = b.source_date
   AND {alias}.ticker = b.ticker
   AND {alias}.resolution_us = toInt64({resolution_us})
   AND {alias}.bar_family = {sql_string(family)}
   AND {alias}.bucket_index = {join_bucket_expr}
"""
            )
            if first_family:
                final_columns.append(f"ifNull({alias}.last_event_timestamp_us, b.scanner_timestamp_us) AS {quote_ident(token + '_timestamp_us')}")
                first_family = False
            for feature in BAR_FAMILY_FEATURE_KEYS[family]:
                final_columns.append(f"toFloat32(ifNull({alias}.{quote_ident(feature)}, 0.0)) AS {quote_ident(family + '_' + token + '_' + feature)}")
            final_columns.append(f"toUInt8({alias}.event_count > 0) AS {quote_ident(family + '_' + token + '_available')}")

    final_sql = ",\n    ".join(final_columns)
    joins_sql = "\n".join(horizon_joins)
    return f"""
WITH
raw AS
(
    SELECT *
    FROM
    (
        SELECT
            ticker,
            cityHash64(ticker) AS ticker_id,
            ordinal,
            sip_timestamp_us,
            bitAnd(event_meta, 1) AS event_type,
            toDate(toTimeZone(fromUnixTimestamp64Micro(sip_timestamp_us, 'UTC'), {sql_string(SESSION_TIMEZONE)})) AS source_date,
            toUInt64(dateDiff('microsecond', toStartOfDay(toTimeZone(fromUnixTimestamp64Micro(sip_timestamp_us, 'UTC'), {sql_string(SESSION_TIMEZONE)})), toTimeZone(fromUnixTimestamp64Micro(sip_timestamp_us, 'UTC'), {sql_string(SESSION_TIMEZONE)}))) AS local_session_us,
            toFloat64(price_primary_int) / if(bitAnd(event_meta, 2) > 0, 10000.0, 100.0) AS primary_price,
            toFloat64(price_secondary_int) / if(bitAnd(event_meta, 4) > 0, 10000.0, 100.0) AS secondary_price,
            toFloat64(size_primary) AS size_primary,
            toFloat64(size_secondary) AS size_secondary
        FROM {table}
        PREWHERE event_date >= toDate({sql_string(min_event_date.isoformat())})
            AND event_date <= toDate({sql_string(max_event_date.isoformat())})
    )
    WHERE source_date IN ({date_sql})
      AND local_session_us >= toUInt64({SESSION_START_US})
      AND local_session_us < toUInt64({SESSION_END_US})
      AND ticker != ''
),
expanded AS
(
    SELECT
        ticker,
        ticker_id,
        source_date,
        sip_timestamp_us,
        ordinal,
        local_session_us,
        'trade' AS bar_family,
        primary_price AS price,
        size_primary AS size
    FROM raw
    WHERE event_type = 1 AND primary_price > 0
    UNION ALL
    SELECT ticker, ticker_id, source_date, sip_timestamp_us, ordinal, local_session_us, 'quote_bid' AS bar_family, secondary_price AS price, size_secondary AS size
    FROM raw
    WHERE event_type = 0 AND secondary_price > 0
    UNION ALL
    SELECT ticker, ticker_id, source_date, sip_timestamp_us, ordinal, local_session_us, 'quote_ask' AS bar_family, primary_price AS price, size_primary AS size
    FROM raw
    WHERE event_type = 0 AND primary_price > 0
),
bar_events AS
(
    SELECT *
    FROM
    (
        SELECT
            e.*,
            toInt64(resolution_us) AS resolution_us,
            intDiv(toInt64(local_session_us), toInt64(resolution_us)) AS bucket_index
        FROM expanded AS e
        ARRAY JOIN [{resolution_sql}] AS resolution_us
    )
    WHERE bucket_index >= toInt64({SESSION_START_US}) / resolution_us
      AND bucket_index < toInt64({SESSION_END_US}) / resolution_us
),
bars AS
(
    SELECT
        source_date,
        ticker,
        ticker_id,
        resolution_us,
        bucket_index,
        bar_family,
        toFloat32(argMin(price, tuple(sip_timestamp_us, ordinal))) AS open,
        toFloat32(argMax(price, tuple(sip_timestamp_us, ordinal))) AS close,
        toFloat32(max(price)) AS high,
        toFloat32(min(price)) AS low,
        toFloat32(sum(size)) AS size_sum,
        toFloat32(argMin(size, tuple(sip_timestamp_us, ordinal))) AS size_open,
        toFloat32(argMax(size, tuple(sip_timestamp_us, ordinal))) AS size_close,
        toFloat32(max(size)) AS size_high,
        toFloat32(min(size)) AS size_low,
        toUInt32(count()) AS event_count,
        toInt64(min(sip_timestamp_us)) AS first_event_timestamp_us,
        toInt64(max(sip_timestamp_us)) AS last_event_timestamp_us
    FROM bar_events
    GROUP BY source_date, ticker, ticker_id, resolution_us, bucket_index, bar_family
),
scanner_trade AS
(
    SELECT
        source_date,
        ticker,
        ticker_id,
        bucket_index AS scanner_bucket,
        last_event_timestamp_us AS scanner_timestamp_us,
        close,
        toFloat32(if(first_value(open) OVER (PARTITION BY source_date, ticker ORDER BY bucket_index) > 0,
            close / first_value(open) OVER (PARTITION BY source_date, ticker ORDER BY bucket_index) - 1.0,
            0.0)) AS change_score,
        size_sum AS volume_score
    FROM bars
    WHERE resolution_us = toInt64({scanner_resolution_us})
      AND bar_family = 'trade'
),
gainers AS
(
    SELECT
        source_date,
        ticker,
        scanner_bucket,
        toInt32(row_number() OVER (PARTITION BY source_date, scanner_bucket ORDER BY change_score DESC, ticker ASC) - 1) AS top_gainers_rank,
        toFloat32(change_score) AS top_gainers_score,
        toFloat32(if(count() OVER (PARTITION BY source_date, scanner_bucket) > 1,
            1.0 - ((row_number() OVER (PARTITION BY source_date, scanner_bucket ORDER BY change_score DESC, ticker ASC) - 1) / (count() OVER (PARTITION BY source_date, scanner_bucket) - 1)),
            1.0)) AS top_gainers_percentile
    FROM scanner_trade
),
volume_nonpenny AS
(
    SELECT
        source_date,
        ticker,
        scanner_bucket,
        toInt32(row_number() OVER (PARTITION BY source_date, scanner_bucket ORDER BY volume_score DESC, ticker ASC) - 1) AS top_volume_rank,
        toFloat32(volume_score) AS top_volume_score,
        toFloat32(if(count() OVER (PARTITION BY source_date, scanner_bucket) > 1,
            1.0 - ((row_number() OVER (PARTITION BY source_date, scanner_bucket ORDER BY volume_score DESC, ticker ASC) - 1) / (count() OVER (PARTITION BY source_date, scanner_bucket) - 1)),
            1.0)) AS top_volume_percentile
    FROM scanner_trade
    WHERE close >= 1.0
),
volume_penny AS
(
    SELECT
        source_date,
        ticker,
        scanner_bucket,
        toInt32(row_number() OVER (PARTITION BY source_date, scanner_bucket ORDER BY volume_score DESC, ticker ASC) - 1) AS top_volume_penny_rank,
        toFloat32(volume_score) AS top_volume_penny_score,
        toFloat32(if(count() OVER (PARTITION BY source_date, scanner_bucket) > 1,
            1.0 - ((row_number() OVER (PARTITION BY source_date, scanner_bucket ORDER BY volume_score DESC, ticker ASC) - 1) / (count() OVER (PARTITION BY source_date, scanner_bucket) - 1)),
            1.0)) AS top_volume_penny_percentile
    FROM scanner_trade
    WHERE close < 1.0
)
SELECT
    {final_sql}
FROM scanner_trade AS b
LEFT JOIN gainers AS g ON g.source_date = b.source_date AND g.ticker = b.ticker AND g.scanner_bucket = b.scanner_bucket
LEFT JOIN volume_nonpenny AS v ON v.source_date = b.source_date AND v.ticker = b.ticker AND v.scanner_bucket = b.scanner_bucket
LEFT JOIN volume_penny AS p ON p.source_date = b.source_date AND p.ticker = b.ticker AND p.scanner_bucket = b.scanner_bucket
{joins_sql}
ORDER BY source_date, scanner_bucket, ticker
{_settings_sql(config)}
"""


def events_source_table(database: str, events_table_base: str, local_dates: tuple[str, ...]) -> str:
    years = sorted({int(item[:4]) for item in local_dates})
    if len(years) == 1:
        return f"{quote_ident(database)}.{quote_ident(f'{events_table_base}_{years[0]}')}"
    pattern = "^(" + "|".join(f"{events_table_base}_{year}" for year in years) + ")$"
    return f"merge({sql_string(database)}, {sql_string(pattern)})"


def _intraday_resolution_us(horizon: str) -> int:
    horizon_us = _duration_us(horizon)
    if horizon_us <= 60_000_000:
        return 100_000
    if horizon_us <= 900_000_000:
        return 1_000_000
    if horizon_us <= 3_600_000_000:
        return 5_000_000
    if horizon_us <= 10_800_000_000:
        return 30_000_000
    return 60_000_000


def _duration_us(value: str) -> int:
    text = str(value).strip().lower().replace(" ", "")
    units = (
        ("ms", 1_000),
        ("us", 1),
        ("s", 1_000_000),
        ("m", 60_000_000),
        ("h", 3_600_000_000),
    )
    for suffix, scale in units:
        if text.endswith(suffix):
            return int(float(text[: -len(suffix)]) * scale)
    raise ValueError(f"Invalid scanner horizon {value!r}")


def _scanner_column_token(value: str) -> str:
    return str(value).strip().lower().replace("-", "_").replace(" ", "_")


def _settings_sql(config: DirectScannerQueryConfig) -> str:
    settings: dict[str, Any] = {}
    if int(config.max_threads) > 0:
        settings["max_threads"] = int(config.max_threads)
    if str(config.max_memory_usage) != "0":
        settings["max_memory_usage"] = parse_size_bytes(str(config.max_memory_usage))
    if not settings:
        return ""
    parts = []
    for key, value in settings.items():
        if isinstance(value, str):
            parts.append(f"{key} = {sql_string(value)}")
        else:
            parts.append(f"{key} = {value}")
    return "SETTINGS " + ", ".join(parts)


def _empty_polars_frame() -> Any:
    try:
        import polars as pl  # type: ignore
    except ModuleNotFoundError as exc:
        raise RuntimeError("Install polars to query direct market scanner frames.") from exc
    return pl.DataFrame()
