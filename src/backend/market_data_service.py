from __future__ import annotations

import re
from dataclasses import asdict
from datetime import date, datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import polars as pl

from src.backend.json_utils import json_safe
from src.data_provider.calendar import discover_raw_bounds, market_sessions, scan_market_source
from src.data_provider.config import (
    DEFAULT_PROCESSED_ROOT,
    DEFAULT_RAW_ROOT,
    EXCHANGE_TIME_ZONE,
    FEATURE_GROUPS,
    SUPERVISION_GROUPS,
    TIMEFRAMES,
    DataProviderConfig,
)
from src.data_provider.manifest import read_manifest
from src.data_provider.provider import MarketDataProvider


CHART_FEATURE_EXCLUDE_COLUMNS = {
    "bar_id",
    "ticker",
    "timeframe",
    "bar_time_utc",
    "bar_time_market",
    "session_date",
    "session_month",
    "minute_of_day",
    "window_start",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "transactions",
}

PRICE_CHART_INDICATORS = ["vwap", "tema9", "tema20"]
OSCILLATOR_CHART_INDICATORS = ["macd_line", "macd_signal", "macd_hist"]
CHART_INDICATORS = PRICE_CHART_INDICATORS + OSCILLATOR_CHART_INDICATORS
INDICATOR_DISPLAY_NAMES = {
    "vwap": "VWAP",
    "tema9": "TEMA9",
    "tema20": "TEMA20",
    "macd_line": "MACD LINE",
    "macd_signal": "MACD SIGNAL",
    "macd_hist": "MACD HIST",
}
INDICATOR_COLORS = {
    "vwap": "#5B21B6",
    "tema9": "#2563EB",
    "tema20": "#B7791F",
    "macd_line": "#1E3A5F",
    "macd_signal": "#B54708",
    "macd_hist": "#33E42A",
}
DYNAMIC_COLORS = ["#1E3A5F", "#B7791F", "#067647", "#B42318", "#2563EB", "#7C3AED", "#0E7490", "#C2410C"]


def timeframe_sort_key(value: str) -> tuple[int, str]:
    order = {timeframe: index for index, timeframe in enumerate(TIMEFRAMES)}
    return order.get(value, 999), value


def format_bytes(value: int | float | None) -> str:
    size = float(value or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:,.0f} {unit}" if unit == "B" else f"{size:,.1f} {unit}"
        size /= 1024
    return f"{size:,.1f} TB"


def display_name(value: str) -> str:
    overrides = {"macd": "MACD", "orb": "ORB", "vwap": "VWAP", "rsi": "RSI", "atr": "ATR"}
    parts = value.replace("-", "_").split("_")
    return " ".join(overrides.get(part.lower(), part.upper() if len(part) <= 3 else part.title()) for part in parts)


def artifact_records(root: Path) -> list[dict[str, Any]]:
    manifest = read_manifest(root)
    records = []
    for key, record in manifest.get("artifacts", {}).items():
        path = Path(str(record.get("path") or ""))
        size_bytes = path.stat().st_size if path.exists() else 0
        records.append(
            {
                "key": key,
                "group": record.get("group"),
                "timeframe": record.get("timeframe"),
                "session_date": record.get("session_date"),
                "path": str(path),
                "exists": path.exists(),
                "rows": int(record.get("rows") or 0),
                "columns": list(record.get("columns") or []),
                "column_count": len(record.get("columns") or []),
                "built_at": record.get("built_at"),
                "schema_version": record.get("schema_version"),
                "feature_version": record.get("feature_version"),
                "supervision_version": record.get("supervision_version"),
                "source_path": record.get("source_path"),
                "source_size_bytes": record.get("source_size_bytes"),
                "size_bytes": size_bytes,
                "size": format_bytes(size_bytes),
            }
        )
    return sorted(records, key=lambda row: (str(row.get("group")), timeframe_sort_key(str(row.get("timeframe") or "")), str(row.get("session_date"))))


def scope_defaults(raw_root: Path = DEFAULT_RAW_ROOT, processed_root: Path = DEFAULT_PROCESSED_ROOT) -> dict[str, Any]:
    first_raw, last_raw, raw_count = discover_raw_bounds(raw_root)
    records = artifact_records(processed_root)
    dates = [date.fromisoformat(str(record["session_date"])) for record in records if record.get("session_date")]
    start = first_raw or (min(dates) if dates else date(2024, 5, 1))
    end = last_raw or (max(dates) if dates else date(2024, 5, 31))
    return {
        "raw_root": str(raw_root),
        "processed_root": str(processed_root),
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "raw_file_count": raw_count,
        "artifact_count": len(records),
        "force_rebuild": True,
    }


def summarize_records(records: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for record in records:
        value = str(record.get(key) or "-")
        row = grouped.setdefault(value, {key: value, "artifacts": 0, "rows": 0, "size_bytes": 0, "sessions": set(), "timeframes": set()})
        row["artifacts"] += 1
        row["rows"] += int(record.get("rows") or 0)
        row["size_bytes"] += int(record.get("size_bytes") or 0)
        if record.get("session_date"):
            row["sessions"].add(record["session_date"])
        if record.get("timeframe"):
            row["timeframes"].add(record["timeframe"])
    rows = []
    for value, row in grouped.items():
        rows.append(
            {
                key: value,
                "artifacts": row["artifacts"],
                "rows": row["rows"],
                "size": format_bytes(row["size_bytes"]),
                "sessions": len(row["sessions"]),
                "timeframes": len(row["timeframes"]),
            }
        )
    return sorted(rows, key=lambda row: str(row[key]))


def review_payload(processed_root: Path, start_date: date | None = None, end_date: date | None = None) -> dict[str, Any]:
    manifest = read_manifest(processed_root)
    records = artifact_records(processed_root)
    if start_date and end_date:
        records = [record for record in records if start_date.isoformat() <= str(record.get("session_date")) <= end_date.isoformat()]
    total_size = sum(int(record.get("size_bytes") or 0) for record in records)
    total_rows = sum(int(record.get("rows") or 0) for record in records)
    latest = sorted(records, key=lambda row: str(row.get("built_at") or ""), reverse=True)[:40]
    return {
        "processed_root": str(processed_root),
        "manifest": {
            "updated_at": manifest.get("updated_at"),
            "schema_version": manifest.get("schema_version"),
            "feature_version": manifest.get("feature_version"),
            "supervision_version": manifest.get("supervision_version"),
            "artifact_count": len(manifest.get("artifacts", {})),
        },
        "metrics": {
            "artifacts": len(records),
            "groups": len({record.get("group") for record in records}),
            "timeframes": len({record.get("timeframe") for record in records}),
            "sessions": len({record.get("session_date") for record in records}),
            "rows": total_rows,
            "size_bytes": total_size,
            "size": format_bytes(total_size),
        },
        "records": records,
        "group_summary": summarize_records(records, "group"),
        "timeframe_summary": summarize_records(records, "timeframe"),
        "latest": latest,
    }


def coverage_rows(records: list[dict[str, Any]], start_date: date, end_date: date, group: str) -> list[dict[str, Any]]:
    sessions = market_sessions(start_date, end_date)
    frames = sorted({str(record["timeframe"]) for record in records if record.get("group") == group}, key=timeframe_sort_key)
    by_key = {(record.get("session_date"), record.get("timeframe")): record for record in records if record.get("group") == group}
    rows = []
    for session in sessions:
        row = {"session_date": session.isoformat()}
        for timeframe in frames:
            record = by_key.get((session.isoformat(), timeframe))
            row[f"{timeframe}_status"] = "ready" if record and record.get("exists") else "missing"
            row[f"{timeframe}_rows"] = int(record.get("rows") or 0) if record else 0
        rows.append(row)
    return rows


def artifact_schema(record: dict[str, Any]) -> list[dict[str, str]]:
    path = Path(str(record.get("path") or ""))
    if not path.exists():
        return []
    schema = pl.scan_parquet(path).collect_schema()
    return [{"column": column, "dtype": str(dtype)} for column, dtype in schema.items()]


def load_artifact_sample(
    record: dict[str, Any],
    columns: list[str],
    row_limit: int,
    tickers: list[str],
    row_offset: int = 0,
    table_query: dict[str, Any] | None = None,
) -> dict[str, Any]:
    path = Path(str(record.get("path") or ""))
    if not path.exists():
        return {"columns": [], "row_count": 0, "row_limit": row_limit, "row_offset": row_offset, "rows": []}
    scan = pl.scan_parquet(path)
    schema = scan.collect_schema()
    schema_names = schema.names()
    if tickers and "ticker" in schema_names:
        scan = scan.filter(pl.col("ticker").is_in([ticker.upper() for ticker in tickers]))
    scan = apply_table_query(scan, schema, table_query)
    selected_columns = [column for column in columns if column in schema_names]
    row_count = int(scan.select(pl.len().alias("row_count")).collect().item(0, "row_count"))
    if selected_columns:
        scan = scan.select(selected_columns)
    row_offset = max(0, min(row_offset, row_count))
    row_limit = max(1, min(row_limit, 5000))
    scan = scan.slice(row_offset, row_limit)
    frame = scan.collect()
    sort_columns = [column for column in ["ticker", "bar_time_market", "bar_time_utc", "trade_method", "horizon_bars", "horizon"] if column in frame.columns]
    backend_sort_column = str((table_query or {}).get("sortColumn") or (table_query or {}).get("sort_column") or "")
    if sort_columns and not backend_sort_column:
        frame = frame.sort(sort_columns)
    return {
        "columns": frame.columns,
        "row_count": row_count,
        "row_limit": row_limit,
        "row_offset": row_offset,
        "rows": json_safe(frame.to_dicts()),
    }


def apply_table_query(scan: pl.LazyFrame, schema: pl.Schema, table_query: dict[str, Any] | None) -> pl.LazyFrame:
    if not table_query:
        return scan
    schema_by_name = dict(schema.items())
    conditions = table_query.get("conditions") if isinstance(table_query.get("conditions"), list) else []
    filters = [
        expression
        for expression in (build_table_query_expression(condition, schema_by_name) for condition in conditions)
        if expression is not None
    ]
    if filters:
        match_mode = str(table_query.get("matchMode") or table_query.get("match_mode") or "all").lower()
        combined = filters[0]
        for expression in filters[1:]:
            if match_mode == "any":
                combined = combined | expression
            else:
                combined = combined & expression
        scan = scan.filter(combined)

    sort_column = str(table_query.get("sortColumn") or table_query.get("sort_column") or "")
    if sort_column in schema_by_name:
        sort_direction = str(table_query.get("sortDirection") or table_query.get("sort_direction") or "asc").lower()
        scan = scan.sort(sort_column, descending=sort_direction == "desc")
    return scan


def build_table_query_expression(condition: Any, schema_by_name: dict[str, pl.DataType]) -> pl.Expr | None:
    if not isinstance(condition, dict):
        return None
    column = str(condition.get("column") or "")
    if column not in schema_by_name:
        return None
    operator = str(condition.get("operator") or "contains").lower()
    dtype = schema_by_name[column]
    column_expr = pl.col(column)
    value = condition.get("value")
    value_secondary = condition.get("valueSecondary", condition.get("value_secondary"))

    if operator == "is_null":
        return column_expr.is_null()
    if operator == "is_not_null":
        return column_expr.is_not_null()

    if operator in {"contains", "starts_with", "ends_with"}:
        text = str(value or "")
        if not text:
            return None
        text_expr = column_expr.cast(pl.String).str.to_lowercase()
        text_value = text.lower()
        if operator == "contains":
            return text_expr.str.contains(re.escape(text_value))
        if operator == "starts_with":
            return text_expr.str.starts_with(text_value)
        return text_expr.str.ends_with(text_value)

    coerced_value = coerce_table_query_value(value, dtype)
    if coerced_value is None:
        return None

    if operator == "eq":
        return column_expr == pl.lit(coerced_value)
    if operator == "ne":
        return column_expr != pl.lit(coerced_value)
    if operator == "gt":
        return column_expr > pl.lit(coerced_value)
    if operator == "gte":
        return column_expr >= pl.lit(coerced_value)
    if operator == "lt":
        return column_expr < pl.lit(coerced_value)
    if operator == "lte":
        return column_expr <= pl.lit(coerced_value)
    if operator == "between":
        coerced_secondary = coerce_table_query_value(value_secondary, dtype)
        if coerced_secondary is None:
            return None
        lower, upper = sorted([coerced_value, coerced_secondary])
        return (column_expr >= pl.lit(lower)) & (column_expr <= pl.lit(upper))
    return None


def coerce_table_query_value(value: Any, dtype: pl.DataType) -> Any:
    if value is None:
        return None
    if is_boolean_dtype(dtype):
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "y"}:
            return True
        if text in {"0", "false", "no", "n"}:
            return False
        return None
    if is_temporal_dtype(dtype):
        text = str(value).strip()
        if not text:
            return None
        try:
            if dtype == pl.Date:
                return date.fromisoformat(text[:10])
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if is_numeric_dtype(dtype):
        text = str(value).replace(",", "").strip()
        if not text:
            return None
        try:
            if dtype in INTEGER_DTYPES:
                return int(float(text))
            return float(text)
        except ValueError:
            return None
    text = str(value)
    return text if text else None


INTEGER_DTYPES = {
    pl.Int8,
    pl.Int16,
    pl.Int32,
    pl.Int64,
    pl.UInt8,
    pl.UInt16,
    pl.UInt32,
    pl.UInt64,
}


def is_numeric_dtype(dtype: pl.DataType) -> bool:
    checker = getattr(dtype, "is_numeric", None)
    return bool(checker()) if callable(checker) else dtype in INTEGER_DTYPES or dtype in {pl.Float32, pl.Float64}


def is_temporal_dtype(dtype: pl.DataType) -> bool:
    checker = getattr(dtype, "is_temporal", None)
    return bool(checker()) if callable(checker) else dtype == pl.Date or "Datetime" in str(dtype)


def is_boolean_dtype(dtype: pl.DataType) -> bool:
    return dtype == pl.Boolean


def first_matching_artifact(records: list[dict[str, Any]], group: str, timeframe: str, session: str) -> dict[str, Any] | None:
    return next((record for record in records if record.get("group") == group and record.get("timeframe") == timeframe and record.get("session_date") == session), None)


def matching_artifacts(records: list[dict[str, Any]], group: str, timeframe: str, start: date, end: date) -> list[dict[str, Any]]:
    start_key = start.isoformat()
    end_key = end.isoformat()
    return [
        record
        for record in records
        if record.get("group") == group
        and record.get("timeframe") == timeframe
        and start_key <= str(record.get("session_date") or "") <= end_key
        and record.get("exists")
    ]


def first_ticker(record: dict[str, Any] | None) -> str:
    if not record:
        return ""
    path = Path(str(record.get("path") or ""))
    if not path.exists():
        return ""
    try:
        frame = pl.scan_parquet(path).select("ticker").drop_nulls().limit(1).collect()
    except Exception:
        return ""
    return "" if frame.is_empty() else str(frame.item(0, "ticker")).upper()


def first_ticker_in_range(records: list[dict[str, Any]], timeframe: str, start: date, end: date) -> str:
    for record in matching_artifacts(records, "bars", timeframe, start, end):
        ticker = first_ticker(record)
        if ticker:
            return ticker
    return ""


def feature_group_options(records: list[dict[str, Any]], timeframe: str, start: date, end: date) -> list[str]:
    start_key = start.isoformat()
    end_key = end.isoformat()
    groups = [
        str(record["group"]).replace("features_", "", 1)
        for record in records
        if str(record.get("group") or "").startswith("features_")
        and record.get("timeframe") == timeframe
        and start_key <= str(record.get("session_date") or "") <= end_key
        and record.get("exists")
    ]
    return sorted(set(groups), key=lambda group: FEATURE_GROUPS.index(group) if group in FEATURE_GROUPS else 999)


def is_numeric_dtype(dtype: Any) -> bool:
    return str(dtype).startswith(("Float", "Int", "UInt"))


def chart_feature_columns(records: list[dict[str, Any]], timeframe: str, start: date, end: date, groups: list[str]) -> list[str]:
    columns = []
    for group in groups:
        for record in matching_artifacts(records, f"features_{group}", timeframe, start, end):
            for item in artifact_schema(record):
                if item["column"] not in CHART_FEATURE_EXCLUDE_COLUMNS and is_numeric_dtype(item["dtype"]):
                    columns.append(item["column"])
    return sorted(set(columns))


def chart_pane_for_column(column: str) -> str:
    lower = column.lower()
    price_terms = ("sma", "ema", "tema", "vwap", "bb_", "donchian", "keltner", "hvn", "lvn", "price_proxy")
    return "price" if any(term in lower for term in price_terms) else "oscillator"


def indicator_settings(selected_columns: list[str]) -> dict[str, dict[str, Any]]:
    settings = {}
    for index, column in enumerate(selected_columns):
        if column in CHART_INDICATORS:
            settings[column] = {
                "color": INDICATOR_COLORS[column],
                "lineWidth": 2 if column == "vwap" else 1,
                "opacity": 0.35 if column == "vwap" else 0.75,
                "pane": "oscillator" if column in OSCILLATOR_CHART_INDICATORS else "price",
                "style": "histogram" if column == "macd_hist" else "line",
                "label": INDICATOR_DISPLAY_NAMES.get(column, display_name(column)),
            }
        else:
            pane = chart_pane_for_column(column)
            settings[column] = {
                "color": DYNAMIC_COLORS[index % len(DYNAMIC_COLORS)],
                "lineWidth": 1,
                "opacity": 0.72 if pane == "price" else 0.82,
                "pane": pane,
                "style": "line",
                "label": display_name(column),
            }
    return settings


def timestamp_seconds(value: Any, timezone_name: str = EXCHANGE_TIME_ZONE) -> int | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo(timezone_name))
    return int(dt.timestamp())


def timeframe_minutes(timeframe: str) -> int | None:
    value = TIMEFRAMES.get(timeframe)
    return value if isinstance(value, int) else None


def session_region_timestamp(session: str, minute_of_day: int) -> int:
    hour, minute = divmod(minute_of_day, 60)
    dt = datetime.combine(date.fromisoformat(session), time(hour=hour, minute=minute), tzinfo=ZoneInfo(EXCHANGE_TIME_ZONE))
    return int(dt.timestamp())


def chart_timestamp_seconds(row: dict[str, Any], timeframe: str) -> int | None:
    minutes = timeframe_minutes(timeframe)
    session = row.get("session_date")
    minute_of_day = row.get("minute_of_day")
    if minutes and session and minute_of_day is not None:
        try:
            minute = int(float(minute_of_day))
        except (TypeError, ValueError):
            minute = -1
        if 0 <= minute < 24 * 60:
            bucket_minute = (minute // minutes) * minutes if minutes > 1 else minute
            return session_region_timestamp(str(session), bucket_minute)
    return timestamp_seconds(row.get("bar_time_market") or row.get("bar_time_utc"))


def extended_session_regions(rows: list[dict[str, Any]], timeframe: str) -> list[dict[str, Any]]:
    if timeframe_minutes(timeframe) is None:
        return []
    sessions = sorted({str(row.get("session_date")) for row in rows if row.get("session_date")})
    regions = []
    for session in sessions:
        regions.extend(
            [
                {
                    "start": session_region_timestamp(session, 4 * 60),
                    "end": session_region_timestamp(session, 9 * 60 + 30),
                    "color": "rgba(251, 191, 36, 0.22)",
                    "label": "Premarket",
                },
                {
                    "start": session_region_timestamp(session, 16 * 60),
                    "end": session_region_timestamp(session, 20 * 60),
                    "color": "rgba(191, 219, 254, 0.24)",
                    "label": "After hours",
                },
            ]
        )
    return regions


def supervision_candidates(frame: pl.DataFrame, supervision_group: str, min_confidence: float) -> pl.DataFrame:
    if frame.is_empty():
        return frame
    columns = set(frame.columns)
    if supervision_group == "method":
        if "method_entry_signal" not in columns:
            return pl.DataFrame()
        frame = frame.filter(pl.col("method_entry_signal") == True)
        if "method_confidence" in columns:
            frame = frame.filter(pl.col("method_confidence") >= min_confidence).sort("method_confidence", descending=True)
    elif supervision_group == "scanner":
        if "is_top_3" in columns:
            frame = frame.filter(pl.col("is_top_3") == True)
        elif "oracle_rank" in columns:
            frame = frame.filter(pl.col("oracle_rank") <= 3)
        if "method_confidence" in columns:
            frame = frame.filter(pl.col("method_confidence") >= min_confidence)
        if "oracle_rank" in columns:
            frame = frame.sort("oracle_rank")
    elif supervision_group == "bar":
        if "oracle_long_entry_signal" not in columns:
            return pl.DataFrame()
        frame = frame.filter(pl.col("oracle_long_entry_signal") == True)
        if "oracle_long_entry_confidence" in columns:
            frame = frame.filter(pl.col("oracle_long_entry_confidence") >= min_confidence).sort("oracle_long_entry_confidence", descending=True)
    if "bar_id" in frame.columns:
        frame = frame.unique(subset=["bar_id"], keep="first")
    sort_column = "bar_time_utc" if "bar_time_utc" in frame.columns else "bar_time_market" if "bar_time_market" in frame.columns else None
    return frame.sort(sort_column) if sort_column else frame


def marker_text(row: dict[str, Any], supervision_group: str) -> str:
    if supervision_group == "scanner":
        rank = row.get("oracle_rank")
        method = display_name(str(row.get("trade_method") or "scan"))
        return f"SCAN #{int(rank) if rank is not None else '-'} {method}"
    if supervision_group == "method":
        method = display_name(str(row.get("trade_method") or "method"))
        value = row.get("method_best_return")
        return f"{method} {float(value or 0) * 100:.2f}%"
    horizon = row.get("horizon_bars") or row.get("horizon")
    return f"BAR h{horizon or '-'}"


def supervision_markers(
    provider: MarketDataProvider,
    *,
    start_date: date,
    end_date: date,
    timeframe: str,
    ticker: str,
    supervision_groups: list[str],
    marker_limit: int,
    min_confidence: float,
) -> list[dict[str, Any]]:
    style = {
        "bar": ("#067647", "circle", "belowBar"),
        "method": ("#2563EB", "arrowUp", "belowBar"),
        "scanner": ("#7C3AED", "arrowUp", "aboveBar"),
    }
    markers: list[dict[str, Any]] = []
    for supervision_group in supervision_groups:
        frame = provider.load_supervision(start_date=start_date, end_date=end_date, timeframe=timeframe, supervision_type=supervision_group, tickers=[ticker])
        candidates = supervision_candidates(frame, supervision_group, min_confidence)
        for row in candidates.head(marker_limit).to_dicts():
            timestamp = chart_timestamp_seconds(row, timeframe)
            if not timestamp:
                continue
            color, shape, position = style.get(supervision_group, ("#1E3A5F", "circle", "belowBar"))
            markers.append({"time": timestamp, "position": position, "color": color, "shape": shape, "text": marker_text(row, supervision_group)})
    markers.sort(key=lambda marker: int(marker.get("time") or 0))
    return markers[:marker_limit]


def chart_payload(
    processed_root: Path,
    *,
    start_date: date,
    end_date: date,
    timeframe: str,
    ticker: str,
    feature_groups_selected: list[str],
    selected_columns: list[str],
    supervision_groups_selected: list[str],
    marker_limit: int,
    min_confidence: float,
) -> dict[str, Any]:
    records = artifact_records(processed_root)
    provider = MarketDataProvider(DataProviderConfig(processed_root=processed_root))
    bars = provider.load_bars(
        start_date=start_date,
        end_date=end_date,
        timeframe=timeframe,
        tickers=[ticker.upper()],
        feature_groups=feature_groups_selected,
    )
    options = {
        "feature_groups": feature_group_options(records, timeframe, start_date, end_date),
        "feature_columns": chart_feature_columns(records, timeframe, start_date, end_date, feature_groups_selected),
        "standard_indicators": CHART_INDICATORS,
        "supervision_groups": [
            group
            for group in SUPERVISION_GROUPS
            if matching_artifacts(records, f"supervision_{group}", timeframe, start_date, end_date)
        ],
    }
    if bars.is_empty():
        return {"candles": [], "volume": [], "overlay_series": [], "oscillator_series": [], "markers": [], "regions": [], "options": options}
    bars = bars.sort("bar_time_market")
    rows = bars.to_dicts()
    candles = []
    volume = []
    for row in rows:
        timestamp = chart_timestamp_seconds(row, timeframe)
        if not timestamp:
            continue
        open_value = float(row.get("open") or 0)
        close_value = float(row.get("close") or 0)
        candles.append(
            {
                "time": timestamp,
                "open": open_value,
                "high": float(row.get("high") or 0),
                "low": float(row.get("low") or 0),
                "close": close_value,
            }
        )
        volume.append(
            {
                "time": timestamp,
                "value": float(row.get("volume") or 0),
                "color": "rgba(51, 228, 42, 0.26)" if close_value >= open_value else "rgba(253, 14, 80, 0.24)",
            }
        )
    settings = indicator_settings(selected_columns)
    overlay_series = []
    oscillator_series = []
    for column, option in settings.items():
        if column not in bars.columns:
            continue
        points = []
        for row in rows:
            timestamp = chart_timestamp_seconds(row, timeframe)
            value = row.get(column)
            if timestamp and value is not None:
                numeric_value = float(value)
                point = {"time": timestamp, "value": numeric_value}
                if column == "macd_hist":
                    point["color"] = "#33E42A" if numeric_value >= 0 else "#FD0E50"
                points.append(point)
        target = oscillator_series if option["pane"] == "oscillator" else overlay_series
        target.append({"column": column, "label": option["label"], "style": option["style"], "color": option["color"], "lineWidth": option["lineWidth"], "data": points})
    markers = (
        []
        if not supervision_groups_selected or marker_limit <= 0
        else supervision_markers(
            provider,
            start_date=start_date,
            end_date=end_date,
            timeframe=timeframe,
            ticker=ticker.upper(),
            supervision_groups=supervision_groups_selected,
            marker_limit=marker_limit,
            min_confidence=min_confidence,
        )
    )
    return {
        "candles": candles,
        "volume": volume,
        "overlay_series": overlay_series,
        "oscillator_series": oscillator_series,
        "markers": markers,
        "regions": extended_session_regions(rows, timeframe),
        "options": options,
    }


def source_scan(raw_root: Path, start_date: date, end_date: date) -> list[dict[str, Any]]:
    return [json_safe(asdict(row)) for row in scan_market_source(raw_root, start_date, end_date)]
