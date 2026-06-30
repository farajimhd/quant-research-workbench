from __future__ import annotations

import argparse
import datetime as dt
import json
import random
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np

if __package__ in {None, ""}:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "research").is_dir():
            sys.path.insert(0, str(parent))
            break

from research.mlops.clickhouse import (
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
    discover_clickhouse_env_files,
    parse_size_bytes,
    quote_ident,
    sql_string,
)
from research.mlops.env import load_env_files
from research.mlops.rolling_loader.indexed_daily_cache import (
    DEFAULT_INDEXED_DAILY_CACHE_ROOT,
    EVENT_PAYLOAD_COLUMNS,
    EVENT_SOURCE_COLUMNS,
    INDEXED_DAILY_CACHE_FORMAT,
    INDEXED_DAILY_CACHE_VERSION,
    day_dir_for,
    jsonable,
    read_json,
    session_window,
    timestamp_us_to_utc,
    utc_date_from_us,
    write_json_atomic,
)


@dataclass(frozen=True, slots=True)
class IndexedDailyCacheAuditConfig:
    cache_root: Path
    split: str = "train"
    sample_days: int = 3
    samples_per_day: int = 3
    seed: int = 17
    source_checks: bool = True
    fail_on_warning: bool = False
    output_path: Path | None = None
    sample_output_path: Path | None = None
    clickhouse_url: str = ""
    clickhouse_user: str = ""
    clickhouse_password: str = ""
    database: str = ""
    events_table: str = ""
    max_threads: int = 4
    max_memory_usage: str = "8G"
    min_source_coverage_ratio: float = 0.90
    max_source_overage_ratio: float = 1.01


@dataclass(slots=True)
class AuditIssue:
    severity: str
    code: str
    message: str
    day: str = ""
    sample_index: int | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AuditResult:
    ok: bool
    status: str
    cache_root: str
    split: str
    started_at: str
    completed_at: str
    elapsed_seconds: float
    summary: dict[str, Any]
    issues: list[AuditIssue]
    report_path: str
    sample_report_path: str

    def to_json(self) -> dict[str, Any]:
        return {
            "ok": bool(self.ok),
            "status": self.status,
            "cache_root": self.cache_root,
            "split": self.split,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "elapsed_seconds": self.elapsed_seconds,
            "summary": self.summary,
            "issues": [asdict(issue) for issue in self.issues],
            "report_path": self.report_path,
            "sample_report_path": self.sample_report_path,
        }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit rolling indexed daily cache packages.")
    parser.add_argument("--cache-root", type=Path, default=DEFAULT_INDEXED_DAILY_CACHE_ROOT)
    parser.add_argument("--cache-id", default="")
    parser.add_argument("--cache-path", type=Path, default=None)
    parser.add_argument("--split", default="train")
    parser.add_argument("--sample-days", type=int, default=3)
    parser.add_argument("--samples-per-day", type=int, default=3)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--source-checks", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--fail-on-warning", action="store_true")
    parser.add_argument("--output-path", type=Path, default=None)
    parser.add_argument("--sample-output-path", type=Path, default=None)
    parser.add_argument("--clickhouse-url", default="")
    parser.add_argument("--user", default="")
    parser.add_argument("--password", default="")
    parser.add_argument("--env-file", type=Path, default=None)
    parser.add_argument("--database", default="")
    parser.add_argument("--events-table", default="")
    parser.add_argument("--max-threads", type=int, default=4)
    parser.add_argument("--max-memory-usage", default="8G")
    parser.add_argument("--min-source-coverage-ratio", type=float, default=0.90)
    parser.add_argument("--max-source-overage-ratio", type=float, default=1.01)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    env_files = discover_clickhouse_env_files()
    if args.env_file is not None:
        env_files.append(args.env_file)
    loaded = load_env_files(env_files)
    if loaded:
        print("Loaded .env files: " + ", ".join(str(path) for path in loaded), flush=True)
    config = IndexedDailyCacheAuditConfig(
        cache_root=_resolve_cache_root(args),
        split=args.split,
        sample_days=max(0, int(args.sample_days)),
        samples_per_day=max(0, int(args.samples_per_day)),
        seed=int(args.seed),
        source_checks=bool(args.source_checks),
        fail_on_warning=bool(args.fail_on_warning),
        output_path=args.output_path,
        sample_output_path=args.sample_output_path,
        clickhouse_url=args.clickhouse_url or default_clickhouse_url(),
        clickhouse_user=args.user or default_clickhouse_user(),
        clickhouse_password=args.password or default_clickhouse_password(),
        database=args.database,
        events_table=args.events_table,
        max_threads=max(1, int(args.max_threads)),
        max_memory_usage=str(args.max_memory_usage),
        min_source_coverage_ratio=max(0.0, float(args.min_source_coverage_ratio)),
        max_source_overage_ratio=max(1.0, float(args.max_source_overage_ratio)),
    )
    result = run_audit(config)
    print(json.dumps(_compact_summary(result), indent=2, sort_keys=True), flush=True)
    return 0 if result.ok else 1


def run_audit(config: IndexedDailyCacheAuditConfig) -> AuditResult:
    started = time.perf_counter()
    started_at = dt.datetime.now(tz=dt.timezone.utc).isoformat()
    cache_root = Path(config.cache_root)
    issues: list[AuditIssue] = []
    samples: list[dict[str, Any]] = []
    manifest = _read_manifest(cache_root, issues)
    source = dict(manifest.get("source") or {})
    database = str(config.database or source.get("database") or "market_sip_compact")
    events_table = str(config.events_table or source.get("events_table") or "events")
    days = _discover_days(cache_root, config.split, manifest, issues)
    summary: dict[str, Any] = {
        "manifest_status": manifest.get("status", ""),
        "manifest_format": manifest.get("format", ""),
        "manifest_version": manifest.get("version", 0),
        "days": len(days),
        "counts": {
            "events": 0,
            "origins": 0,
            "source_session_events": 0,
        },
    }
    _check_manifest(manifest, issues)
    rng = random.Random(int(config.seed))
    sampled_days = _sample_days(days, config.sample_days, rng)
    source_client = (
        ClickHouseHttpClient(config.clickhouse_url, config.clickhouse_user, config.clickhouse_password)
        if config.source_checks
        else None
    )
    for day_dir in days:
        day_summary = _audit_day(
            day_dir=day_dir,
            sampled=day_dir in sampled_days,
            samples_per_day=int(config.samples_per_day),
            rng=rng,
            issues=issues,
            samples=samples,
            source_client=source_client,
            database=database,
            events_table=events_table,
            max_threads=int(config.max_threads),
            max_memory_usage=str(config.max_memory_usage),
            coverage_min=float(config.min_source_coverage_ratio),
            coverage_max=float(config.max_source_overage_ratio),
        )
        for key in ("events", "origins", "source_session_events"):
            summary["counts"][key] += int(day_summary.get(key, 0))
    if config.fail_on_warning and any(issue.severity == "warning" for issue in issues):
        issues.append(AuditIssue("error", "warning_escalated", "--fail-on-warning converted warnings to audit failure."))
    issue_counts = _issue_counts(issues)
    summary["issue_counts"] = issue_counts
    ok = issue_counts.get("error", 0) == 0
    status = "ok" if ok else "failed"
    completed_at = dt.datetime.now(tz=dt.timezone.utc).isoformat()
    report_path = config.output_path or (cache_root / "indexed_audit_report.json")
    sample_path = config.sample_output_path or (cache_root / "indexed_audit_samples.jsonl")
    result = AuditResult(
        ok=ok,
        status=status,
        cache_root=str(cache_root),
        split=str(config.split),
        started_at=started_at,
        completed_at=completed_at,
        elapsed_seconds=time.perf_counter() - started,
        summary=summary,
        issues=issues,
        report_path=str(report_path),
        sample_report_path=str(sample_path),
    )
    write_json_atomic(report_path, result.to_json())
    if samples:
        sample_path.parent.mkdir(parents=True, exist_ok=True)
        with sample_path.open("w", encoding="utf-8") as handle:
            for row in samples:
                handle.write(json.dumps(jsonable(row), sort_keys=True) + "\n")
    return result


def _audit_day(
    *,
    day_dir: Path,
    sampled: bool,
    samples_per_day: int,
    rng: random.Random,
    issues: list[AuditIssue],
    samples: list[dict[str, Any]],
    source_client: ClickHouseHttpClient | None,
    database: str,
    events_table: str,
    max_threads: int,
    max_memory_usage: str,
    coverage_min: float,
    coverage_max: float,
) -> dict[str, int]:
    pl = _polars()
    day = day_dir.name.removeprefix("day=")
    manifest_path = day_dir / "day_manifest.json"
    if not manifest_path.exists():
        issues.append(AuditIssue("error", "day_manifest_missing", "Missing day_manifest.json.", day=day))
        return {}
    manifest = read_json(manifest_path)
    session = manifest.get("session") or {}
    _check_day_files(day_dir, day, issues)
    events = _read_parquet(day_dir / "events.parquet", pl, issues, day)
    origins = _read_parquet(day_dir / "origins.parquet", pl, issues, day)
    windows = _read_parquet(day_dir / "event_window_index.parquet", pl, issues, day)
    if events is None or origins is None or windows is None:
        return {}
    event_count = int(events.height)
    origin_count = int(origins.height)
    session_start = int(session.get("start_timestamp_us") or 0)
    session_end = int(session.get("end_timestamp_us") or 0)
    if origin_count:
        min_origin = int(origins.select(pl.col("origin_timestamp_us").min()).item())
        max_origin = int(origins.select(pl.col("origin_timestamp_us").max()).item())
        if min_origin < session_start:
            issues.append(AuditIssue("error", "origin_before_session", "Origin timestamp before session start.", day=day, details={"min_origin": min_origin, "session_start": session_start}))
        if max_origin >= session_end:
            issues.append(AuditIssue("error", "origin_after_session", "Origin timestamp at/after session end.", day=day, details={"max_origin": max_origin, "session_end": session_end}))
    if origins.select(["ticker_id", "origin_ordinal"]).unique().height != origin_count:
        issues.append(AuditIssue("error", "duplicate_origin_identity", "Duplicate (ticker_id, origin_ordinal) rows found.", day=day))
    if windows.height != origin_count:
        issues.append(AuditIssue("error", "window_origin_count", "event_window_index row count does not match origins.", day=day, details={"windows": windows.height, "origins": origin_count}))
    _check_event_schema(events, day, issues)
    _check_window_samples(events, origins, windows, manifest, day, issues, rng, sample_count=max(samples_per_day, 1024 if sampled else 128))
    source_count = _source_event_count(
        source_client=source_client,
        database=database,
        events_table=events_table,
        start_us=session_start,
        end_us=session_end,
        max_threads=max_threads,
        max_memory_usage=max_memory_usage,
    ) if source_client is not None else int((manifest.get("event_dependency") or {}).get("source_session_event_count") or 0)
    if source_count > 0:
        ratio = float(origin_count) / float(source_count)
        if ratio < coverage_min:
            issues.append(AuditIssue("warning", "source_coverage_low", "Origin count is far below source session event count.", day=day, details={"origins": origin_count, "source_events": source_count, "ratio": ratio}))
        if ratio > coverage_max:
            issues.append(AuditIssue("error", "source_coverage_overage", "Origin count exceeds source session event count.", day=day, details={"origins": origin_count, "source_events": source_count, "ratio": ratio}))
    if sampled and source_client is not None and origin_count:
        _check_source_samples(
            source_client=source_client,
            database=database,
            events_table=events_table,
            events=events,
            origins=origins,
            day=day,
            samples=samples,
            issues=issues,
            rng=rng,
            samples_per_day=samples_per_day,
            max_threads=max_threads,
            max_memory_usage=max_memory_usage,
        )
    return {"events": event_count, "origins": origin_count, "source_session_events": source_count}


def _check_window_samples(events: Any, origins: Any, windows: Any, manifest: Mapping[str, Any], day: str, issues: list[AuditIssue], rng: random.Random, *, sample_count: int) -> None:
    if origins.height == 0 or windows.height == 0:
        return
    pl = _polars()
    context_lags = tuple(int(value) for value in ((manifest.get("config") or {}).get("context_lags") or ()))
    events_per_chunk = int((manifest.get("config") or {}).get("events_per_chunk") or 0)
    if not context_lags or events_per_chunk <= 0:
        issues.append(AuditIssue("error", "context_geometry_missing", "Missing context_lags/events_per_chunk in day manifest.", day=day))
        return
    ordinals = events.get_column("ordinal").to_numpy().astype(np.int64, copy=False)
    row_count = int(origins.height)
    take = min(row_count, max(0, int(sample_count)))
    sample_indices = sorted(rng.sample(range(row_count), take)) if take < row_count else list(range(row_count))
    for sample_index in sample_indices:
        origin = origins.row(sample_index, named=True)
        row = windows.row(sample_index, named=True)
        if int(row.get("origin_id")) != int(origin.get("origin_id")):
            issues.append(AuditIssue("error", "window_origin_id_mismatch", "Window origin_id does not match origins row.", day=day, sample_index=sample_index))
            continue
        for context_index, _lag in enumerate(context_lags):
            column = f"window_start_{context_index:03d}"
            if column not in row:
                issues.append(AuditIssue("error", "window_start_missing", f"Missing {column}.", day=day, sample_index=sample_index))
                continue
            start = int(row[column])
            end = start + events_per_chunk - 1
            if start < 0 or end >= ordinals.shape[0]:
                issues.append(AuditIssue("error", "window_bounds", "Window offsets outside events array.", day=day, sample_index=sample_index, details={"start": start, "end": end, "events": int(ordinals.shape[0])}))
                continue
            values = ordinals[start : end + 1]
            if values.shape[0] != events_per_chunk or not bool(np.all(values[1:] == values[:-1] + 1)):
                issues.append(AuditIssue("error", "window_ordinal_gap", "Window ordinals are not contiguous.", day=day, sample_index=sample_index, details={"context_index": context_index, "first": int(values[0]) if values.size else -1, "last": int(values[-1]) if values.size else -1}))


def _check_source_samples(
    *,
    source_client: ClickHouseHttpClient,
    database: str,
    events_table: str,
    events: Any,
    origins: Any,
    day: str,
    samples: list[dict[str, Any]],
    issues: list[AuditIssue],
    rng: random.Random,
    samples_per_day: int,
    max_threads: int,
    max_memory_usage: str,
) -> None:
    take = min(int(samples_per_day), int(origins.height))
    if take <= 0:
        return
    sample_indices = sorted(rng.sample(range(int(origins.height)), take)) if take < origins.height else list(range(int(origins.height)))
    for sample_index in sample_indices:
        origin = origins.row(sample_index, named=True)
        row_offset = int(origin["event_row_offset"])
        saved = events.row(row_offset, named=True)
        source = _fetch_source_event(
            source_client=source_client,
            database=database,
            events_table=events_table,
            ticker=str(origin["ticker"]),
            ordinal=int(origin["origin_ordinal"]),
            max_threads=max_threads,
            max_memory_usage=max_memory_usage,
        )
        sample_record = {
            "day": day,
            "sample_index": sample_index,
            "ticker": str(origin["ticker"]),
            "origin_ordinal": int(origin["origin_ordinal"]),
            "origin_timestamp_us": int(origin["origin_timestamp_us"]),
        }
        samples.append(sample_record)
        if source is None:
            issues.append(AuditIssue("error", "source_event_missing", "Source event was not found.", day=day, sample_index=sample_index, details=sample_record))
            continue
        for saved_name, source_name in _source_compare_columns().items():
            if saved.get(saved_name) != source.get(source_name):
                issues.append(
                    AuditIssue(
                        "error",
                        "source_event_mismatch",
                        "Saved event row does not match ClickHouse source.",
                        day=day,
                        sample_index=sample_index,
                        details={"column": saved_name, "saved": saved.get(saved_name), "source": source.get(source_name), **sample_record},
                    )
                )
                break


def _source_event_count(
    *,
    source_client: ClickHouseHttpClient | None,
    database: str,
    events_table: str,
    start_us: int,
    end_us: int,
    max_threads: int,
    max_memory_usage: str,
) -> int:
    if source_client is None:
        return 0
    table = f"{quote_ident(database)}.{quote_ident(events_table)}"
    start_date = utc_date_from_us(start_us).isoformat()
    end_date = (utc_date_from_us(end_us) + dt.timedelta(days=1)).isoformat()
    query = f"""
SELECT count() AS c
FROM {table}
PREWHERE event_date >= toDate({sql_string(start_date)})
  AND event_date < toDate({sql_string(end_date)})
WHERE sip_timestamp_us >= {int(start_us)}
  AND sip_timestamp_us < {int(end_us)}
SETTINGS max_threads = {int(max_threads)}, max_memory_usage = {parse_size_bytes(str(max_memory_usage))}
FORMAT JSONEachRow
"""
    for line in source_client.execute(query).splitlines():
        if line.strip():
            return int(json.loads(line).get("c") or 0)
    return 0


def _fetch_source_event(
    *,
    source_client: ClickHouseHttpClient,
    database: str,
    events_table: str,
    ticker: str,
    ordinal: int,
    max_threads: int,
    max_memory_usage: str,
) -> dict[str, Any] | None:
    table = f"{quote_ident(database)}.{quote_ident(events_table)}"
    columns = ",\n    ".join(quote_ident(column) for column in ("ticker", *EVENT_SOURCE_COLUMNS))
    query = f"""
SELECT
    {columns}
FROM {table}
WHERE ticker = {sql_string(ticker)}
  AND ordinal = {int(ordinal)}
LIMIT 1
SETTINGS max_threads = {int(max_threads)}, max_memory_usage = {parse_size_bytes(str(max_memory_usage))}
FORMAT JSONEachRow
"""
    for line in source_client.execute(query).splitlines():
        if line.strip():
            return json.loads(line)
    return None


def _source_compare_columns() -> dict[str, str]:
    return {
        "ordinal": "ordinal",
        "event_meta": "event_meta",
        "timestamp_us": "sip_timestamp_us",
        "price_primary_int": "price_primary_int",
        "price_secondary_int": "price_secondary_int",
        "size_primary": "size_primary",
        "size_secondary": "size_secondary",
        "exchange_primary": "exchange_primary",
        "exchange_secondary": "exchange_secondary",
        "condition_token_1": "condition_token_1",
        "condition_token_2": "condition_token_2",
        "condition_token_3": "condition_token_3",
        "condition_token_4": "condition_token_4",
        "condition_token_5": "condition_token_5",
    }


def _check_manifest(manifest: Mapping[str, Any], issues: list[AuditIssue]) -> None:
    if manifest.get("format") != INDEXED_DAILY_CACHE_FORMAT:
        issues.append(AuditIssue("error", "manifest_format", f"Unexpected manifest format: {manifest.get('format')!r}"))
    if int(manifest.get("version") or 0) != INDEXED_DAILY_CACHE_VERSION:
        issues.append(AuditIssue("error", "manifest_version", f"Unexpected manifest version: {manifest.get('version')!r}"))
    if manifest.get("status") not in {"complete", "audit_failed"}:
        issues.append(AuditIssue("error", "manifest_status", f"Manifest status is not complete: {manifest.get('status')!r}"))


def _check_day_files(day_dir: Path, day: str, issues: list[AuditIssue]) -> None:
    required = (
        "events.parquet",
        "event_ranges.parquet",
        "origins.parquet",
        "event_window_index.parquet",
        "macro_bars.parquet",
        "macro_ranges.parquet",
        "intraday_label_index.parquet",
        "day_manifest.json",
    )
    for name in required:
        if not (day_dir / name).exists():
            issues.append(AuditIssue("error", "day_file_missing", f"Missing required day file: {name}", day=day))


def _check_event_schema(events: Any, day: str, issues: list[AuditIssue]) -> None:
    missing = [column for column in ("row_offset", "ticker", "ticker_id", *EVENT_PAYLOAD_COLUMNS) if column not in events.columns]
    if missing:
        issues.append(AuditIssue("error", "event_schema_missing", "events.parquet is missing required columns.", day=day, details={"missing": missing}))
    if events.height and "row_offset" in events.columns:
        offsets = events.get_column("row_offset").to_numpy()
        if offsets.shape[0] and not bool(np.all(offsets == np.arange(offsets.shape[0]))):
            issues.append(AuditIssue("error", "event_row_offset_sequence", "event row_offset is not contiguous from zero.", day=day))


def _read_manifest(cache_root: Path, issues: list[AuditIssue]) -> dict[str, Any]:
    path = cache_root / "manifest.json"
    if not path.exists():
        issues.append(AuditIssue("error", "manifest_missing", f"Missing manifest.json: {path}"))
        return {}
    return read_json(path)


def _discover_days(cache_root: Path, split: str, manifest: Mapping[str, Any], issues: list[AuditIssue]) -> list[Path]:
    split_dir = cache_root / split
    if not split_dir.exists():
        issues.append(AuditIssue("error", "split_dir_missing", f"Missing split directory: {split_dir}"))
        return []
    days = sorted(path for path in split_dir.glob("day=*") if path.is_dir())
    manifest_days = manifest.get("days") if isinstance(manifest.get("days"), list) else []
    if manifest_days and len(manifest_days) != len(days):
        issues.append(AuditIssue("warning", "manifest_day_count", "Manifest day count does not match day directories.", details={"manifest": len(manifest_days), "dirs": len(days)}))
    tmp = list(split_dir.glob("day=*.tmp"))
    if tmp:
        issues.append(AuditIssue("error", "tmp_day_dirs_present", "Unfinished temporary day directories are present.", details={"count": len(tmp)}))
    return days


def _read_parquet(path: Path, pl: Any, issues: list[AuditIssue], day: str) -> Any | None:
    try:
        return pl.read_parquet(path)
    except Exception as exc:
        issues.append(AuditIssue("error", "parquet_read_failed", f"Could not read {path.name}: {exc!r}", day=day))
        return None


def _sample_days(days: list[Path], count: int, rng: random.Random) -> set[Path]:
    if count <= 0 or count >= len(days):
        return set(days)
    return set(rng.sample(days, count))


def _issue_counts(issues: Iterable[AuditIssue]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for issue in issues:
        counts[issue.severity] = counts.get(issue.severity, 0) + 1
    return counts


def _compact_summary(result: AuditResult) -> dict[str, Any]:
    return {
        "ok": result.ok,
        "status": result.status,
        "cache_root": result.cache_root,
        "split": result.split,
        "elapsed_seconds": result.elapsed_seconds,
        "summary": result.summary,
        "report_path": result.report_path,
        "sample_report_path": result.sample_report_path,
        "issues_preview": [asdict(issue) for issue in result.issues[:10]],
    }


def _resolve_cache_root(args: argparse.Namespace) -> Path:
    if args.cache_path is not None:
        return Path(args.cache_path)
    root = Path(args.cache_root)
    return root / str(args.cache_id) if args.cache_id else root


def _polars() -> Any:
    try:
        import polars as pl  # type: ignore
    except ModuleNotFoundError as exc:
        raise RuntimeError("Install polars to audit indexed rolling caches.") from exc
    return pl


if __name__ == "__main__":
    raise SystemExit(main())

