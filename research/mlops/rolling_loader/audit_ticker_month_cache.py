from __future__ import annotations

import argparse
import datetime as dt
import json
import random
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

if __package__ in {None, ""}:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "research").is_dir():
            sys.path.insert(0, str(parent))
            break

from research.mlops.clickhouse import default_clickhouse_password, default_clickhouse_url, default_clickhouse_user, discover_clickhouse_env_files, parse_size_bytes, quote_ident, sql_string
from research.mlops.env import load_env_files
from research.mlops.rolling_loader.run_build_ticker_month_cache import query_polars
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
    events_table: str = "events"
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
    parser.add_argument("--events-table", default="events")
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
            events_table=args.events_table,
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
    totals = {"packages": len(package_dirs), "sampled_packages": len(sampled), "events": 0, "origins": 0, "labels": 0}
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
    context_required = ("ticker_news_tokens", "sec_filing_tokens", "xbrl", "daily_bars")
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
        _check_labels(origins, labels, issues, package_dir)
        if config.source_checks and origins.height:
            _source_check_origin(origins, manifest, config, client_opts, issues, package_dir)


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


def _check_labels(origins: Any, labels: Any, issues: list[AuditIssue], package_dir: Path) -> None:
    if not origins.height:
        return
    if not labels.height:
        issues.append(AuditIssue("warning", "labels_empty", "No intraday forward labels for package with origins.", {"package": str(package_dir)}))
        return
    origin_count = int(origins.height)
    labeled_origins = int(labels.select("origin_key").unique().height) if "origin_key" in labels.columns else 0
    if labeled_origins < max(1, origin_count // 2):
        issues.append(AuditIssue("warning", "label_origin_coverage_low", "Intraday label origin coverage is low.", {"package": str(package_dir), "origins": origin_count, "labeled_origins": labeled_origins}))


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
