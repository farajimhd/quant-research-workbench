from __future__ import annotations

import ast
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
                "build_id": record.get("build_id"),
                "build_name": record.get("build_name"),
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


def load_artifact_query_sample(
    records: list[dict[str, Any]],
    *,
    group: str,
    timeframe: str,
    start_date: date,
    end_date: date,
    columns: list[str],
    row_limit: int,
    tickers: list[str],
    row_offset: int = 0,
    table_query: dict[str, Any] | None = None,
) -> dict[str, Any]:
    artifacts = matching_artifacts(records, group, timeframe, start_date, end_date)
    if not artifacts:
        return {
            "columns": [],
            "has_more": False,
            "row_count": 0,
            "row_limit": row_limit,
            "row_offset": row_offset,
            "rows": [],
            "scanned_artifacts": 0,
        }
    paths = [str(record_path(record)) for record in artifacts]
    scan = pl.scan_parquet(paths)
    schema = scan.collect_schema()
    schema_names = schema.names()
    if tickers and "ticker" in schema_names:
        scan = scan.filter(pl.col("ticker").is_in([ticker.upper() for ticker in tickers]))
    scan = apply_table_query(scan, schema, table_query)
    selected_columns = [column for column in columns if column in schema_names]
    if not selected_columns:
        selected_columns = default_preview_columns(schema_names)
    if selected_columns:
        scan = scan.select(selected_columns)
    row_offset = max(0, row_offset)
    row_limit = max(1, min(row_limit, 5000))
    frame = scan.slice(row_offset, row_limit + 1).collect()
    has_more = frame.height > row_limit
    if has_more:
        frame = frame.head(row_limit)
    rows = frame.to_dicts()
    return {
        "columns": frame.columns,
        "has_more": has_more,
        "row_count": row_offset + len(rows) + (1 if has_more else 0),
        "row_limit": row_limit,
        "row_offset": row_offset,
        "rows": json_safe(rows),
        "scanned_artifacts": len(artifacts),
    }


def default_preview_columns(schema_names: list[str]) -> list[str]:
    preferred = [
        "bar_id",
        "ticker",
        "session_date",
        "timeframe",
        "bar_time_market",
        "bar_time_utc",
        "minute_of_day",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "transactions",
    ]
    selected = [column for column in preferred if column in schema_names]
    if selected:
        return selected[:16]
    return schema_names[:24]


def load_scanner_snapshot(
    records: list[dict[str, Any]],
    *,
    session_date: date,
    timeframe: str,
    bar_time: str,
    feature_groups: list[str],
    columns: list[str],
    row_limit: int,
    row_offset: int = 0,
    table_query: dict[str, Any] | None = None,
    derived_columns: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    derived_column_names = requested_derived_column_names(derived_columns)
    bars_record = first_matching_artifact(records, "bars", timeframe, session_date.isoformat())
    if not bars_record or not bars_record.get("exists"):
        return scanner_empty_payload(timeframe, session_date, bar_time, row_limit, row_offset, "Bars artifact not found")
    minute = parse_bar_start_minute(bar_time)
    if minute is None:
        return scanner_empty_payload(timeframe, session_date, bar_time, row_limit, row_offset, "Invalid bar time")

    scan = pl.scan_parquet(str(record_path(bars_record)))
    schema = scan.collect_schema()

    joined_groups: list[str] = []
    for group in feature_groups:
        feature_record = first_matching_artifact(records, f"features_{group}", timeframe, session_date.isoformat())
        if not feature_record or not feature_record.get("exists"):
            continue
        feature_scan = pl.scan_parquet(str(record_path(feature_record)))
        feature_schema = feature_scan.collect_schema()
        if "bar_id" not in feature_schema.names():
            continue
        duplicate_columns = [column for column in feature_schema.names() if column != "bar_id" and column in scan.collect_schema().names()]
        if duplicate_columns:
            feature_scan = feature_scan.drop(duplicate_columns)
        scan = scan.join(feature_scan, on="bar_id", how="left", coalesce=True)
        joined_groups.append(group)

    joined_schema = scan.collect_schema()
    scan = apply_scanner_compatibility_columns(scan, joined_schema)
    joined_schema = scan.collect_schema()
    minute_filter = scanner_minute_filter(joined_schema.names(), session_date, minute)
    if minute_filter is not None:
        scan = scan.filter(minute_filter)
    scan = apply_derived_columns(scan, joined_schema, derived_columns)
    joined_schema = scan.collect_schema()
    joined_columns = joined_schema.names()
    scan = apply_table_query(scan, joined_schema, table_query)
    selected_columns = [column for column in columns if column in joined_columns]
    if not selected_columns:
        selected_columns = default_scanner_columns(joined_columns)
    for column in derived_column_names:
        if column in joined_columns and column not in selected_columns:
            selected_columns.append(column)
    if selected_columns:
        scan = scan.select(selected_columns)

    row_limit = max(1, min(row_limit, 5000))
    row_offset = max(0, row_offset)
    frame = scan.slice(row_offset, row_limit + 1).collect()
    has_more = frame.height > row_limit
    if has_more:
        frame = frame.head(row_limit)
    rows = frame.to_dicts()
    return {
        "bar_time": bar_time,
        "columns": frame.columns,
        "feature_groups": joined_groups,
        "has_more": has_more,
        "reason": "",
        "row_count": row_offset + len(rows) + (1 if has_more else 0),
        "row_limit": row_limit,
        "row_offset": row_offset,
        "rows": json_safe(rows),
        "session_date": session_date.isoformat(),
        "timeframe": timeframe,
        "total_columns": len(joined_columns),
    }


def scanner_minute_filter(schema_names: list[str], session_date: date, minute: int) -> pl.Expr | None:
    if "minute_of_day" in schema_names:
        return pl.col("minute_of_day") == minute
    if "bar_time_market" in schema_names:
        start = datetime.combine(session_date, time(minute // 60, minute % 60))
        return pl.col("bar_time_market").dt.replace_time_zone(None) == start
    return None


def apply_scanner_compatibility_columns(scan: pl.LazyFrame, schema: pl.Schema) -> pl.LazyFrame:
    names = schema.names()
    order_columns = [column for column in ["ticker", "bar_time_utc", "bar_time_market", "minute_of_day"] if column in names]
    if order_columns:
        scan = scan.sort(order_columns)

    scan = apply_scanner_volume_compatibility_columns(scan, names)
    schema = scan.collect_schema()
    return apply_scanner_price_action_compatibility_columns(scan, schema.names())


def apply_scanner_volume_compatibility_columns(scan: pl.LazyFrame, names: list[str]) -> pl.LazyFrame:
    missing_volume_sma10 = "volume_sma10" not in names
    missing_relative_volume10 = "relative_volume10" not in names
    if "volume" not in names or not (missing_volume_sma10 or missing_relative_volume10):
        return scan

    if missing_volume_sma10:
        scan = scan.with_columns(pl.col("volume").rolling_mean(10).over("ticker").alias("volume_sma10"))
    if missing_relative_volume10:
        scan = scan.with_columns(
            pl.when(pl.col("volume_sma10") > 0)
            .then(pl.col("volume") / pl.col("volume_sma10"))
            .otherwise(0.0)
            .alias("relative_volume10")
        )
    return scan


def apply_scanner_price_action_compatibility_columns(scan: pl.LazyFrame, names: list[str]) -> pl.LazyFrame:
    group_columns = scanner_session_group_columns(names)
    if not group_columns or not {"open", "close"}.issubset(names):
        return scan

    base_exprs: list[pl.Expr] = []
    if "body" not in names:
        base_exprs.append((pl.col("close") - pl.col("open")).alias("body"))
    if "body_abs" not in names:
        body_expr = pl.col("body") if "body" in names else pl.col("close") - pl.col("open")
        base_exprs.append(body_expr.abs().alias("body_abs"))
    if "is_green" not in names:
        base_exprs.append((pl.col("close") > pl.col("open")).alias("is_green"))
    if "is_red" not in names:
        base_exprs.append((pl.col("close") < pl.col("open")).alias("is_red"))
    if "bar_range" not in names and {"high", "low"}.issubset(names):
        base_exprs.append((pl.col("high") - pl.col("low")).alias("bar_range"))
    if "session_bar_count" not in names:
        base_exprs.append(pl.cum_count("close").over(group_columns).alias("session_bar_count"))
    if base_exprs:
        scan = scan.with_columns(base_exprs)
        names = scan.collect_schema().names()

    cumulative_exprs: list[pl.Expr] = []
    if "green_bar_count_so_far" not in names and "is_green" in names:
        cumulative_exprs.append(pl.when(pl.col("is_green")).then(1).otherwise(0).cum_sum().over(group_columns).alias("green_bar_count_so_far"))
    if "red_bar_count_so_far" not in names and "is_red" in names:
        cumulative_exprs.append(pl.when(pl.col("is_red")).then(1).otherwise(0).cum_sum().over(group_columns).alias("red_bar_count_so_far"))
    if "green_body_sum_so_far" not in names and {"is_green", "body_abs"}.issubset(names):
        cumulative_exprs.append(pl.when(pl.col("is_green")).then(pl.col("body_abs")).otherwise(0.0).cum_sum().over(group_columns).alias("green_body_sum_so_far"))
    if "red_body_sum_so_far" not in names and {"is_red", "body_abs"}.issubset(names):
        cumulative_exprs.append(pl.when(pl.col("is_red")).then(pl.col("body_abs")).otherwise(0.0).cum_sum().over(group_columns).alias("red_body_sum_so_far"))
    if "green_range_sum_so_far" not in names and {"is_green", "bar_range"}.issubset(names):
        cumulative_exprs.append(pl.when(pl.col("is_green")).then(pl.col("bar_range")).otherwise(0.0).cum_sum().over(group_columns).alias("green_range_sum_so_far"))
    if "red_range_sum_so_far" not in names and {"is_red", "bar_range"}.issubset(names):
        cumulative_exprs.append(pl.when(pl.col("is_red")).then(pl.col("bar_range")).otherwise(0.0).cum_sum().over(group_columns).alias("red_range_sum_so_far"))
    if "net_body_sum_so_far" not in names and "body" in names:
        cumulative_exprs.append(pl.col("body").cum_sum().over(group_columns).alias("net_body_sum_so_far"))
    if cumulative_exprs:
        scan = scan.with_columns(cumulative_exprs)
        names = scan.collect_schema().names()

    average_exprs: list[pl.Expr] = []
    if "green_body_avg" not in names and {"green_body_sum_so_far", "session_bar_count"}.issubset(names):
        average_exprs.append(
            pl.when(pl.col("session_bar_count") > 0)
            .then(pl.col("green_body_sum_so_far") / pl.col("session_bar_count"))
            .otherwise(0.0)
            .alias("green_body_avg")
        )
    if "red_body_avg" not in names and {"red_body_sum_so_far", "session_bar_count"}.issubset(names):
        average_exprs.append(
            pl.when(pl.col("session_bar_count") > 0)
            .then(pl.col("red_body_sum_so_far") / pl.col("session_bar_count"))
            .otherwise(0.0)
            .alias("red_body_avg")
        )
    if average_exprs:
        scan = scan.with_columns(average_exprs)
    return scan


def scanner_session_group_columns(names: list[str]) -> list[str]:
    if "ticker" not in names:
        return []
    if "session_date" in names:
        return ["ticker", "session_date"]
    return ["ticker"]


def requested_derived_column_names(derived_columns: list[dict[str, Any]] | None) -> list[str]:
    names: list[str] = []
    for item in derived_columns or []:
        if not isinstance(item, dict) or item.get("enabled") is False:
            continue
        name = str(item.get("name") or "").strip()
        if name:
            names.append(name)
    return names


def apply_derived_columns(scan: pl.LazyFrame, schema: pl.Schema, derived_columns: list[dict[str, Any]] | None) -> pl.LazyFrame:
    if not derived_columns:
        return scan
    schema_by_name = dict(schema.items())
    for index, item in enumerate(derived_columns, start=1):
        if not isinstance(item, dict):
            continue
        if item.get("enabled") is False:
            continue
        name = normalize_derived_column_name(item.get("name"))
        expression_text = str(item.get("expression") or "").strip()
        if not name or not expression_text:
            continue
        if name in schema_by_name:
            raise ValueError(f"Derived column '{name}' already exists")
        expression = build_derived_column_expression(expression_text, schema_by_name).alias(name)
        scan = scan.with_columns(expression)
        schema_by_name[name] = pl.Float64
        if index > 50:
            raise ValueError("Too many derived columns; limit is 50")
    return scan


def normalize_derived_column_name(value: Any) -> str:
    name = str(value or "").strip()
    if not name:
        return ""
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$", name):
        raise ValueError(f"Invalid derived column name '{name}'")
    return name


def build_derived_column_expression(expression_text: str, schema_by_name: dict[str, pl.DataType]) -> pl.Expr:
    if len(expression_text) > 500:
        raise ValueError("Derived expression is too long")
    try:
        parsed = ast.parse(expression_text, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"Invalid derived expression: {expression_text}") from exc
    return build_derived_ast_expression(parsed.body, schema_by_name)


def build_derived_ast_expression(node: ast.AST, schema_by_name: dict[str, pl.DataType]) -> pl.Expr:
    if isinstance(node, ast.Name):
        if node.id not in schema_by_name:
            raise ValueError(f"Unknown column '{node.id}' in derived expression")
        return pl.col(node.id)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool):
            return pl.lit(node.value)
        if isinstance(node.value, (int, float)):
            return pl.lit(node.value)
        raise ValueError("Derived expressions only support numeric and boolean literals")
    if isinstance(node, ast.UnaryOp):
        operand = build_derived_ast_expression(node.operand, schema_by_name)
        if isinstance(node.op, ast.USub):
            return -operand
        if isinstance(node.op, ast.UAdd):
            return operand
        if isinstance(node.op, ast.Not):
            return ~operand
    if isinstance(node, ast.BinOp):
        left = build_derived_ast_expression(node.left, schema_by_name)
        right = build_derived_ast_expression(node.right, schema_by_name)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            return left / right
        if isinstance(node.op, ast.Pow):
            return left.pow(right)
    if isinstance(node, ast.BoolOp):
        values = [build_derived_ast_expression(value, schema_by_name) for value in node.values]
        if not values:
            raise ValueError("Boolean expression needs at least one value")
        combined = values[0]
        for value in values[1:]:
            combined = combined & value if isinstance(node.op, ast.And) else combined | value
        return combined
    if isinstance(node, ast.Compare):
        left = build_derived_ast_expression(node.left, schema_by_name)
        comparisons: list[pl.Expr] = []
        current_left = left
        for operator, comparator in zip(node.ops, node.comparators):
            right = build_derived_ast_expression(comparator, schema_by_name)
            comparisons.append(build_derived_comparison(current_left, operator, right))
            current_left = right
        combined = comparisons[0]
        for comparison in comparisons[1:]:
            combined = combined & comparison
        return combined
    if isinstance(node, ast.Call):
        return build_derived_call_expression(node, schema_by_name)
    raise ValueError("Unsupported derived expression syntax")


def build_derived_comparison(left: pl.Expr, operator: ast.cmpop, right: pl.Expr) -> pl.Expr:
    if isinstance(operator, ast.Eq):
        return left == right
    if isinstance(operator, ast.NotEq):
        return left != right
    if isinstance(operator, ast.Gt):
        return left > right
    if isinstance(operator, ast.GtE):
        return left >= right
    if isinstance(operator, ast.Lt):
        return left < right
    if isinstance(operator, ast.LtE):
        return left <= right
    raise ValueError("Unsupported comparison operator")


def build_derived_call_expression(node: ast.Call, schema_by_name: dict[str, pl.DataType]) -> pl.Expr:
    if not isinstance(node.func, ast.Name) or node.keywords:
        raise ValueError("Derived expressions only support simple function calls")
    function = node.func.id.lower()
    args = [build_derived_ast_expression(arg, schema_by_name) for arg in node.args]
    if function == "abs" and len(args) == 1:
        return args[0].abs()
    if function == "sqrt" and len(args) == 1:
        return args[0].sqrt()
    if function == "log" and len(args) == 1:
        return args[0].log()
    if function == "log1p" and len(args) == 1:
        return (args[0] + 1).log()
    if function == "clip" and len(args) == 3:
        return args[0].clip(args[1], args[2])
    if function == "min" and len(args) >= 2:
        return pl.min_horizontal(args)
    if function == "max" and len(args) >= 2:
        return pl.max_horizontal(args)
    if function == "rank_desc" and len(args) == 1:
        return args[0].rank(method="dense", descending=True)
    if function == "rank_asc" and len(args) == 1:
        return args[0].rank(method="dense")
    if function == "percentile_rank" and len(args) == 1:
        return args[0].rank(method="average") / pl.len()
    if function == "zscore" and len(args) == 1:
        std = args[0].std()
        return pl.when(std > 0).then((args[0] - args[0].mean()) / std).otherwise(0.0)
    raise ValueError(f"Unsupported derived expression function '{function}'")


def scanner_empty_payload(timeframe: str, session_date: date, bar_time: str, row_limit: int, row_offset: int, reason: str) -> dict[str, Any]:
    return {
        "bar_time": bar_time,
        "columns": [],
        "feature_groups": [],
        "has_more": False,
        "reason": reason,
        "row_count": 0,
        "row_limit": row_limit,
        "row_offset": row_offset,
        "rows": [],
        "session_date": session_date.isoformat(),
        "timeframe": timeframe,
        "total_columns": 0,
    }


def parse_bar_start_minute(value: str) -> int | None:
    text = str(value or "").strip()
    match = re.match(r"^(\d{1,2}):(\d{2})", text)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2))
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        return None
    return hour * 60 + minute


def default_scanner_columns(schema_names: list[str]) -> list[str]:
    preferred = [
        "ticker",
        "bar_time_market",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "transactions",
        "dollar_volume",
        "relative_volume10",
        "relative_volume20",
        "relative_dollar_volume20",
        "intraday_rvol13",
        "intraday_dollar_rvol13",
        "tod_cum_volume_avg13",
        "tod_cum_dollar_volume_avg13",
        "return_1",
        "close_location",
        "is_green",
        "is_red",
        "vwap",
        "session_bar_count",
        "day_high_so_far",
        "day_low_so_far",
        "day_volume_so_far",
        "day_dollar_volume_so_far",
        "green_bar_count_so_far",
        "red_bar_count_so_far",
        "green_body_sum_so_far",
        "red_body_sum_so_far",
        "green_body_avg",
        "red_body_avg",
        "green_range_sum_so_far",
        "red_range_sum_so_far",
        "net_body_sum_so_far",
        "gap_pct",
        "or_5m_high",
        "or_5m_low",
        "or_5m_range",
        "tema9",
        "tema20",
        "macd_line",
        "macd_signal",
        "macd_hist",
        "rsi14",
    ]
    selected = [column for column in preferred if column in schema_names]
    return selected or schema_names[:32]


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
                    and chart_role not in {"", "marker", "text_label", "background_state", "anchored_zone", "data_only", "table_only"}
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
        "knowledge": item.get("knowledge", {}),
        "leakage": item.get("leakage", {}),
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
        if role in {"marker", "text_label", "price_zone", "anchored_zone", "background_state", "data_only", "table_only"}:
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
            zone_height_mode = str(presentation.get("zoneHeightMode") or "price_range")
            padding_bps = bounded_float(presentation.get("zonePaddingBps"), default=0.0, lower=0.0, upper=100.0)
            if zone_height_mode != "fixed_px" and padding_bps > 0:
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
                    "borderStyle": str(presentation.get("borderStyle") or "solid"),
                    "borderWidth": max(0, min(3, int(presentation.get("borderWidth") or 1))),
                    "fillColor": fill_color,
                    "fillOpacity": fill_opacity,
                    "label": str(item.get("title") or signal_column),
                    "maxPixelHeight": bounded_float(presentation.get("maxPixelHeight"), default=0.0, lower=0.0, upper=96.0),
                    "minPixelHeight": bounded_float(presentation.get("minPixelHeight"), default=0.0, lower=0.0, upper=32.0),
                    "zoneHeightMode": zone_height_mode,
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
        role = str(presentation.get("chartRole") or "")
        if role not in {"marker", "text_label"}:
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
            marker = {
                "displayItemId": str(item.get("id") or ""),
                "time": timestamp,
                "position": str(presentation.get("markerPosition") or "belowBar"),
                "color": str(presentation.get("color") or "#1E3A5F"),
                "shape": str(presentation.get("markerShape") or "circle"),
                "size": bounded_float(presentation.get("markerSize"), default=0.1 if role == "text_label" else 1.0, lower=0.1, upper=4.0),
            }
            label = display_marker_text(item, presentation, role)
            if label:
                marker["text"] = label
            markers.append(marker)
            if len(markers) >= marker_limit:
                return markers
    return markers


def display_marker_text(item: dict[str, Any], presentation: dict[str, Any], role: str) -> str:
    explicit = str(presentation.get("labelText") or "").strip()
    label_mode = str(presentation.get("labelMode") or ("short" if role == "text_label" else "none"))
    if label_mode == "none" and role != "text_label" and not explicit:
        return ""
    if explicit:
        return explicit[:24]
    if label_mode == "full":
        return str(item.get("title") or "Feature")[:32]
    signal = str(presentation.get("signalColumn") or "")
    return short_event_label(signal or str(item.get("id") or item.get("title") or "Feature"))


def short_event_label(value: str) -> str:
    key = value.lower().split(".")[-1]
    labels = {
        "higher_high": "HH",
        "lower_low": "LL",
        "swing_high_3": "SH3",
        "swing_low_3": "SL3",
        "swing_high_5": "SH5",
        "swing_low_5": "SL5",
        "bos_up": "BOS+",
        "bos_down": "BOS-",
        "breaks_high20": "BH20",
        "breaks_low20": "BL20",
        "bullish_fvg": "FVG+",
        "bearish_fvg": "FVG-",
        "bullish_displacement": "OB+",
        "bearish_displacement": "OB-",
    }
    if key in labels:
        return labels[key]
    words = [part for part in re.split(r"[^A-Za-z0-9]+", key) if part]
    if not words:
        return "EV"
    return "".join(word[0] for word in words[:4]).upper()


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
    spec = supervision_label_spec(supervision_group)
    if spec:
        return supervision_label_candidates(frame, spec, min_confidence)
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


SUPERVISION_LABEL_SPECS: dict[str, dict[str, str]] = {
    "bar:oracle_long_entry_signal": {"source": "bar", "signal": "oracle_long_entry_signal", "confidence": "oracle_long_entry_confidence", "kind": "bar_entry"},
    "bar:oracle_long_exit_signal": {"source": "bar", "signal": "oracle_long_exit_signal", "confidence": "oracle_long_exit_confidence", "kind": "bar_exit"},
    "bar:mfe_before_mae": {"source": "bar", "signal": "mfe_before_mae", "confidence": "path_efficiency", "kind": "bar_path"},
    "bar:fwd_liquidity_confirmed": {"source": "bar", "signal": "fwd_liquidity_confirmed", "confidence": "fwd_liquidity_quality_score", "kind": "bar_liquidity"},
    "bar:fwd_volume_shock_before_mfe": {"source": "bar", "signal": "fwd_volume_shock_before_mfe", "confidence": "fwd_liquidity_quality_score", "kind": "bar_volume_sequence"},
    "method:method_entry_signal": {"source": "method", "signal": "method_entry_signal", "confidence": "method_confidence", "kind": "method_entry"},
    "method:method_exit_signal": {"source": "method", "signal": "method_exit_signal", "confidence": "method_confidence", "kind": "method_exit"},
    "scanner:is_top_1": {"source": "scanner", "signal": "is_top_1", "confidence": "method_confidence", "kind": "scanner_rank"},
    "scanner:is_top_3": {"source": "scanner", "signal": "is_top_3", "confidence": "method_confidence", "kind": "scanner_rank"},
    "scanner:is_top_5": {"source": "scanner", "signal": "is_top_5", "confidence": "method_confidence", "kind": "scanner_rank"},
    "scanner:is_top_10": {"source": "scanner", "signal": "is_top_10", "confidence": "method_confidence", "kind": "scanner_rank"},
    "scanner:is_top_1pct": {"source": "scanner", "signal": "is_top_1pct", "confidence": "method_confidence", "kind": "scanner_rank"},
    "scanner:is_top_5pct": {"source": "scanner", "signal": "is_top_5pct", "confidence": "method_confidence", "kind": "scanner_rank"},
}

DEFAULT_SUPERVISION_LABELS: dict[str, list[str]] = {
    "bar": ["bar:oracle_long_entry_signal", "bar:oracle_long_exit_signal"],
    "method": ["method:method_entry_signal", "method:method_exit_signal"],
    "scanner": ["scanner:is_top_3"],
}


def supervision_label_spec(value: str) -> dict[str, str] | None:
    return SUPERVISION_LABEL_SPECS.get(str(value).lower())


def selected_supervision_label_specs(values: list[str]) -> list[tuple[str, dict[str, str]]]:
    specs: list[tuple[str, dict[str, str]]] = []
    seen: set[str] = set()
    for value in values:
        key = str(value).lower()
        expanded = DEFAULT_SUPERVISION_LABELS.get(key, [key])
        for item_key in expanded:
            spec = supervision_label_spec(item_key)
            if not spec or item_key in seen:
                continue
            seen.add(item_key)
            specs.append((item_key, spec))
    return specs


def supervision_label_candidates(frame: pl.DataFrame, spec: dict[str, str], min_confidence: float) -> pl.DataFrame:
    signal = spec["signal"]
    if signal not in frame.columns:
        return pl.DataFrame()
    candidates = frame.filter(pl.col(signal) == True)
    confidence_column = spec.get("confidence", "")
    if confidence_column and confidence_column in candidates.columns:
        scored = candidates.filter(pl.col(confidence_column) >= min_confidence)
        if not scored.is_empty():
            candidates = scored
        candidates = candidates.sort(confidence_column, descending=True)
    elif "oracle_rank" in candidates.columns:
        candidates = candidates.sort("oracle_rank")
    if "bar_id" in candidates.columns:
        candidates = candidates.unique(subset=["bar_id"], keep="first", maintain_order=True)
    sort_column = "bar_time_utc" if "bar_time_utc" in candidates.columns else "bar_time_market" if "bar_time_market" in candidates.columns else None
    return candidates.sort(sort_column) if sort_column else candidates


def supervision_display_id(supervision_group: str) -> str:
    return f"supervision:{supervision_group}"


def method_short_name(method: Any) -> str:
    value = str(method or "method").upper()
    aliases = {
        "PRICE_VOLUME_SHOCK": "PVS",
        "MOMENTUM_SCALP": "MOM",
    }
    return aliases.get(value, display_name(value).replace(" ", "")[:8].upper() or "METHOD")


def percent_label(value: Any, default: str = "-") -> str:
    numeric = numeric_or_none(value)
    if numeric is None:
        return default
    return f"{numeric * 100:.2f}%"


def confidence_label(value: Any) -> str:
    numeric = numeric_or_none(value)
    if numeric is None:
        return ""
    return f" c{numeric:.2f}"


def numeric_or_none(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if numeric == numeric else None


def int_or_default(value: Any, default: int = 1) -> int:
    numeric = numeric_or_none(value)
    if numeric is None:
        return default
    return int(numeric)


def integer_label(value: Any, default: str = "-") -> str:
    numeric = numeric_or_none(value)
    if numeric is None:
        return default
    return str(int(numeric))


def marker_text(row: dict[str, Any], supervision_group: str) -> str:
    spec = supervision_label_spec(supervision_group)
    source = spec["source"] if spec else supervision_group
    kind = spec.get("kind") if spec else ""
    if source == "scanner":
        method = method_short_name(row.get("trade_method"))
        return f"#{integer_label(row.get('oracle_rank'))} {method} {percent_label(row.get('method_best_return'))}"
    if source == "method":
        method = method_short_name(row.get("trade_method"))
        if kind == "method_exit":
            return f"IGNORE {method} {confidence_label(row.get('method_confidence')).strip()}"
        return f"{method} {percent_label(row.get('method_best_return'))}{confidence_label(row.get('method_confidence'))}"
    if kind == "bar_exit":
        return f"EXIT h{integer_label(row.get('horizon_bars') or row.get('horizon'))} risk {percent_label(row.get('fwd_mae'))}{confidence_label(row.get('oracle_long_exit_confidence'))}"
    if kind == "bar_liquidity":
        return f"LIQ h{integer_label(row.get('horizon_bars') or row.get('horizon'))} q{confidence_value(row.get('fwd_liquidity_quality_score'))}"
    if kind == "bar_volume_sequence":
        return f"VOL<MFE h{integer_label(row.get('horizon_bars') or row.get('horizon'))}"
    if kind == "bar_path":
        return f"MFE<MAE h{integer_label(row.get('horizon_bars') or row.get('horizon'))}"
    return f"BAR h{integer_label(row.get('horizon_bars') or row.get('horizon'))} {percent_label(row.get('oracle_best_exit_return') or row.get('fwd_mfe'))}{confidence_label(row.get('oracle_long_entry_confidence'))}"


def supervision_marker_size(supervision_group: str, row: dict[str, Any]) -> float:
    spec = supervision_label_spec(supervision_group)
    source = spec["source"] if spec else supervision_group
    if source == "scanner":
        rank = numeric_or_none(row.get("oracle_rank"))
        if rank == 1:
            return 1.45
        if rank is not None and rank <= 3:
            return 1.25
        return 1.0
    confidence_column = spec.get("confidence") if spec else ("method_confidence" if source == "method" else "oracle_long_entry_confidence")
    confidence = numeric_or_none(row.get(confidence_column))
    if confidence is not None and confidence >= 0.85:
        return 1.35
    return 1.1


def confidence_value(value: Any) -> str:
    numeric = numeric_or_none(value)
    if numeric is None:
        return "-"
    return f"{numeric:.2f}"


def supervision_annotations(
    provider: MarketDataProvider,
    rows: list[dict[str, Any]],
    *,
    start_date: date,
    end_date: date,
    timeframe: str,
    ticker: str,
    supervision_groups: list[str],
    marker_limit: int,
    min_confidence: float,
    catalog: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    markers: list[dict[str, Any]] = []
    zones: list[dict[str, Any]] = []
    if marker_limit <= 0:
        return markers, zones
    bars_by_id = {str(row.get("bar_id")): row for row in rows if row.get("bar_id") is not None}
    candle_duration = chart_candle_duration_seconds(rows, timeframe)
    specs = selected_supervision_label_specs(supervision_groups)
    if not specs:
        return markers, zones
    group_limit = max(1, marker_limit // max(1, len(specs)))
    frames: dict[str, pl.DataFrame] = {}
    for supervision_group, spec in specs:
        source = spec["source"]
        if source not in frames:
            frames[source] = provider.load_supervision(start_date=start_date, end_date=end_date, timeframe=timeframe, supervision_type=source, tickers=[ticker])
        candidates = supervision_candidates(frames[source], supervision_group, min_confidence)
        for row in candidates.head(group_limit).to_dicts():
            timestamp = chart_timestamp_seconds(row, timeframe)
            if not timestamp:
                continue
            color, shape, position = supervision_marker_style(catalog, supervision_group, row)
            markers.append(
                {
                    "displayItemId": supervision_display_id(supervision_group),
                    "time": timestamp,
                    "position": position,
                    "color": color,
                    "shape": shape,
                    "size": supervision_marker_size(supervision_group, row),
                    "text": marker_text(row, supervision_group),
                }
            )
            source_bar = bars_by_id.get(str(row.get("bar_id")), {})
            zones.extend(supervision_price_zones(row, supervision_group, source_bar, timestamp, candle_duration))
            if len(markers) >= marker_limit:
                return sorted(markers, key=lambda marker: int(marker.get("time") or 0)), zones
    markers.sort(key=lambda marker: int(marker.get("time") or 0))
    zones.sort(key=lambda zone: int(zone.get("start") or 0))
    return markers[:marker_limit], zones


def supervision_price_zones(
    row: dict[str, Any],
    supervision_group: str,
    source_bar: dict[str, Any],
    start: int,
    candle_duration: int,
) -> list[dict[str, Any]]:
    spec = supervision_label_spec(supervision_group)
    source = spec["source"] if spec else supervision_group
    kind = spec.get("kind") if spec else ""
    close = numeric_or_none(source_bar.get("close"))
    if close is None or close <= 0:
        return []
    if source == "bar":
        target_price = numeric_or_none(row.get("oracle_best_exit_price"))
        target_return = numeric_or_none(row.get("oracle_best_exit_return") or row.get("fwd_mfe"))
        horizon_bars = int_or_default(row.get("horizon_bars"), 1)
        target_end = timestamp_seconds(row.get("oracle_best_exit_time_utc")) or start + horizon_bars * candle_duration
        target_label = f"BAR h{integer_label(row.get('horizon_bars'))} target {percent_label(target_return)}"
        risk_return = numeric_or_none(row.get("fwd_mae"))
        risk_end = start + max(1, int_or_default(row.get("time_to_mae_bars") or row.get("horizon_bars"), 1)) * candle_duration
        target_color = "#067647"
    elif source == "method":
        target_return = numeric_or_none(row.get("method_best_return"))
        target_price = numeric_or_none(row.get("method_best_price"))
        if target_price is None and target_return is not None:
            target_price = close * (1.0 + target_return)
        target_end = timestamp_seconds(row.get("method_best_exit_time_utc")) or start + int_or_default(row.get("method_best_horizon_minutes"), 1) * 60
        target_label = f"{method_short_name(row.get('trade_method'))} target {percent_label(target_return)}"
        risk_return = numeric_or_none(row.get("method_mae_before_best"))
        target_color = "#2563EB"
        risk_end = target_end
    elif source == "scanner":
        target_return = numeric_or_none(row.get("method_best_return"))
        target_price = close * (1.0 + target_return) if target_return is not None else None
        target_end = start + int_or_default(row.get("method_best_horizon_minutes"), 1) * 60
        target_label = f"SCAN #{integer_label(row.get('oracle_rank'))} target {percent_label(target_return)}"
        risk_return = numeric_or_none(row.get("method_mae_before_best"))
        target_color = "#7C3AED"
        risk_end = target_end
    else:
        return []
    zones: list[dict[str, Any]] = []
    show_target = kind not in {"bar_exit", "method_exit", "bar_liquidity", "bar_volume_sequence"}
    if show_target and target_price is not None and target_price > close:
        zones.append(
            supervision_zone(
                supervision_group,
                start=start,
                end=max(start + candle_duration, int(target_end)),
                lower=close,
                upper=target_price,
                color=target_color,
                label=target_label,
                fill_opacity=0.055,
                border_opacity=0.20,
            )
        )
    if risk_return is not None and risk_return < 0:
        risk_price = close * (1.0 + risk_return)
        zones.append(
            supervision_zone(
                supervision_group,
                start=start,
                end=max(start + candle_duration, int(risk_end)),
                lower=risk_price,
                upper=close,
                color="#B42318",
                label=f"Risk {percent_label(risk_return)}",
                fill_opacity=0.045,
                border_opacity=0.16,
            )
        )
    return zones


def supervision_zone(
    supervision_group: str,
    *,
    start: int,
    end: int,
    lower: float,
    upper: float,
    color: str,
    label: str,
    fill_opacity: float,
    border_opacity: float,
) -> dict[str, Any]:
    return {
        "displayItemId": supervision_display_id(supervision_group),
        "start": start,
        "end": end,
        "upper": max(upper, lower),
        "lower": min(upper, lower),
        "color": color,
        "borderColor": color,
        "borderOpacity": border_opacity,
        "borderStyle": "dashed",
        "borderWidth": 1,
        "fillColor": color,
        "fillOpacity": fill_opacity,
        "label": label,
        "minPixelHeight": 3.0,
        "maxPixelHeight": 0.0,
        "zoneHeightMode": "price_range",
    }


def supervision_marker_style(catalog: dict[str, Any], supervision_group: str, row: dict[str, Any]) -> tuple[str, str, str]:
    by_id = catalog_item_by_id(catalog)
    spec = supervision_label_spec(supervision_group)
    source = spec["source"] if spec else supervision_group
    signal = spec.get("signal") if spec else ""
    defaults = {
        "bar": ("#067647", "arrowUp", "belowBar"),
        "method": ("#2563EB", "arrowUp", "belowBar"),
        "scanner": ("#7C3AED", "square", "aboveBar"),
    }
    item = None
    if signal:
        item = by_id.get(signal)
    elif source == "method":
        item = by_id.get(f"method.{row.get('trade_method')}")
    elif source == "scanner":
        item = by_id.get("scanner.method_rank")
    elif source == "bar":
        item = by_id.get("oracle_long_entry_signal")
    default_color, default_shape, default_position = defaults.get(source, ("#1E3A5F", "circle", "belowBar"))
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
        "supervision_groups": [],
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
    supervision, supervision_zones = (
        ([], [])
        if not supervision_groups_selected or supervision_marker_limit <= 0
        else supervision_annotations(
            provider,
            rows,
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
    price_zones = [*display_price_zones(rows, timeframe, selected_display_contracts), *supervision_zones]
    return {
        "candles": candles,
        "volume": volume,
        "overlay_series": overlay_series,
        "oscillator_series": oscillator_series,
        "markers": markers,
        "regions": regions,
        "price_zones": price_zones,
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
