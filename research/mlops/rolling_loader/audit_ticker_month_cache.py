from __future__ import annotations

import argparse
import datetime as dt
import json
import random
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

import numpy as np

if __package__ in {None, ""}:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "research").is_dir():
            sys.path.insert(0, str(parent))
            break

from research.mlops.clickhouse import default_clickhouse_password, default_clickhouse_url, default_clickhouse_user, discover_clickhouse_env_files, parse_size_bytes, quote_ident, sql_string
from research.mlops.env import load_env_files
from research.mlops.rolling_loader.run_build_ticker_month_cache import (
    CONDITION_DIRECT_SOURCE_FAMILIES,
    CONDITION_INDICATOR_SOURCE_FAMILIES,
    FUTURE_CONDITION_GROUPS,
    FUTURE_EVENT_FLAG_LABEL_KEYS,
    query_polars,
)

SESSION_TIMEZONE = "America/New_York"
SESSION_END_SECOND = 20 * 60 * 60
from research.mlops.rolling_loader.ticker_month_cache import (
    TICKER_MONTH_CACHE_FORMAT,
    TICKER_MONTH_CACHE_VERSION,
    read_json,
    write_json_atomic,
)


@dataclass(frozen=True, slots=True)
class TickerMonthAuditConfig:
    cache_root: Path
    split: str = "train"
    sample_packages_per_month: int = 8
    source_checks: bool = True
    clickhouse_url: str = ""
    clickhouse_user: str = ""
    clickhouse_password: str = ""
    database: str = "market_sip_compact"
    sec_context_database: str = "market_sip_compact"
    events_table: str = "events"
    condition_token_reference_table: str = "event_condition_token_reference"
    news_embedding_table: str = "news_text_embeddings"
    sec_filing_text_embedding_table: str = "sec_filing_text_embeddings"
    source_label_samples_per_package: int = 2
    source_label_horizons_per_sample: int = 4
    max_threads: int = 8
    max_memory_usage: str = "80G"


@dataclass(slots=True)
class AuditIssue:
    severity: str
    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AuditResult:
    ok: bool
    status: str
    summary: dict[str, Any]
    report_path: str


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit ticker/month rolling SSD cache packages.")
    parser.add_argument("--cache-root", type=Path, required=True)
    parser.add_argument("--split", default="train")
    parser.add_argument("--sample-packages-per-month", type=int, default=8)
    parser.add_argument("--source-checks", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--clickhouse-url", default="")
    parser.add_argument("--user", default="")
    parser.add_argument("--password", default="")
    parser.add_argument("--database", default="market_sip_compact")
    parser.add_argument("--sec-context-database", default="market_sip_compact")
    parser.add_argument("--events-table", default="events")
    parser.add_argument("--condition-token-reference-table", default="event_condition_token_reference")
    parser.add_argument("--news-embedding-table", default="news_text_embeddings")
    parser.add_argument("--sec-filing-text-embedding-table", default="sec_filing_text_embeddings")
    parser.add_argument("--source-label-samples-per-package", type=int, default=2)
    parser.add_argument("--source-label-horizons-per-sample", type=int, default=4)
    parser.add_argument("--max-threads", type=int, default=8)
    parser.add_argument("--max-memory-usage", default="80G")
    parser.add_argument("--env-file", type=Path, default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    env_files = discover_clickhouse_env_files()
    if args.env_file is not None:
        env_files.append(args.env_file)
    loaded = load_env_files(env_files, verbose=False)
    if loaded:
        print("Loaded .env files: " + ", ".join(str(path) for path in loaded), flush=True)
    result = run_audit(
        TickerMonthAuditConfig(
            cache_root=args.cache_root,
            split=args.split,
            sample_packages_per_month=max(0, int(args.sample_packages_per_month)),
            source_checks=bool(args.source_checks),
            clickhouse_url=args.clickhouse_url or default_clickhouse_url(),
            clickhouse_user=args.user or default_clickhouse_user(),
            clickhouse_password=args.password or default_clickhouse_password(),
            database=args.database,
            sec_context_database=args.sec_context_database,
            events_table=args.events_table,
            condition_token_reference_table=args.condition_token_reference_table,
            news_embedding_table=args.news_embedding_table,
            sec_filing_text_embedding_table=args.sec_filing_text_embedding_table,
            source_label_samples_per_package=max(0, int(args.source_label_samples_per_package)),
            source_label_horizons_per_sample=max(0, int(args.source_label_horizons_per_sample)),
            max_threads=max(1, int(args.max_threads)),
            max_memory_usage=str(args.max_memory_usage),
        )
    )
    print(json.dumps(result.summary, indent=2, sort_keys=True), flush=True)
    print(f"AUDIT {result.status} report={result.report_path}", flush=True)
    return 0 if result.ok else 2


def run_audit(config: TickerMonthAuditConfig) -> AuditResult:
    issues: list[AuditIssue] = []
    root = Path(config.cache_root)
    manifest_path = root / "manifest.json"
    manifest = _read_manifest(manifest_path, issues)
    _check_root_manifest(manifest, issues)
    package_dirs = _discover_package_dirs(root, config.split, manifest, issues)
    sampled = _sample_packages(package_dirs, max(0, int(config.sample_packages_per_month)))
    totals = {
        "packages": len(package_dirs),
        "sampled_packages": len(sampled),
        "events": 0,
        "origins": 0,
        "labels": 0,
        "source_origins_checked": 0,
        "source_label_horizons_checked": 0,
    }
    client_opts = {"clickhouse_url": config.clickhouse_url, "user": config.clickhouse_user, "password": config.clickhouse_password}
    for package_dir in sampled:
        _audit_package(package_dir, issues, totals, config, client_opts)
    status = "passed" if not any(issue.severity == "error" for issue in issues) else "failed"
    report = {
        "status": status,
        "ok": status == "passed",
        "generated_at": dt.datetime.now(tz=dt.timezone.utc).isoformat(),
        "summary": totals,
        "issues": [issue.__dict__ for issue in issues],
    }
    report_path = root / f"{config.split}_ticker_month_audit_report.json"
    write_json_atomic(report_path, report)
    return AuditResult(ok=status == "passed", status=status, summary={"totals": totals, "issues": _issue_counts(issues)}, report_path=str(report_path))


def _audit_package(package_dir: Path, issues: list[AuditIssue], totals: dict[str, int], config: TickerMonthAuditConfig, client_opts: Mapping[str, str]) -> None:
    manifest_path = package_dir / "manifest.json"
    manifest = _read_manifest(manifest_path, issues)
    if not manifest:
        return
    files = manifest.get("files") or {}
    context_required = ("ticker_news_embeddings", "sec_filing_embeddings", "xbrl", "daily_bars")
    for key in context_required:
        rel = files.get(key)
        if not rel or not (package_dir / str(rel)).exists():
            issues.append(AuditIssue("error", "missing_file", f"Missing package file {key}.", {"package": str(package_dir), "file": rel}))
    part_specs = _package_part_specs(manifest)
    if not part_specs:
        issues.append(AuditIssue("error", "missing_parts", "Package manifest has no event parts.", {"package": str(package_dir)}))
        return
    try:
        import polars as pl  # type: ignore
    except ModuleNotFoundError as exc:
        issues.append(AuditIssue("error", "polars_missing", str(exc)))
        return
    for part in part_specs:
        part_id = int(part.get("part_id") or 0)
        part_files = part.get("files") or {}
        for key in ("events", "origins", "event_window_index", "ranges", "intraday_forward_labels"):
            rel = part_files.get(key)
            if not rel or not (package_dir / str(rel)).exists():
                issues.append(AuditIssue("error", "missing_file", f"Missing part file {key}.", {"package": str(package_dir), "part_id": part_id, "file": rel}))
        try:
            events = pl.read_parquet(package_dir / str(part_files.get("events", "events.parquet")))
            origins = pl.read_parquet(package_dir / str(part_files.get("origins", "origins.parquet")))
            labels = pl.read_parquet(package_dir / str(part_files.get("intraday_forward_labels", "intraday_forward_labels.parquet")))
            windows = pl.read_parquet(package_dir / str(part_files.get("event_window_index", "event_window_index.parquet")))
        except Exception as exc:
            issues.append(AuditIssue("error", "read_failed", f"Failed to read parquet: {exc!r}", {"package": str(package_dir), "part_id": part_id}))
            continue
        totals["events"] += int(events.height)
        totals["origins"] += int(origins.height)
        totals["labels"] += int(labels.height)
        _check_part_origin_bounds(origins, part, issues, package_dir)
        _check_event_order(events, issues, package_dir)
        _check_windows(events, origins, windows, manifest, issues, package_dir)
        _check_labels(origins, labels, manifest, issues, package_dir)
        if config.source_checks and origins.height:
            _source_check_origin(origins, manifest, config, client_opts, issues, package_dir)
            _source_check_labels(origins, labels, manifest, config, client_opts, issues, totals, package_dir)


def _package_part_specs(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    parts = manifest.get("parts")
    if isinstance(parts, list) and parts:
        return [part for part in parts if isinstance(part, dict)]
    files = manifest.get("files") or {}
    legacy_keys = ("events", "origins", "event_window_index", "ranges", "intraday_forward_labels")
    if all(key in files for key in legacy_keys):
        return [{"part_id": 0, "files": {key: files[key] for key in legacy_keys}}]
    return []


def _check_part_origin_bounds(origins: Any, part: Mapping[str, Any], issues: list[AuditIssue], package_dir: Path) -> None:
    if not origins.height:
        return
    start = part.get("origin_ordinal_start")
    end = part.get("origin_ordinal_end")
    if start is None or end is None:
        return
    ordinals = origins.get_column("origin_ordinal").to_numpy()
    if (ordinals < int(start)).any() or (ordinals > int(end)).any():
        issues.append(AuditIssue("error", "part_origin_bounds", "Part contains origins outside its ordinal bounds.", {"package": str(package_dir), "part_id": int(part.get("part_id") or 0), "origin_ordinal_start": int(start), "origin_ordinal_end": int(end)}))


def _check_event_order(events: Any, issues: list[AuditIssue], package_dir: Path) -> None:
    if events.height <= 1:
        return
    ordinals = events.get_column("ordinal").to_numpy()
    diffs = ordinals[1:] - ordinals[:-1]
    if (diffs < 0).any():
        issues.append(AuditIssue("error", "event_order", "Events are not sorted by ordinal.", {"package": str(package_dir)}))


def _check_windows(events: Any, origins: Any, windows: Any, manifest: Mapping[str, Any], issues: list[AuditIssue], package_dir: Path) -> None:
    if not origins.height:
        return
    events_per_chunk = int(((manifest.get("config") or {}).get("events_per_chunk")) or 0)
    if events_per_chunk <= 0:
        issues.append(AuditIssue("error", "events_per_chunk_missing", "Missing events_per_chunk.", {"package": str(package_dir)}))
        return
    ordinals = events.get_column("ordinal").to_numpy()
    sample_count = min(128, int(windows.height))
    for idx in range(sample_count):
        row = windows.row(idx, named=True)
        for key, value in row.items():
            if not str(key).startswith("window_start_"):
                continue
            start = int(value)
            end = start + events_per_chunk - 1
            if start < 0 or end >= len(ordinals):
                issues.append(AuditIssue("error", "window_bounds", "Event window is out of bounds.", {"package": str(package_dir), "row": idx, "column": key}))
                return
            if int(ordinals[end]) - int(ordinals[start]) != events_per_chunk - 1:
                issues.append(AuditIssue("error", "window_gap", "Event window crosses an ordinal gap.", {"package": str(package_dir), "row": idx, "column": key}))
                return


def _check_labels(origins: Any, labels: Any, manifest: Mapping[str, Any], issues: list[AuditIssue], package_dir: Path) -> None:
    if not origins.height:
        return
    if not labels.height:
        issues.append(AuditIssue("warning", "labels_empty", "No intraday forward labels for package with origins.", {"package": str(package_dir)}))
        return
    origin_count = int(origins.height)
    labeled_origins = int(labels.select("origin_key").unique().height) if "origin_key" in labels.columns else 0
    if labeled_origins < max(1, origin_count // 2):
        issues.append(AuditIssue("warning", "label_origin_coverage_low", "Intraday label origin coverage is low.", {"package": str(package_dir), "origins": origin_count, "labeled_origins": labeled_origins}))
    if "origin_key" in labels.columns and int(labels.select("origin_key").unique().height) != int(labels.height):
        issues.append(AuditIssue("error", "label_origin_duplicate", "Compact intraday labels contain duplicate origin keys.", {"package": str(package_dir), "labels": int(labels.height), "unique_origin_keys": labeled_origins}))
        return
    if _labels_are_pivoted(labels):
        expected = len(manifest.get("config", {}).get("intraday_label_horizons") or ())
        if expected <= 0:
            expected = None
        base_compact_label_keys = (
            "horizon_us",
            "price_primary_int",
            "price_secondary_int",
            "size_primary_sum",
            "size_secondary_sum",
            "event_count",
            "last_event_timestamp_us",
            "available",
        )
        flag_keys = (
            manifest.get("config", {}).get("future_event_flag_label_keys")
            or (
                *(manifest.get("config", {}).get("future_condition_label_keys") or ()),
                *(manifest.get("config", {}).get("future_external_arrival_label_keys") or ()),
            )
        )
        compact_label_keys = (*base_compact_label_keys, *flag_keys)
        for key in compact_label_keys:
            if key not in labels.columns:
                issues.append(AuditIssue("error", "label_column_missing", "Compact intraday label column is missing.", {"package": str(package_dir), "column": key}))
                return
        sample_count = min(8, int(labels.height))
        for idx in random.sample(range(int(labels.height)), sample_count):
            row = labels.row(idx, named=True)
            for key in compact_label_keys:
                value_count = len(_cell_list(row.get(key)))
                if expected is not None and value_count != expected:
                    issues.append(AuditIssue("error", "compact_label_width", "Compact intraday label width does not match configured horizons.", {"package": str(package_dir), "row": idx, "column": key, "values": value_count, "expected": expected}))
                    return
            horizon_us = _cell_array(row.get("horizon_us"), np.int64)
            if horizon_us.size > 1 and bool(np.any(horizon_us[1:] <= horizon_us[:-1])):
                issues.append(AuditIssue("error", "compact_label_horizon_order", "Compact intraday label horizons are not strictly increasing.", {"package": str(package_dir), "row": idx}))
                return
            available = _cell_array(row.get("available"), np.uint8)
            event_count = _cell_array(row.get("event_count"), np.uint64)
            last_ts = _cell_array(row.get("last_event_timestamp_us"), np.int64)
            origin_ts = int(row.get("origin_timestamp_us") or 0)
            if available.size and bool(np.any((available != 0) & (available != 1))):
                issues.append(AuditIssue("error", "available_not_binary", "Compact label available values are not binary.", {"package": str(package_dir), "row": idx}))
                return
            valid = available.astype(bool, copy=False)
            if bool(valid.any()):
                if bool(np.any(event_count[valid] <= 0)):
                    issues.append(AuditIssue("error", "available_event_count_zero", "Available labels contain zero event_count.", {"package": str(package_dir), "row": idx}))
                    return
                if bool(np.any(last_ts[valid] <= origin_ts)):
                    issues.append(AuditIssue("error", "label_not_forward", "Available labels are not strictly forward-looking.", {"package": str(package_dir), "row": idx, "origin_timestamp_us": origin_ts}))
                    return
                if bool(np.any(last_ts[valid] > origin_ts + horizon_us[valid])):
                    issues.append(AuditIssue("error", "label_horizon_overrun", "Available labels exceed their horizon.", {"package": str(package_dir), "row": idx, "origin_timestamp_us": origin_ts}))
                    return
            for key in flag_keys:
                flags = _cell_array(row.get(key), np.uint8)
                if flags.size and bool(np.any((flags != 0) & (flags != 1))):
                    issues.append(AuditIssue("error", "future_flag_not_binary", "Future event flag values are not binary.", {"package": str(package_dir), "row": idx, "column": key}))
                    return
                invalid_session = np.asarray([not _horizon_inside_session(origin_ts, int(value)) for value in horizon_us], dtype=np.bool_)
                if invalid_session.size and flags.size == invalid_session.size and bool(np.any(flags[invalid_session] != 0)):
                    issues.append(AuditIssue("error", "future_flag_crosses_session", "Future event flag is set for a horizon that crosses the NY session end.", {"package": str(package_dir), "row": idx, "column": key}))
                    return


def _labels_are_pivoted(labels: Any) -> bool:
    if labels is None or int(getattr(labels, "height", 0) or 0) <= 0 or "horizon_us" not in labels.columns:
        return False
    dtype_text = str(labels.schema.get("horizon_us", "")).lower()
    return "list" in dtype_text or "array" in dtype_text


def _cell_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if hasattr(value, "to_list"):
        return list(value.to_list())
    if hasattr(value, "to_numpy"):
        return list(value.to_numpy())
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _cell_array(value: Any, dtype: Any) -> np.ndarray:
    return np.asarray(_cell_list(value), dtype=dtype)


def _horizon_inside_session(origin_timestamp_us: int, horizon_us: int) -> bool:
    local = dt.datetime.fromtimestamp(int(origin_timestamp_us) / 1_000_000.0, tz=dt.timezone.utc).astimezone(ZoneInfo(SESSION_TIMEZONE))
    local_us = ((local.hour * 3600 + local.minute * 60 + local.second) * 1_000_000) + local.microsecond
    return int(local_us) + int(horizon_us) <= SESSION_END_SECOND * 1_000_000


def _source_check_origin(origins: Any, manifest: Mapping[str, Any], config: TickerMonthAuditConfig, client_opts: Mapping[str, str], issues: list[AuditIssue], package_dir: Path) -> None:
    idx = random.Random(17).randrange(int(origins.height))
    row = origins.row(idx, named=True)
    ticker = str(row["ticker"])
    ordinal = int(row["origin_ordinal"])
    table = f"{quote_ident(config.database)}.{quote_ident(config.events_table)}"
    query = f"""
SELECT
    ticker,
    ordinal,
    sip_timestamp_us
FROM {table}
WHERE ticker = {sql_string(ticker)}
  AND ordinal = {ordinal}
LIMIT 2
{_settings_sql(config)}
"""
    try:
        frame = query_polars(client_opts, query)
    except Exception as exc:
        issues.append(AuditIssue("error", "source_check_failed", f"ClickHouse source check failed: {exc!r}", {"package": str(package_dir), "ticker": ticker, "ordinal": ordinal}))
        return
    if int(frame.height) != 1:
        issues.append(AuditIssue("error", "source_origin_missing", "Origin does not resolve to exactly one source event.", {"package": str(package_dir), "ticker": ticker, "ordinal": ordinal, "rows": int(frame.height)}))
        return
    source_ts = int(frame.get_column("sip_timestamp_us")[0])
    if source_ts != int(row["origin_timestamp_us"]):
        issues.append(AuditIssue("error", "source_origin_mismatch", "Origin timestamp does not match source.", {"package": str(package_dir), "ticker": ticker, "ordinal": ordinal, "cache_ts": int(row["origin_timestamp_us"]), "source_ts": source_ts}))


def _source_check_labels(
    origins: Any,
    labels: Any,
    manifest: Mapping[str, Any],
    config: TickerMonthAuditConfig,
    client_opts: Mapping[str, str],
    issues: list[AuditIssue],
    totals: dict[str, int],
    package_dir: Path,
) -> None:
    if not _labels_are_pivoted(labels) or int(labels.height) <= 0:
        return
    samples = min(max(0, int(config.source_label_samples_per_package)), int(labels.height))
    horizon_samples = max(0, int(config.source_label_horizons_per_sample))
    if samples <= 0 or horizon_samples <= 0:
        return
    rng = random.Random(f"source-label|{package_dir.as_posix()}")
    label_rows = list(range(int(labels.height))) if int(labels.height) <= samples else sorted(rng.sample(range(int(labels.height)), samples))
    for row_index in label_rows:
        row = labels.row(int(row_index), named=True)
        ticker = str(row.get("ticker") or "").upper()
        origin_timestamp_us = int(row.get("origin_timestamp_us") or 0)
        origin_key = str(row.get("origin_key") or f"{ticker}|{row.get('origin_ordinal')}")
        horizons = _cell_array(row.get("horizon_us"), np.int64)
        if horizons.size <= 0:
            continue
        cached = _label_arrays_for_row(row)
        for horizon_index in _sample_horizon_indexes(int(horizons.size), horizon_samples, rng):
            try:
                expected = _query_source_label_for_horizon(
                    ticker=ticker,
                    origin_timestamp_us=origin_timestamp_us,
                    horizon_us=int(horizons[int(horizon_index)]),
                    config=config,
                    client_opts=client_opts,
                )
            except Exception as exc:
                issues.append(AuditIssue("error", "source_label_query_failed", f"Independent source label query failed: {exc!r}", {"package": str(package_dir), "origin_key": origin_key, "horizon_index": int(horizon_index)}))
                return
            _compare_source_label(cached=cached, expected=expected, horizon_index=int(horizon_index), issues=issues, package_dir=package_dir, origin_key=origin_key)
            totals["source_label_horizons_checked"] += 1
        totals["source_origins_checked"] += 1


def _sample_horizon_indexes(width: int, samples: int, rng: random.Random) -> list[int]:
    width = max(0, int(width))
    samples = max(0, min(int(samples), width))
    if width <= 0 or samples <= 0:
        return []
    anchors = {0, width // 2, width - 1}
    if len(anchors) >= samples:
        return sorted(anchors)[:samples]
    remaining = [idx for idx in range(width) if idx not in anchors]
    return sorted(anchors.union(rng.sample(remaining, samples - len(anchors))))


def _label_arrays_for_row(row: Mapping[str, Any]) -> dict[str, np.ndarray]:
    arrays = {
        "price_primary_int": _cell_array(row.get("price_primary_int"), np.float32),
        "price_secondary_int": _cell_array(row.get("price_secondary_int"), np.float32),
        "size_primary_sum": _cell_array(row.get("size_primary_sum"), np.float32),
        "size_secondary_sum": _cell_array(row.get("size_secondary_sum"), np.float32),
        "event_count": _cell_array(row.get("event_count"), np.uint64),
        "last_event_timestamp_us": _cell_array(row.get("last_event_timestamp_us"), np.int64),
        "available": _cell_array(row.get("available"), np.uint8),
    }
    for key in FUTURE_EVENT_FLAG_LABEL_KEYS:
        if key in row:
            arrays[key] = _cell_array(row.get(key), np.uint8)
    return arrays


def _compare_source_label(
    *,
    cached: Mapping[str, np.ndarray],
    expected: Mapping[str, Any],
    horizon_index: int,
    issues: list[AuditIssue],
    package_dir: Path,
    origin_key: str,
) -> None:
    float_fields = ("price_primary_int", "price_secondary_int", "size_primary_sum", "size_secondary_sum")
    int_fields = ("event_count", "last_event_timestamp_us", "available", *FUTURE_EVENT_FLAG_LABEL_KEYS)
    for key in int_fields:
        if key not in cached or int(cached[key].shape[0]) <= int(horizon_index):
            issues.append(AuditIssue("error", "source_label_cached_field_missing", "Cached label field is missing or too short.", {"package": str(package_dir), "origin_key": origin_key, "horizon_index": int(horizon_index), "field": key}))
            continue
        actual = int(cached[key][int(horizon_index)])
        exp = int(expected.get(key, 0) or 0)
        if actual != exp:
            issues.append(AuditIssue("error", "source_label_mismatch", "Cached future label does not match independent ClickHouse source recomputation.", {"package": str(package_dir), "origin_key": origin_key, "horizon_index": int(horizon_index), "field": key, "cached": actual, "expected": exp}))
    for key in float_fields:
        if key not in cached or int(cached[key].shape[0]) <= int(horizon_index):
            issues.append(AuditIssue("error", "source_label_cached_field_missing", "Cached label field is missing or too short.", {"package": str(package_dir), "origin_key": origin_key, "horizon_index": int(horizon_index), "field": key}))
            continue
        actual = float(cached[key][int(horizon_index)])
        exp = float(expected.get(key, 0.0) or 0.0)
        if abs(actual - exp) > 1e-3:
            issues.append(AuditIssue("error", "source_label_mismatch", "Cached future label does not match independent ClickHouse source recomputation.", {"package": str(package_dir), "origin_key": origin_key, "horizon_index": int(horizon_index), "field": key, "cached": actual, "expected": exp}))


def _query_source_label_for_horizon(
    *,
    ticker: str,
    origin_timestamp_us: int,
    horizon_us: int,
    config: TickerMonthAuditConfig,
    client_opts: Mapping[str, str],
) -> Mapping[str, Any]:
    query = _source_label_query_sql(ticker=ticker, origin_timestamp_us=origin_timestamp_us, horizon_us=horizon_us, config=config)
    frame = query_polars(client_opts, query)
    if int(frame.height) != 1:
        raise RuntimeError(f"Independent source label query returned {int(frame.height)} rows.")
    return frame.row(0, named=True)


def _source_label_query_sql(*, ticker: str, origin_timestamp_us: int, horizon_us: int, config: TickerMonthAuditConfig) -> str:
    event_table = f"{quote_ident(config.database)}.{quote_ident(config.events_table)}"
    news_table = f"{quote_ident(config.database)}.{quote_ident(config.news_embedding_table)}"
    sec_table = f"{quote_ident(config.sec_context_database)}.{quote_ident(config.sec_filing_text_embedding_table)}"
    end_timestamp_us = int(origin_timestamp_us) + int(horizon_us)
    return f"""
WITH
    toDate(toTimeZone(fromUnixTimestamp64Micro({int(origin_timestamp_us)}, 'UTC'), {sql_string(SESSION_TIMEZONE)})) AS origin_local_date,
    dateDiff('microsecond', toStartOfDay(toTimeZone(fromUnixTimestamp64Micro({int(origin_timestamp_us)}, 'UTC'), {sql_string(SESSION_TIMEZONE)})), toTimeZone(fromUnixTimestamp64Micro({int(origin_timestamp_us)}, 'UTC'), {sql_string(SESSION_TIMEZONE)})) AS origin_local_us,
    toUInt8((origin_local_us + {int(horizon_us)}) <= {SESSION_END_SECOND * 1_000_000}) AS session_valid,
    {_condition_token_array_aliases_sql(config)}
SELECT
    toFloat32(if(event_count > 0, last_price_primary, 0.0)) AS price_primary_int,
    toFloat32(if(event_count > 0, last_price_secondary, 0.0)) AS price_secondary_int,
    toFloat32(greatest(size_primary_sum, 0.0)) AS size_primary_sum,
    toFloat32(greatest(size_secondary_sum, 0.0)) AS size_secondary_sum,
    toUInt64(event_count) AS event_count,
    toInt64(if(event_count > 0, last_event_timestamp_us, 0)) AS last_event_timestamp_us,
    toUInt8(session_valid AND event_count > 0) AS available,
    {_condition_flag_outer_select_sql()},
    toUInt8(session_valid AND (
        SELECT count()
        FROM {news_table}
        WHERE ticker = {sql_string(ticker)}
          AND timestamp_us > {int(origin_timestamp_us)}
          AND timestamp_us <= {int(end_timestamp_us)}
          AND toDate(toTimeZone(fromUnixTimestamp64Micro(timestamp_us, 'UTC'), {sql_string(SESSION_TIMEZONE)})) = origin_local_date
          AND dateDiff('second', toStartOfDay(toTimeZone(fromUnixTimestamp64Micro(timestamp_us, 'UTC'), {sql_string(SESSION_TIMEZONE)})), toTimeZone(fromUnixTimestamp64Micro(timestamp_us, 'UTC'), {sql_string(SESSION_TIMEZONE)})) < {SESSION_END_SECOND}
    ) > 0) AS ticker_news_arrival_flag,
    toUInt8(session_valid AND (
        SELECT count()
        FROM {sec_table}
        WHERE ticker = {sql_string(ticker)}
          AND timestamp_us > {int(origin_timestamp_us)}
          AND timestamp_us <= {int(end_timestamp_us)}
          AND toDate(toTimeZone(fromUnixTimestamp64Micro(timestamp_us, 'UTC'), {sql_string(SESSION_TIMEZONE)})) = origin_local_date
          AND dateDiff('second', toStartOfDay(toTimeZone(fromUnixTimestamp64Micro(timestamp_us, 'UTC'), {sql_string(SESSION_TIMEZONE)})), toTimeZone(fromUnixTimestamp64Micro(timestamp_us, 'UTC'), {sql_string(SESSION_TIMEZONE)})) < {SESSION_END_SECOND}
    ) > 0) AS sec_filing_arrival_flag
FROM
(
    SELECT
        count() AS event_count,
        sum(toFloat64(size_primary)) AS size_primary_sum,
        sum(toFloat64(size_secondary)) AS size_secondary_sum,
        argMax(price_primary, tuple(sip_timestamp_us, ordinal)) AS last_price_primary,
        argMax(price_secondary, tuple(sip_timestamp_us, ordinal)) AS last_price_secondary,
        max(sip_timestamp_us) AS last_event_timestamp_us,
        {_condition_flag_inner_select_sql()}
    FROM
    (
        SELECT
            ordinal,
            sip_timestamp_us,
            toFloat32(if(price_primary_int > 0, price_primary_int / if(bitAnd(event_meta, 2) = 2, 10000.0, 100.0), 0.0)) AS price_primary,
            toFloat32(if(price_secondary_int > 0, price_secondary_int / if(bitAnd(event_meta, 4) = 4, 10000.0, 100.0), 0.0)) AS price_secondary,
            size_primary,
            size_secondary,
            arrayFilter(t -> t != 0, [condition_token_1, condition_token_2, condition_token_3, condition_token_4, condition_token_5]) AS condition_tokens
        FROM {event_table}
        PREWHERE ticker = {sql_string(ticker)}
          AND sip_timestamp_us > {int(origin_timestamp_us)}
          AND sip_timestamp_us <= {int(end_timestamp_us)}
        WHERE toDate(toTimeZone(fromUnixTimestamp64Micro(sip_timestamp_us, 'UTC'), {sql_string(SESSION_TIMEZONE)})) = origin_local_date
          AND dateDiff('second', toStartOfDay(toTimeZone(fromUnixTimestamp64Micro(sip_timestamp_us, 'UTC'), {sql_string(SESSION_TIMEZONE)})), toTimeZone(fromUnixTimestamp64Micro(sip_timestamp_us, 'UTC'), {sql_string(SESSION_TIMEZONE)})) < {SESSION_END_SECOND}
    )
)
{_settings_sql(config)}
"""


def _condition_token_array_aliases_sql(config: TickerMonthAuditConfig) -> str:
    table = f"{quote_ident(config.database)}.{quote_ident(config.condition_token_reference_table)}"
    aliases: list[str] = []
    for label_key, groups in FUTURE_CONDITION_GROUPS:
        predicates = []
        for source_family, modifiers in groups:
            modifier_sql = ", ".join(str(int(value)) for value in modifiers)
            if source_family in CONDITION_INDICATOR_SOURCE_FAMILIES:
                excluded = ", ".join(sql_string(value) for value in sorted(CONDITION_DIRECT_SOURCE_FAMILIES))
                predicates.append(f"(source_family NOT IN ({excluded}) AND modifier_int IN ({modifier_sql}))")
            else:
                predicates.append(f"(source_family = {sql_string(source_family)} AND modifier_int IN ({modifier_sql}))")
        aliases.append(
            f"""(
        SELECT groupArray(toUInt8(token_id))
        FROM {table}
        WHERE is_join_canonical = 1
          AND ({" OR ".join(predicates)})
    ) AS {quote_ident(label_key + "_tokens")}"""
        )
    return ",\n    ".join(aliases)


def _condition_flag_inner_select_sql() -> str:
    return ",\n        ".join(
        f"max(toUInt8(arrayExists(t -> has({quote_ident(label_key + '_tokens')}, t), condition_tokens))) AS {quote_ident(label_key + '_raw')}"
        for label_key in [name for name, _ in FUTURE_CONDITION_GROUPS]
    )


def _condition_flag_outer_select_sql() -> str:
    return ",\n    ".join(
        f"toUInt8(session_valid AND {quote_ident(label_key + '_raw')} > 0) AS {quote_ident(label_key)}"
        for label_key in [name for name, _ in FUTURE_CONDITION_GROUPS]
    )


def _discover_package_dirs(root: Path, split: str, manifest: Mapping[str, Any], issues: list[AuditIssue]) -> list[Path]:
    split_dir = root / split
    if not split_dir.exists():
        issues.append(AuditIssue("error", "split_missing", f"Missing split directory: {split_dir}"))
        return []
    return sorted(path for path in split_dir.glob("month=*/ticker_hash=*/ticker=*") if path.is_dir())


def _check_root_manifest(manifest: Mapping[str, Any], issues: list[AuditIssue]) -> None:
    if not manifest:
        return
    if manifest.get("format") != TICKER_MONTH_CACHE_FORMAT:
        issues.append(AuditIssue("error", "manifest_format", f"Unexpected manifest format: {manifest.get('format')!r}"))
    if int(manifest.get("version") or 0) != TICKER_MONTH_CACHE_VERSION:
        issues.append(AuditIssue("error", "manifest_version", f"Unexpected manifest version: {manifest.get('version')!r}"))
    if manifest.get("status") not in {"running", "complete", "audit_failed", "interrupted"}:
        issues.append(AuditIssue("warning", "manifest_status", f"Unexpected manifest status: {manifest.get('status')!r}"))


def _read_manifest(path: Path, issues: list[AuditIssue]) -> dict[str, Any]:
    if not path.exists():
        issues.append(AuditIssue("error", "manifest_missing", f"Missing manifest: {path}"))
        return {}
    try:
        return read_json(path)
    except Exception as exc:
        issues.append(AuditIssue("error", "manifest_read_failed", f"Failed to read manifest: {exc!r}", {"path": str(path)}))
        return {}


def _sample_packages(package_dirs: list[Path], per_month: int) -> list[Path]:
    if per_month <= 0:
        return package_dirs
    rng = random.Random(17)
    grouped: dict[str, list[Path]] = {}
    for path in package_dirs:
        month = next((part.split("=", 1)[1] for part in path.parts if part.startswith("month=")), "")
        grouped.setdefault(month, []).append(path)
    sampled: list[Path] = []
    for paths in grouped.values():
        if len(paths) <= per_month:
            sampled.extend(paths)
        else:
            sampled.extend(rng.sample(paths, per_month))
    return sorted(sampled)


def _issue_counts(issues: list[AuditIssue]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for issue in issues:
        counts[issue.severity] = counts.get(issue.severity, 0) + 1
    return counts


def _settings_sql(config: TickerMonthAuditConfig) -> str:
    settings: dict[str, Any] = {}
    if int(config.max_threads) > 0:
        settings["max_threads"] = int(config.max_threads)
    if str(config.max_memory_usage) != "0":
        settings["max_memory_usage"] = parse_size_bytes(str(config.max_memory_usage))
    if not settings:
        return ""
    return "SETTINGS " + ", ".join(f"{key} = {value}" for key, value in settings.items())


if __name__ == "__main__":
    raise SystemExit(main())
