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
from src.data_provider.catalog import (
    catalog_columns_by_column,
    catalog_display_items,
    catalog_item_by_id,
    provider_catalog,
    title_for_column,
)
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
    return title_for_column(value)


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


def record_path(record: dict[str, Any]) -> Path:
    return Path(str(record.get("path") or ""))


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


def chart_feature_columns(records: list[dict[str, Any]], timeframe: str, start: date, end: date, groups: list[str], catalog: dict[str, Any]) -> list[str]:
    columns = []
    catalog_by_column = catalog_columns_by_column(catalog)
    for group in groups:
        for record in matching_artifacts(records, f"features_{group}", timeframe, start, end):
            for item in artifact_schema(record):
                column_contract = catalog_by_column.get(item["column"])
                presentation = column_contract.get("presentation", {}) if column_contract else {}
                chart_role = str(presentation.get("chartRole") or "")
                if (
                    item["column"] not in CHART_FEATURE_EXCLUDE_COLUMNS
                    and is_numeric_dtype(item["dtype"])
                    and presentation.get("selectable", True)
                    and chart_role not in {"", "marker", "background_state", "anchored_zone", "data_only", "table_only"}
                ):
                    columns.append(item["column"])
    order = {str(item.get("column")): index for index, item in enumerate(catalog.get("columns", []))}
    return sorted(set(columns), key=lambda column: order.get(column, 9999))


def chart_pane_for_column(column: str) -> str:
    lower = column.lower()
    price_terms = ("sma", "ema", "tema", "vwap", "bb_", "donchian", "keltner", "hvn", "lvn", "price_proxy")
    if any(term in lower for term in price_terms):
        return "price"
    if lower.startswith("macd_"):
        return "macd"
    return "pane_2"


def indicator_settings(selected_columns: list[str], catalog: dict[str, Any]) -> dict[str, dict[str, Any]]:
    settings = {}
    catalog_by_column = catalog_columns_by_column(catalog)
    for index, column in enumerate(selected_columns):
        column_contract = catalog_by_column.get(column, {})
        presentation = column_contract.get("presentation", {}) if column_contract else {}
        role = str(presentation.get("chartRole") or "")
        pane_name = str(presentation.get("pane") or chart_pane_for_column(column))
        pane = "price" if pane_name == "price" or role in {"price_overlay", "band", "continuous_band", "anchored_zone", "price_zone"} else "oscillator"
        color = str(presentation.get("color") or DYNAMIC_COLORS[index % len(DYNAMIC_COLORS)])
        band_fill_opacity = bounded_float(presentation.get("bandFillOpacity"), default=0.16, lower=0.0, upper=0.6)
        settings[column] = {
            "bandFillColor": str(presentation.get("bandFillColor") or color),
            "bandFillOpacity": band_fill_opacity,
            "chartRole": role,
            "color": "#33E42A" if color == "inherit_candle_direction" else color,
            "dynamicColor": color == "inherit_candle_direction",
            "lineWidth": int(presentation.get("lineWidth") or (3 if column in {"vwap", "ema200", "sma200"} else 1)),
            "opacity": bounded_float(presentation.get("opacity"), default=0.46 if column in {"vwap", "ema200", "sma200"} else 0.82, lower=0.05, upper=1.0),
            "pane": pane,
            "paneKey": pane_name,
            "style": "histogram" if role == "histogram" else "line",
            "lineStyle": str(presentation.get("lineStyle") or "solid"),
            "legend": bool(presentation.get("legend", True)),
            "label": str(column_contract.get("title") or display_name(column)),
        }
    return settings


def chart_display_item_options(records: list[dict[str, Any]], timeframe: str, start: date, end: date, catalog: dict[str, Any]) -> list[dict[str, Any]]:
    available_groups = {
        str(record.get("group"))
        for record in records
        if record.get("timeframe") == timeframe
        and start.isoformat() <= str(record.get("session_date") or "") <= end.isoformat()
        and record.get("exists")
    }
    items = []
    for item in catalog.get("displayItems", []):
        artifact_groups = [str(group) for group in item.get("artifactGroups", [])]
        if artifact_groups and not all(group in available_groups for group in artifact_groups):
            continue
        presentation = item.get("presentation", {})
        if presentation.get("selectable", True) is False:
            continue
        items.append(chart_display_item_summary(item))
    return items


def chart_display_item_summary(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item.get("id"),
        "title": item.get("title"),
        "category": item.get("category"),
        "group": item.get("group"),
        "sourceColumns": item.get("sourceColumns", []),
        "artifactGroups": item.get("artifactGroups", []),
        "featureGroups": item.get("featureGroups", []),
        "presentation": item.get("presentation", {}),
    }


def resolve_chart_display_items(
    catalog: dict[str, Any],
    available_options: list[dict[str, Any]],
    selected_display_item_ids: list[str] | None,
    selected_columns: list[str],
) -> list[dict[str, Any]]:
    catalog_items = catalog_display_items(catalog)
    available_ids = [str(option.get("id")) for option in available_options if option.get("id")]
    available_set = set(available_ids)
    wanted: set[str] = set()
    if selected_display_item_ids is not None:
        wanted.update(item_id for item_id in selected_display_item_ids if item_id in available_set)
    elif selected_columns:
        selected_column_set = set(selected_columns)
        for item_id in available_ids:
            item = catalog_items.get(item_id, {})
            source_columns = {str(column) for column in item.get("sourceColumns", [])}
            if source_columns & selected_column_set:
                wanted.add(item_id)
    if selected_display_item_ids is not None and not wanted:
        return []
    if not wanted:
        wanted.update(
            str(option.get("id"))
            for option in available_options
            if option.get("id") and option.get("presentation", {}).get("defaultVisible")
        )
    return [catalog_items[item_id] for item_id in available_ids if item_id in wanted and item_id in catalog_items]


def feature_groups_for_display_items(items: list[dict[str, Any]]) -> list[str]:
    groups: set[str] = set()
    for item in items:
        for group in item.get("featureGroups", []):
            if group:
                groups.add(str(group))
    return sorted(groups, key=lambda group: FEATURE_GROUPS.index(group) if group in FEATURE_GROUPS else 999)


def display_item_settings(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    settings: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(items):
        presentation = item.get("presentation", {})
        role = str(presentation.get("chartRole") or "")
        if role in {"marker", "price_zone", "anchored_zone", "background_state", "data_only", "table_only"}:
            continue
        parts = presentation.get("parts") if role == "composite" and isinstance(presentation.get("parts"), list) else []
        if parts:
            for part in parts:
                if isinstance(part, dict):
                    option = display_part_settings(item, part, index)
                    if option:
                        settings[option["key"]] = option
        else:
            source_columns = [str(column) for column in item.get("sourceColumns", [])]
            if not source_columns:
                continue
            option = display_part_settings(
                item,
                {
                    "column": str(presentation.get("sourceColumn") or source_columns[0]),
                    "label": item.get("title"),
                    "chartRole": role,
                    "pane": presentation.get("pane"),
                    "style": "histogram" if role == "histogram" else "line",
                    "color": presentation.get("color"),
                    "lineStyle": presentation.get("lineStyle"),
                    "lineWidth": presentation.get("lineWidth"),
                    "legend": presentation.get("legend"),
                    "bandFillColor": presentation.get("bandFillColor"),
                    "bandFillOpacity": presentation.get("bandFillOpacity"),
                },
                index,
            )
            if option:
                settings[option["key"]] = option
    return settings


def display_part_settings(item: dict[str, Any], part: dict[str, Any], index: int) -> dict[str, Any] | None:
    column = str(part.get("column") or "")
    if not column:
        return None
    presentation = item.get("presentation", {})
    role = str(part.get("chartRole") or presentation.get("chartRole") or "")
    pane_name = str(part.get("pane") or presentation.get("pane") or chart_pane_for_column(column))
    pane = "price" if pane_name == "price" or role in {"price_overlay", "band", "continuous_band", "anchored_zone", "price_zone"} else "oscillator"
    color = str(part.get("color") or presentation.get("color") or DYNAMIC_COLORS[index % len(DYNAMIC_COLORS)])
    line_width = int(part.get("lineWidth") or presentation.get("lineWidth") or (3 if column in {"vwap", "ema200", "sma200"} else 1))
    band_fill_opacity = bounded_float(part.get("bandFillOpacity", presentation.get("bandFillOpacity")), default=0.16, lower=0.0, upper=0.6)
    return {
        "key": f"{item.get('id')}:{column}",
        "displayItemId": str(item.get("id") or ""),
        "bandFillColor": str(part.get("bandFillColor") or presentation.get("bandFillColor") or color),
        "bandFillOpacity": band_fill_opacity,
        "chartRole": role,
        "color": "#33E42A" if color == "inherit_candle_direction" else color,
        "column": column,
        "dynamicColor": color == "inherit_candle_direction",
        "lineWidth": max(1, min(6, line_width)),
        "opacity": bounded_float(part.get("opacity", presentation.get("opacity")), default=0.46 if column in {"vwap", "ema200", "sma200"} else 0.82, lower=0.05, upper=1.0),
        "pane": pane,
        "paneKey": pane_name,
        "style": "histogram" if str(part.get("style") or "").lower() == "histogram" or role == "histogram" else "line",
        "lineStyle": str(part.get("lineStyle") or presentation.get("lineStyle") or "solid"),
        "legend": bool(part.get("legend", presentation.get("legend", True))),
        "label": str(part.get("label") or item.get("title") or display_name(column)),
    }


def display_price_zones(rows: list[dict[str, Any]], timeframe: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    zones: list[dict[str, Any]] = []
    if not rows:
        return zones
    candle_duration = chart_candle_duration_seconds(rows, timeframe)
    for item in items:
        presentation = item.get("presentation", {})
        if presentation.get("chartRole") not in {"price_zone", "anchored_zone"}:
            continue
        signal_column = str(presentation.get("signalColumn") or "")
        upper_column = str(presentation.get("upperColumn") or "")
        lower_column = str(presentation.get("lowerColumn") or "")
        if not signal_column or not upper_column or not lower_column:
            continue
        extend_bars = max(1, min(240, int(presentation.get("maxBars") or presentation.get("extendBars") or 20)))
        for index, row in enumerate(rows):
            if not truthy(row.get(signal_column)):
                continue
            try:
                upper = float(row.get(upper_column))
                lower = float(row.get(lower_column))
            except (TypeError, ValueError):
                continue
            if not (upper > 0 and lower > 0):
                continue
            start = chart_timestamp_seconds(row, timeframe)
            if not start:
                continue
            end_index = min(len(rows) - 1, index + extend_bars)
            end = chart_timestamp_seconds(rows[end_index], timeframe) if end_index > index else None
            if not end or end <= start:
                end = start + candle_duration * extend_bars
            else:
                end += candle_duration
            high = max(upper, lower)
            low = min(upper, lower)
            padding_bps = bounded_float(presentation.get("zonePaddingBps"), default=0.0, lower=0.0, upper=100.0)
            if padding_bps > 0:
                midpoint = (high + low) / 2.0
                padding = max(abs(midpoint) * padding_bps / 10_000.0, 0.000001)
                high += padding
                low -= padding
            color = str(presentation.get("color") or "#1E3A5F")
            fill_color = str(presentation.get("bandFillColor") or color)
            fill_opacity = bounded_float(presentation.get("bandFillOpacity"), default=0.08, lower=0.02, upper=0.35)
            zones.append(
                {
                    "displayItemId": str(item.get("id") or ""),
                    "start": start,
                    "end": end,
                    "upper": high,
                    "lower": low,
                    "color": color,
                    "borderColor": str(presentation.get("borderColor") or fill_color),
                    "borderOpacity": bounded_float(presentation.get("borderOpacity"), default=max(fill_opacity * 1.8, 0.12), lower=0.0, upper=0.35),
                    "borderWidth": max(0, min(3, int(presentation.get("borderWidth") or 1))),
                    "fillColor": fill_color,
                    "fillOpacity": fill_opacity,
                    "label": str(item.get("title") or signal_column),
                }
            )
    return zones


def display_background_regions(rows: list[dict[str, Any]], timeframe: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    regions: list[dict[str, Any]] = []
    if not rows:
        return regions
    candle_duration = chart_candle_duration_seconds(rows, timeframe)
    for item in items:
        presentation = item.get("presentation", {})
        if presentation.get("chartRole") != "background_state":
            continue
        state_column = str(presentation.get("stateColumn") or "")
        if not state_column:
            continue
        state_colors = presentation.get("stateColors") if isinstance(presentation.get("stateColors"), dict) else {}
        opacity = bounded_float(presentation.get("opacity"), default=0.08, lower=0.01, upper=0.35)
        current_state: str | None = None
        start_time: int | None = None
        previous_time: int | None = None
        for row in rows:
            timestamp = chart_timestamp_seconds(row, timeframe)
            if not timestamp:
                continue
            state = str(row.get(state_column) or "").strip()
            if not state:
                if current_state is not None and start_time is not None and previous_time is not None:
                    regions.append(background_region(item, current_state, start_time, previous_time + candle_duration, state_colors, opacity))
                current_state = None
                start_time = None
                previous_time = timestamp
                continue
            if current_state is None:
                current_state = state
                start_time = timestamp
            elif state != current_state:
                if start_time is not None and previous_time is not None:
                    regions.append(background_region(item, current_state, start_time, previous_time + candle_duration, state_colors, opacity))
                current_state = state
                start_time = timestamp
            previous_time = timestamp
        if current_state is not None and start_time is not None and previous_time is not None:
            regions.append(background_region(item, current_state, start_time, previous_time + candle_duration, state_colors, opacity))
    return regions


def background_region(item: dict[str, Any], state: str, start: int, end: int, state_colors: dict[str, Any], opacity: float) -> dict[str, Any]:
    color = str(state_colors.get(state) or state_colors.get(state.lower()) or "#667085")
    return {
        "start": start,
        "end": end,
        "color": rgba_css(color, opacity),
        "label": f"{item.get('title') or 'State'}: {display_name(state)}",
    }


def rgba_css(color: str, opacity: float) -> str:
    match = re.fullmatch(r"#?([0-9a-fA-F]{6})", color.strip())
    if not match:
        return color
    raw = match.group(1)
    red = int(raw[0:2], 16)
    green = int(raw[2:4], 16)
    blue = int(raw[4:6], 16)
    return f"rgba({red}, {green}, {blue}, {opacity:.3f})"


def display_item_markers(rows: list[dict[str, Any]], timeframe: str, items: list[dict[str, Any]], marker_limit: int) -> list[dict[str, Any]]:
    markers: list[dict[str, Any]] = []
    if marker_limit <= 0:
        return markers
    for item in items:
        presentation = item.get("presentation", {})
        if presentation.get("chartRole") != "marker":
            continue
        signal_columns = presentation.get("signalColumns")
        if not isinstance(signal_columns, list):
            signal_columns = [presentation.get("signalColumn")] if presentation.get("signalColumn") else item.get("sourceColumns", [])
        for row in rows:
            if not any(truthy(row.get(str(column))) for column in signal_columns):
                continue
            timestamp = chart_timestamp_seconds(row, timeframe)
            if not timestamp:
                continue
            markers.append(
                {
                    "displayItemId": str(item.get("id") or ""),
                    "time": timestamp,
                    "position": str(presentation.get("markerPosition") or "belowBar"),
                    "color": str(presentation.get("color") or "#1E3A5F"),
                    "shape": str(presentation.get("markerShape") or "circle"),
                    "text": str(item.get("title") or "Feature"),
                }
            )
            if len(markers) >= marker_limit:
                return markers
    return markers


def chart_candle_duration_seconds(rows: list[dict[str, Any]], timeframe: str) -> int:
    minutes = timeframe_minutes(timeframe)
    if minutes:
        return max(60, minutes * 60)
    timestamps = [timestamp for timestamp in (chart_timestamp_seconds(row, timeframe) for row in rows[:80]) if timestamp]
    deltas = sorted({right - left for left, right in zip(timestamps, timestamps[1:]) if right > left})
    return deltas[len(deltas) // 2] if deltas else 24 * 60 * 60


def truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def bounded_float(value: Any, default: float, lower: float, upper: float) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        numeric = default
    return max(lower, min(upper, numeric))


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
    catalog: dict[str, Any],
) -> list[dict[str, Any]]:
    markers: list[dict[str, Any]] = []
    for supervision_group in supervision_groups:
        frame = provider.load_supervision(start_date=start_date, end_date=end_date, timeframe=timeframe, supervision_type=supervision_group, tickers=[ticker])
        candidates = supervision_candidates(frame, supervision_group, min_confidence)
        for row in candidates.head(marker_limit).to_dicts():
            timestamp = chart_timestamp_seconds(row, timeframe)
            if not timestamp:
                continue
            color, shape, position = supervision_marker_style(catalog, supervision_group, row)
            markers.append({"time": timestamp, "position": position, "color": color, "shape": shape, "text": marker_text(row, supervision_group)})
    markers.sort(key=lambda marker: int(marker.get("time") or 0))
    return markers[:marker_limit]


def supervision_marker_style(catalog: dict[str, Any], supervision_group: str, row: dict[str, Any]) -> tuple[str, str, str]:
    by_id = catalog_item_by_id(catalog)
    defaults = {
        "bar": ("#067647", "circle", "belowBar"),
        "method": ("#2563EB", "arrowUp", "belowBar"),
        "scanner": ("#7C3AED", "arrowUp", "aboveBar"),
    }
    item = None
    if supervision_group == "method":
        item = by_id.get(f"method.{row.get('trade_method')}")
    elif supervision_group == "scanner":
        item = by_id.get("scanner.method_rank")
    elif supervision_group == "bar":
        item = by_id.get("oracle_long_entry_signal")
    default_color, default_shape, default_position = defaults.get(supervision_group, ("#1E3A5F", "circle", "belowBar"))
    presentation = item.get("presentation", {}) if item else {}
    return (
        str(presentation.get("color") or default_color),
        str(presentation.get("markerShape") or default_shape),
        str(presentation.get("markerPosition") or default_position),
    )


def chart_payload(
    processed_root: Path,
    *,
    start_date: date,
    end_date: date,
    timeframe: str,
    ticker: str,
    feature_groups_selected: list[str],
    selected_columns: list[str],
    selected_display_items: list[str] | None,
    supervision_groups_selected: list[str],
    marker_limit: int,
    min_confidence: float,
) -> dict[str, Any]:
    records = artifact_records(processed_root)
    catalog = provider_catalog(processed_root)
    provider = MarketDataProvider(DataProviderConfig(processed_root=processed_root))
    display_options = chart_display_item_options(records, timeframe, start_date, end_date, catalog)
    selected_display_contracts = resolve_chart_display_items(catalog, display_options, selected_display_items, selected_columns)
    display_feature_groups = feature_groups_for_display_items(selected_display_contracts)
    requested_feature_groups = display_feature_groups or feature_groups_selected
    bars = provider.load_bars(
        start_date=start_date,
        end_date=end_date,
        timeframe=timeframe,
        tickers=[ticker.upper()],
        feature_groups=requested_feature_groups,
    )
    feature_columns = chart_feature_columns(records, timeframe, start_date, end_date, requested_feature_groups, catalog)
    catalog_by_column = catalog_columns_by_column(catalog)
    indicator_columns = [
        column
        for column in feature_columns
        if catalog_by_column.get(column, {}).get("category") == "indicator"
    ]
    non_indicator_columns = [
        column
        for column in feature_columns
        if column not in set(indicator_columns)
    ]
    options = {
        "feature_groups": feature_group_options(records, timeframe, start_date, end_date),
        "feature_columns": non_indicator_columns,
        "standard_indicators": indicator_columns,
        "supervision_groups": [
            group
            for group in SUPERVISION_GROUPS
            if matching_artifacts(records, f"supervision_{group}", timeframe, start_date, end_date)
        ],
        "display_items": display_options,
    }
    if bars.is_empty():
        return {
            "candles": [],
            "volume": [],
            "overlay_series": [],
            "oscillator_series": [],
            "markers": [],
            "regions": [],
            "price_zones": [],
            "options": options,
        }
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
    settings = display_item_settings(selected_display_contracts) if selected_display_contracts else indicator_settings(selected_columns, catalog)
    overlay_series = []
    oscillator_series = []
    for column, option in settings.items():
        source_column = str(option.get("column") or column)
        if source_column not in bars.columns:
            continue
        points = []
        for row in rows:
            timestamp = chart_timestamp_seconds(row, timeframe)
            value = row.get(source_column)
            if timestamp and value is not None:
                numeric_value = float(value)
                point = {"time": timestamp, "value": numeric_value}
                if option.get("dynamicColor"):
                    point["color"] = "#33E42A" if numeric_value >= 0 else "#FD0E50"
                points.append(point)
        target = oscillator_series if option["pane"] == "oscillator" else overlay_series
        target.append(
            {
                "column": source_column,
                "displayItemId": option.get("displayItemId"),
                "label": option["label"],
                "style": option["style"],
                "chartRole": option["chartRole"],
                "paneKey": option.get("paneKey"),
                "lineStyle": option["lineStyle"],
                "color": option["color"],
                "opacity": option.get("opacity", 1.0),
                "bandFillColor": option["bandFillColor"],
                "bandFillOpacity": option["bandFillOpacity"],
                "legend": option["legend"],
                "lineWidth": option["lineWidth"],
                "data": points,
            }
        )
    feature_markers = display_item_markers(rows, timeframe, selected_display_contracts, marker_limit)
    supervision_marker_limit = max(0, marker_limit - len(feature_markers))
    supervision = (
        []
        if not supervision_groups_selected or supervision_marker_limit <= 0
        else supervision_markers(
            provider,
            start_date=start_date,
            end_date=end_date,
            timeframe=timeframe,
            ticker=ticker.upper(),
            supervision_groups=supervision_groups_selected,
            marker_limit=supervision_marker_limit,
            min_confidence=min_confidence,
            catalog=catalog,
        )
    )
    markers = sorted([*feature_markers, *supervision], key=lambda marker: int(marker.get("time") or 0))[:marker_limit]
    regions = [*extended_session_regions(rows, timeframe), *display_background_regions(rows, timeframe, selected_display_contracts)]
    return {
        "candles": candles,
        "volume": volume,
        "overlay_series": overlay_series,
        "oscillator_series": oscillator_series,
        "markers": markers,
        "regions": regions,
        "price_zones": display_price_zones(rows, timeframe, selected_display_contracts),
        "options": options,
    }


def catalog_preview_payload(processed_root: Path, item_id: str, preferred_timeframe: str | None = None) -> dict[str, Any]:
    catalog = provider_catalog(processed_root)
    item = catalog_item_by_id(catalog).get(item_id)
    if not item:
        return {"sampled": False, "reason": "Catalog item was not found.", "payload": None}
    display_items = catalog_display_items(catalog)
    display_item_id = item_id if item_id in display_items else related_display_item_id(catalog, item)
    source_columns = [str(column) for column in item.get("sourceColumns", []) or ([item.get("column")] if item.get("column") else [])]
    signal_columns = preview_signal_columns(item)
    records = artifact_records(processed_root)
    sample = find_catalog_preview_sample(records, item, signal_columns, source_columns, preferred_timeframe)
    if not sample:
        return {"sampled": False, "reason": "No saved row demonstrated this catalog item in the current processed store.", "payload": None}
    selected_display_items = [display_item_id] if display_item_id else None
    selected_columns = [] if display_item_id else [str(item.get("column"))] if item.get("column") else []
    payload = chart_payload(
        processed_root,
        start_date=date.fromisoformat(sample["session_date"]),
        end_date=date.fromisoformat(sample["session_date"]),
        timeframe=sample["timeframe"],
        ticker=sample["ticker"],
        feature_groups_selected=[],
        selected_columns=selected_columns,
        selected_display_items=selected_display_items,
        supervision_groups_selected=[],
        marker_limit=180,
        min_confidence=0.0,
    )
    return {"sampled": True, "reason": "", "sample": sample, "payload": payload}


def related_display_item_id(catalog: dict[str, Any], item: dict[str, Any]) -> str | None:
    column = str(item.get("column") or "")
    if not column:
        return None
    for display_item in catalog.get("displayItems", []):
        if column in {str(source) for source in display_item.get("sourceColumns", [])}:
            return str(display_item.get("id"))
    return None


def preview_signal_columns(item: dict[str, Any]) -> list[str]:
    presentation = item.get("presentation", {})
    signals = presentation.get("signalColumns")
    if isinstance(signals, list):
        return [str(column) for column in signals if column]
    signal = presentation.get("signalColumn")
    if signal:
        return [str(signal)]
    source_columns = [str(column) for column in item.get("sourceColumns", []) if column]
    return [column for column in source_columns if "signal" in column or column.startswith(("is_", "bullish_", "bearish_"))]


def find_catalog_preview_sample(
    records: list[dict[str, Any]],
    item: dict[str, Any],
    signal_columns: list[str],
    source_columns: list[str],
    preferred_timeframe: str | None,
) -> dict[str, Any] | None:
    artifact_groups = [str(group) for group in item.get("artifactGroups", [])]
    candidate_records = [
        record for record in records
        if record.get("exists") and (not artifact_groups or str(record.get("group")) in artifact_groups)
    ]
    if preferred_timeframe:
        candidate_records = sorted(candidate_records, key=lambda record: (str(record.get("timeframe")) != preferred_timeframe, artifact_group_sample_order(str(record.get("group"))), str(record.get("session_date"))))
    else:
        candidate_records = sorted(candidate_records, key=lambda record: (timeframe_sort_key(str(record.get("timeframe") or "")), artifact_group_sample_order(str(record.get("group"))), str(record.get("session_date"))))
    filter_columns = signal_columns or source_columns
    for record in candidate_records:
        path = record_path(record)
        if not path.exists():
            continue
        scan = pl.scan_parquet(path)
        schema = scan.collect_schema()
        schema_names = schema.names()
        available_filter_columns = [column for column in filter_columns if column in schema_names]
        if not available_filter_columns and "bar_id" not in schema_names:
            continue
        expression = preview_filter_expression(schema, available_filter_columns)
        if expression is not None:
            scan = scan.filter(expression)
        selected = [column for column in ["bar_id", "ticker", "bar_time_market"] if column in schema_names]
        if not selected:
            continue
        frame = scan.select(selected).limit(1).collect()
        if frame.is_empty():
            continue
        row = frame.to_dicts()[0]
        sample = sample_from_preview_row(records, record, row)
        if sample:
            return sample
    return None


def artifact_group_sample_order(group: str) -> int:
    return 1 if group == "bars" else 0


def preview_filter_expression(schema: pl.Schema, columns: list[str]) -> pl.Expr | None:
    expressions: list[pl.Expr] = []
    for column in columns:
        dtype = schema[column]
        if dtype == pl.Boolean:
            expressions.append(pl.col(column).fill_null(False))
        elif dtype.is_numeric():
            expressions.append(pl.col(column).is_not_null() & (pl.col(column) != 0))
        else:
            expressions.append(pl.col(column).is_not_null() & (pl.col(column).cast(pl.Utf8).str.len_chars() > 0))
    if not expressions:
        return None
    expression = expressions[0]
    for item in expressions[1:]:
        expression = expression | item
    return expression


def sample_from_preview_row(records: list[dict[str, Any]], record: dict[str, Any], row: dict[str, Any]) -> dict[str, Any] | None:
    timeframe = str(record.get("timeframe") or "")
    session_date = str(record.get("session_date") or "")
    ticker = str(row.get("ticker") or "")
    timestamp = chart_timestamp_seconds(row, timeframe)
    if not ticker and row.get("bar_id"):
        bars_record = first_matching_artifact(records, "bars", timeframe, session_date)
        if bars_record:
            bars_path = record_path(bars_record)
            if bars_path.exists():
                bars = (
                    pl.scan_parquet(bars_path)
                    .filter(pl.col("bar_id") == str(row["bar_id"]))
                    .select([column for column in ["ticker", "bar_time_market", "bar_time_utc"] if column])
                    .limit(1)
                    .collect()
                )
                if not bars.is_empty():
                    bar_row = bars.to_dicts()[0]
                    ticker = str(bar_row.get("ticker") or "")
                    timestamp = chart_timestamp_seconds(bar_row, timeframe)
    if not ticker or not timeframe or not session_date:
        return None
    return {
        "ticker": ticker,
        "timeframe": timeframe,
        "session_date": session_date,
        "bar_id": str(row.get("bar_id") or ""),
        "time": timestamp,
    }


def source_scan(raw_root: Path, start_date: date, end_date: date) -> list[dict[str, Any]]:
    return [json_safe(asdict(row)) for row in scan_market_source(raw_root, start_date, end_date)]
