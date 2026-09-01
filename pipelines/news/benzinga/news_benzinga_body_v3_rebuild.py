from __future__ import annotations

import argparse
import concurrent.futures
import gzip
import json
import os
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from pipelines.news.benzinga.core.clickhouse_values import datetime64_utc_text
from pipelines.news.benzinga.core.clickhouse_writer import insert_json_each_row, table_exists
from pipelines.news.benzinga.core.clickhouse_writer_body_v3 import (
    BLOCK_COLUMNS, EVENT_COLUMNS, LINEAGE_COLUMNS, RENDERED_COLUMNS, SOURCE_COLUMNS, TICKER_COLUMNS,
    NewsBodyV3TargetConfig, create_body_v3_tables,
)
from pipelines.news.benzinga.core.clickhouse_writer_v2 import json_each_row_batches, v2_batch_query_id
from pipelines.news.benzinga.core.clickhouse_writer_v2 import EVENT_COLUMNS as V2_EVENT_COLUMNS
from pipelines.news.benzinga.news_benzinga_body_v3 import (
    BODY_CLEANER_VERSION, BODY_RENDERER_VERSION, BODY_TEXT_CONTRACT, body_purity_reasons, build_body_v3_rows,
    contract_manifest, render_canonical_body,
)
from pipelines.news.benzinga.core.clickhouse_writer_body_v4 import body_v4_target_config
from pipelines.news.benzinga import news_benzinga_body_v4
from pipelines.news.benzinga.news_benzinga_render_v2 import NEWS_RENDERER_VERSION
from pipelines.news.benzinga.news_benzinga_rendered_v2_rebuild import (
    DEFAULT_PATH_MAP, RetryingClickHouseHttpClient, append_jsonl, parse_path_maps,
    resolve_path, source_scope,
)
from pipelines.news.benzinga.news_benzinga_url_policy import (
    default_clickhouse_password, default_clickhouse_url, default_clickhouse_user,
)
from research.mlops.clickhouse import quote_ident, sql_string
from research.mlops.env import discover_env_files, load_env_files


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT_ROOT = Path("D:/TradingML/runtimes/news/benzinga_news_body_v3")
DEFAULT_V4_OUTPUT_ROOT = Path("D:/TradingML/runtimes/news/benzinga_news_body_v4")


@dataclass(slots=True)
class BodyBuildCounts:
    source_rows: int = 0
    rendered_rows: int = 0
    source_parts: int = 0
    block_rows: int = 0
    ticker_rows: int = 0
    missing_body_rows: int = 0
    partial_body_rows: int = 0
    purity_error_rows: int = 0
    failures: int = 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build, certify, promote, or roll back a versioned Benzinga body-only authority.")
    parser.add_argument(
        "action", choices=("rebuild", "repair-purity", "certify", "promote", "rollback"),
        nargs="?", default="rebuild",
    )
    parser.add_argument("--execute", action="store_true", help="Allow ClickHouse writes; rebuild otherwise validates only.")
    parser.add_argument("--database", default=os.environ.get("NEWS_BENZINGA_CLICKHOUSE_DATABASE", "q_live"))
    parser.add_argument("--source-table", default="benzinga_news_event_v2")
    parser.add_argument("--previous-rendered-table", default="")
    parser.add_argument("--previous-source-table", default="benzinga_news_source_v2")
    parser.add_argument("--start-date", default="")
    parser.add_argument("--end-date-exclusive", default="")
    parser.add_argument("--limit-days", type=int, default=0)
    parser.add_argument("--force", action="store_true", help="Rebuild selected days even when current row counts are complete.")
    parser.add_argument("--workers", type=int, default=max(4, min(32, os.cpu_count() or 16)))
    parser.add_argument("--window-days", type=int, default=14, help="Bounded restart unit; rows retain daily partitions.")
    parser.add_argument("--insert-batch-size", type=int, default=500)
    parser.add_argument("--body-version", choices=("v3", "v4"), default="v3")
    parser.add_argument("--output-root", default="")
    parser.add_argument("--path-prefix-map", action="append", default=[])
    parser.add_argument("--clickhouse-attempts", type=int, default=12)
    parser.add_argument("--clickhouse-timeout-seconds", type=float, default=180.0)
    return parser.parse_args(argv)


def configure_body_version(version: str) -> None:
    """Bind this single-process runner to one immutable body contract."""
    global BODY_CLEANER_VERSION, BODY_RENDERER_VERSION, BODY_TEXT_CONTRACT
    global body_purity_reasons, build_body_v3_rows, contract_manifest, render_canonical_body
    if version == "v3":
        return
    BODY_CLEANER_VERSION = news_benzinga_body_v4.BODY_CLEANER_VERSION
    BODY_RENDERER_VERSION = news_benzinga_body_v4.BODY_RENDERER_VERSION
    BODY_TEXT_CONTRACT = news_benzinga_body_v4.BODY_TEXT_CONTRACT
    body_purity_reasons = news_benzinga_body_v4.body_purity_reasons
    build_body_v3_rows = news_benzinga_body_v4.build_body_v4_rows
    contract_manifest = news_benzinga_body_v4.contract_manifest
    render_canonical_body = news_benzinga_body_v4.render_canonical_body


def main(argv: list[str] | None = None) -> int:
    load_env_files(discover_env_files(REPO_ROOT), verbose=False)
    args = parse_args(argv)
    configure_body_version(args.body_version)
    if not args.previous_rendered_table:
        args.previous_rendered_table = (
            "benzinga_news_rendered_v3" if args.body_version == "v4" else "benzinga_news_rendered_v2"
        )
    if not args.output_root:
        args.output_root = str(DEFAULT_V4_OUTPUT_ROOT if args.body_version == "v4" else DEFAULT_OUTPUT_ROOT)
    run_id = datetime.now(UTC).strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
    run_root = Path(args.output_root) / run_id
    run_root.mkdir(parents=True, exist_ok=True)
    status_path = run_root / "status.jsonl"
    client = RetryingClickHouseHttpClient(
        default_clickhouse_url(), default_clickhouse_user(), default_clickhouse_password(),
        attempts=args.clickhouse_attempts, retry_base_seconds=2, retry_max_seconds=30,
        request_timeout_seconds=args.clickhouse_timeout_seconds, status_path=status_path,
    )
    target = (
        body_v4_target_config(database=args.database, execute=args.execute)
        if args.body_version == "v4"
        else NewsBodyV3TargetConfig(database=args.database, execute=args.execute)
    )
    try:
        if args.action in {"repair-purity", "certify", "promote", "rollback"}:
            if not args.execute:
                raise SystemExit(f"{args.action} requires --execute")
            if args.action == "repair-purity":
                return repair_purity_rows(client, target, args, run_id, run_root)
            if args.action == "certify":
                return certify_existing(client, target, args.source_table, run_id, run_root)
            return run_control_action(client, target, args.action, run_id, run_root)
        return rebuild(client, target, args, run_id, run_root, status_path)
    finally:
        client.close()


def rebuild(
    client: RetryingClickHouseHttpClient,
    target: NewsBodyV3TargetConfig,
    args: argparse.Namespace,
    run_id: str,
    run_root: Path,
    status_path: Path,
) -> int:
    source_min, source_max, expected_total = source_scope(client, args.database, args.source_table)
    start = date.fromisoformat(args.start_date) if args.start_date else source_min
    end = date.fromisoformat(args.end_date_exclusive) if args.end_date_exclusive else source_max + timedelta(days=1)
    days = list(iter_days(start, end))
    if args.limit_days:
        days = days[: args.limit_days]
    full_scope = not args.limit_days and start == source_min and end == source_max + timedelta(days=1)
    windows = [days[index:index + max(1, args.window_days)] for index in range(0, len(days), max(1, args.window_days))]
    expected_by_day = load_source_counts_by_day(client, args.database, args.source_table, start, end)
    completed_days = load_complete_days(client, target, expected_by_day) if args.execute and not args.force else set()
    completed_days.update(day for day in days if expected_by_day.get(day, 0) == 0)
    path_maps = parse_path_maps(args.path_prefix_map) or [DEFAULT_PATH_MAP]
    started = datetime64_utc_text()
    counts = BodyBuildCounts()
    audit_samples: dict[str, list[dict[str, Any]]] = {}
    before_labels = operator_label_snapshot(client, args.database)
    print(
        f"NEWS BODY {BODY_RENDERER_VERSION.rsplit('_', 1)[-1].upper()} | contract={BODY_TEXT_CONTRACT} "
        f"days={len(days):,} windows={len(windows):,} "
        f"source_rows={expected_total:,} execute={args.execute} workers={args.workers}", flush=True,
    )
    if args.execute:
        create_body_v3_tables(client, target)
        write_authority(client, target, run_id, "building", counts, "", started)
    for window_index, window in enumerate(windows, start=1):
        window_start = window[0]
        window_end = window[-1] + timedelta(days=1)
        expected_window = sum(expected_by_day.get(day, 0) for day in window)
        if args.execute and all(day in completed_days for day in window):
            counts.source_rows += expected_window
            counts.rendered_rows += expected_window
            append_jsonl(status_path, {
                "start_date": window_start.isoformat(), "end_date_exclusive": window_end.isoformat(),
                "status": "skipped_complete", "rows": expected_window,
            })
            continue
        source_rows = load_body_source_window(client, args.database, args.source_table, window_start, window_end)
        previous = load_previous_hashes(client, args.database, args.previous_rendered_table, window_start, window_end)
        source_parts = load_previous_sources(
            client, args.database, args.source_table, args.previous_source_table, window_start, window_end,
        )
        built_rows: list[dict[str, Any]] = []
        purity_violations: list[dict[str, Any]] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
            futures = [
                executor.submit(
                    render_body_one,
                    row,
                    previous,
                    source_parts.get(str(row.get("canonical_news_id") or ""), []),
                    path_maps,
                )
                for row in source_rows
            ]
            for source_row, future in zip(source_rows, futures):
                try:
                    built = future.result()
                    built_rows.append(built)
                    collect_audit_samples(audit_samples, built)
                    status = built["rendered"]["body_status"]
                    counts.missing_body_rows += status == "missing"
                    counts.partial_body_rows += status == "partial"
                    purity_reasons = body_purity_reasons(built["rendered"]["canonical_body_text"])
                    counts.purity_error_rows += bool(purity_reasons)
                    if purity_reasons:
                        purity_violations.append({
                            "canonical_news_id": built["rendered"]["canonical_news_id"],
                            "reasons": list(purity_reasons),
                        })
                except Exception as exc:  # noqa: BLE001
                    counts.failures += 1
                    append_jsonl(run_root / "errors.jsonl", {
                        "start_date": window_start.isoformat(), "canonical_news_id": source_row.get("canonical_news_id", ""),
                        "error_type": type(exc).__name__, "error": str(exc)[:400],
                    })
        if len(built_rows) != len(source_rows):
            raise RuntimeError(f"{window_start}: built {len(built_rows):,}/{len(source_rows):,}; see {run_root / 'errors.jsonl'}")
        if purity_violations:
            for violation in purity_violations[:100]:
                append_jsonl(run_root / "purity_errors.jsonl", {
                    "start_date": window_start.isoformat(), **violation,
                })
            raise RuntimeError(
                f"{window_start}: {len(purity_violations):,} canonical bodies failed purity; "
                f"see {run_root / 'purity_errors.jsonl'}"
            )
        # Large JSONEachRow reads can be followed by a server-closed keep-alive
        # socket. Start the product inserts on a fresh acknowledged connection
        # instead of paying one avoidable lost-socket retry per window.
        client.close()
        insert_body_rows(client, target, built_rows, execute=args.execute, max_rows=args.insert_batch_size)
        counts.source_rows += len(source_rows)
        counts.rendered_rows += len(built_rows)
        counts.source_parts += sum(len(row["sources"]) for row in built_rows)
        counts.block_rows += sum(len(row["blocks"]) for row in built_rows)
        counts.ticker_rows += sum(len(row["tickers"]) for row in built_rows)
        append_jsonl(status_path, {
            "start_date": window_start.isoformat(), "end_date_exclusive": window_end.isoformat(),
            "status": "completed" if args.execute else "dry_run", "rows": len(source_rows),
        })
        print(
            f"[{window_index:,}/{len(windows):,}] {window_start}..{window_end} rows={len(source_rows):,} "
            f"missing={counts.missing_body_rows:,} "
            f"partial={counts.partial_body_rows:,} purity={counts.purity_error_rows:,}", flush=True,
        )
    if args.execute:
        counts = load_all_body_counts(client, target)
    after_labels = operator_label_snapshot(client, args.database)
    if args.execute and full_scope:
        audit = certify_body_authority(client, target, args.source_table, full_scope=True)
    elif args.execute:
        audit = {
            "status": "partial_validated",
            "errors": [],
            "metrics": asdict(counts),
            "note": "Bounded rebuild passed per-window cardinality and purity gates; whole-corpus relations require certify.",
        }
    else:
        audit = {"status": "dry_run", "errors": [], "metrics": asdict(counts)}
    audit.update({
        "contract": contract_manifest(), "run_id": run_id, "full_scope": full_scope,
        "operator_labels_before": before_labels, "operator_labels_after": after_labels,
        "operator_label_note": "Read-only observation only; this pipeline never writes operator label or note tables.",
    })
    report_path = run_root / "certification.json"
    report_path.write_text(json.dumps(audit, indent=2, sort_keys=True), encoding="utf-8")
    samples_path = run_root / "audit_samples.jsonl"
    for category, samples in sorted(audit_samples.items()):
        for sample in samples:
            append_jsonl(samples_path, {"category": category, **sample})
    if args.execute:
        status = "certified" if full_scope and not audit["errors"] else "audit_failed" if audit["errors"] else "partial_validated"
        write_authority(client, target, run_id, status, counts, str(report_path), started,
                        relational_errors=len(audit["errors"]))
        if status == "audit_failed":
            raise RuntimeError(f"{BODY_RENDERER_VERSION} was not certified: {audit['errors']}; report={report_path}")
    print(f"COMPLETED | status={audit['status']} report={report_path}", flush=True)
    return 0


def repair_purity_rows(
    client: RetryingClickHouseHttpClient,
    target: NewsBodyV3TargetConfig,
    args: argparse.Namespace,
    run_id: str,
    run_root: Path,
) -> int:
    """Re-render only current rows rejected by the independent SQL purity gate."""
    if BODY_RENDERER_VERSION != news_benzinga_body_v4.BODY_RENDERER_VERSION:
        raise RuntimeError("targeted purity repair is supported only for Body V4")
    create_body_v3_tables(client, target)
    marker = body_marker_sql("r.canonical_body_text")
    wrapper = body_binary_wrapper_sql("r.canonical_body_text")
    projection = ", ".join(f"e.{quote_ident(name)}" for name in V2_EVENT_COLUMNS)
    source_rows = parse_json_each_rows(client.execute(f"""
SELECT {projection}
FROM {quote_ident(args.database)}.{quote_ident(args.source_table)} AS e FINAL
INNER JOIN {quote_ident(target.database)}.{quote_ident(target.rendered_table)} AS r FINAL
  ON r.canonical_news_id=e.canonical_news_id
WHERE r.renderer_version={sql_string(BODY_RENDERER_VERSION)} AND ({wrapper} OR {marker})
ORDER BY e.published_at_utc,e.provider_article_id
FORMAT JSONEachRow
"""))
    if not source_rows:
        report = {"run_id": run_id, "status": "nothing_to_repair", "input_rows": 0, "purity_errors": 0}
        report_path = run_root / "purity_repair.json"
        report_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
        print(f"BODY PURITY REPAIR | rows=0 report={report_path}", flush=True)
        return 0

    previous = load_flagged_previous_hashes(client, target, args.previous_rendered_table)
    source_parts = load_flagged_previous_sources(
        client, target, args.source_table, args.previous_source_table,
    )
    path_maps = parse_path_maps(args.path_prefix_map) or [DEFAULT_PATH_MAP]
    built_rows: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = [
            executor.submit(
                render_body_one,
                row,
                previous,
                source_parts.get(str(row.get("canonical_news_id") or ""), []),
                path_maps,
            )
            for row in source_rows
        ]
        for row, future in zip(source_rows, futures):
            try:
                built = future.result()
                reasons = body_purity_reasons(str(built["rendered"]["canonical_body_text"]))
                if reasons:
                    raise RuntimeError("surviving purity reasons: " + ",".join(reasons))
                built_rows.append(built)
            except Exception as exc:  # noqa: BLE001
                failures.append({
                    "canonical_news_id": str(row.get("canonical_news_id") or ""),
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:400],
                })
    if failures or len(built_rows) != len(source_rows):
        for failure in failures:
            append_jsonl(run_root / "purity_repair_errors.jsonl", failure)
        raise RuntimeError(
            f"targeted purity repair built {len(built_rows):,}/{len(source_rows):,}; "
            f"see {run_root / 'purity_repair_errors.jsonl'}"
        )
    client.close()
    insert_body_rows(client, target, built_rows, execute=True, max_rows=args.insert_batch_size)
    remaining = int(client.execute(f"""
SELECT countIf({body_binary_wrapper_sql('canonical_body_text')} OR {body_marker_sql('canonical_body_text')})
FROM {quote_ident(target.database)}.{quote_ident(target.rendered_table)} FINAL
WHERE renderer_version={sql_string(BODY_RENDERER_VERSION)} FORMAT TSV
""").strip() or 0)
    report = {
        "run_id": run_id,
        "status": "repaired" if remaining == 0 else "repair_incomplete",
        "input_rows": len(source_rows),
        "written_rows": len(built_rows),
        "remaining_purity_rows": remaining,
        "operator_labels_observed": operator_label_snapshot(client, target.database),
        "operator_label_note": "Read-only observation only; purity repair never writes label or note tables.",
    }
    report_path = run_root / "purity_repair.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    if remaining:
        raise RuntimeError(f"targeted purity repair left {remaining:,} rows; report={report_path}")
    print(f"BODY PURITY REPAIR | rows={len(built_rows):,} remaining=0 report={report_path}", flush=True)
    return 0


def load_flagged_previous_hashes(
    client: RetryingClickHouseHttpClient,
    target: NewsBodyV3TargetConfig,
    previous_table: str,
) -> dict[str, tuple[str, str]]:
    marker = body_marker_sql("r.canonical_body_text")
    wrapper = body_binary_wrapper_sql("r.canonical_body_text")
    return {
        str(row["canonical_news_id"]): (str(row["previous_text_hash"]), str(row["renderer_version"]))
        for row in parse_json_each_rows(client.execute(f"""
SELECT p.canonical_news_id,p.body_hash AS previous_text_hash,p.renderer_version
FROM {quote_ident(target.database)}.{quote_ident(previous_table)} AS p FINAL
INNER JOIN {quote_ident(target.database)}.{quote_ident(target.rendered_table)} AS r FINAL
  ON r.canonical_news_id=p.canonical_news_id
WHERE r.renderer_version={sql_string(BODY_RENDERER_VERSION)} AND ({wrapper} OR {marker})
FORMAT JSONEachRow
"""))
    }


def load_flagged_previous_sources(
    client: RetryingClickHouseHttpClient,
    target: NewsBodyV3TargetConfig,
    event_table: str,
    source_table: str,
) -> dict[str, list[dict[str, Any]]]:
    marker = body_marker_sql("r.canonical_body_text")
    wrapper = body_binary_wrapper_sql("r.canonical_body_text")
    rows = parse_json_each_rows(client.execute(f"""
SELECT s.*
FROM {quote_ident(target.database)}.{quote_ident(source_table)} AS s FINAL
INNER JOIN {quote_ident(target.database)}.{quote_ident(event_table)} AS e FINAL
  ON s.canonical_news_id=e.canonical_news_id AND s.source_revision_key=e.source_revision_key
INNER JOIN {quote_ident(target.database)}.{quote_ident(target.rendered_table)} AS r FINAL
  ON r.canonical_news_id=e.canonical_news_id
WHERE r.renderer_version={sql_string(BODY_RENDERER_VERSION)} AND ({wrapper} OR {marker})
ORDER BY s.canonical_news_id,s.source_kind,s.source_ordinal
FORMAT JSONEachRow
"""))
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row.get("canonical_news_id") or ""), []).append(row)
    return grouped


def render_body_one(
    row: dict[str, Any], previous: dict[str, tuple[str, str]], previous_sources: list[dict[str, Any]],
    path_maps: list[tuple[str, str]],
) -> dict[str, Any]:
    row = dict(row)
    row["pdf_artifact_paths"] = [
        str(resolved) for value in row.get("pdf_artifact_paths") or []
        if (resolved := resolve_path(str(value), path_maps)) is not None
    ]
    raw_path = resolve_path(str(row.get("raw_artifact_path") or ""), path_maps)
    if raw_path and raw_path.exists():
        payload = json.loads(raw_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"raw payload is not an object: {raw_path}")
    else:
        payload = {
            "id": row.get("provider_article_id"), "title": row.get("title"), "teaser": row.get("teaser"),
            "url": row.get("article_url"), "tickers": row.get("tickers") or [],
        }
    provider_source = next(
        (source for source in previous_sources if source.get("source_kind") == "provider_body"), None,
    )
    if not payload.get("body") and provider_source:
        row["body_text"] = str(provider_source.get("rendered_text") or "")
    enrichments = [
        enrichment_from_previous_source(source, path_maps)
        for source in previous_sources
        if source.get("source_kind") in {"external", "pdf"}
    ]
    body = render_canonical_body(payload, normalized_row=row, enrichment_rows=enrichments)
    old_hash, old_renderer = previous.get(str(row.get("canonical_news_id") or ""), ("0" * 64, NEWS_RENDERER_VERSION))
    return build_body_v3_rows(
        payload, row, body, previous_rendered_text_hash=old_hash or "0" * 64,
        previous_renderer_version=old_renderer or NEWS_RENDERER_VERSION,
    )


def load_body_source_window(
    client: RetryingClickHouseHttpClient, database: str, table: str, start: date, end: date,
) -> list[dict[str, Any]]:
    projection = ", ".join(quote_ident(name) for name in V2_EVENT_COLUMNS)
    rows = parse_json_each_rows(client.execute(f"""
SELECT {projection}
FROM {quote_ident(database)}.{quote_ident(table)} FINAL
WHERE published_at_utc >= toDateTime64('{start.isoformat()} 00:00:00', 9, 'UTC')
  AND published_at_utc < toDateTime64('{end.isoformat()} 00:00:00', 9, 'UTC')
ORDER BY published_at_utc, provider_article_id
FORMAT JSONEachRow
"""))
    for row in rows:
        row.setdefault("body_text", "")
        row.setdefault("external_text", "")
        row.setdefault("pdf_text", "")
        row.setdefault("pdf_artifact_paths", [])
    return rows


def load_previous_sources(
    client: RetryingClickHouseHttpClient, database: str, event_table: str, source_table: str,
    start: date, end: date,
) -> dict[str, list[dict[str, Any]]]:
    if not table_exists(client, database, source_table):
        return {}
    rows = parse_json_each_rows(client.execute(f"""
SELECT s.*
FROM {quote_ident(database)}.{quote_ident(source_table)} AS s FINAL
INNER JOIN {quote_ident(database)}.{quote_ident(event_table)} AS e FINAL
  ON s.canonical_news_id=e.canonical_news_id AND s.source_revision_key=e.source_revision_key
WHERE e.published_date>=toDate('{start.isoformat()}') AND e.published_date<toDate('{end.isoformat()}')
ORDER BY s.canonical_news_id,s.source_kind,s.source_ordinal
FORMAT JSONEachRow
"""))
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row.get("canonical_news_id") or ""), []).append(row)
    return grouped


def parse_json_each_rows(payload: str) -> list[dict[str, Any]]:
    # Split only on the JSONEachRow record delimiter. str.splitlines() also
    # treats retained C1 characters such as U+0085 as line boundaries and can
    # therefore cut a valid source row in the middle of its JSON string.
    return [json.loads(line) for line in payload.split("\n") if line.strip()]


def enrichment_from_previous_source(
    source: dict[str, Any], path_maps: list[tuple[str, str]],
) -> dict[str, Any]:
    source_kind = str(source.get("source_kind") or "")
    artifact = resolve_path(str(source.get("artifact_path") or ""), path_maps)
    artifact_text = _read_text_artifact(artifact) if source_kind == "external" else ""
    return {
        "extracted_text": str(source.get("rendered_text") or ""),
        "raw_html": artifact_text if "<" in artifact_text and ">" in artifact_text else "",
        "fetched_sha256": str(source.get("original_hash") or source.get("source_hash") or ""),
        "extracted_text_hash": str(source.get("cleaned_hash") or source.get("rendered_hash") or ""),
        "final_url": str(source.get("source_url") or ""),
        "artifact_path": str(artifact or ""),
        "resolved_action": "fetch_pdf" if source_kind == "pdf" else "fetch_html",
        "extraction_method": "pdf_retained_artifact" if source_kind == "pdf" else "html_retained_artifact",
    }


def _read_text_artifact(path: Path | None) -> str:
    if path is None or not path.exists():
        return ""
    try:
        if path.suffix.casefold() == ".gz":
            with gzip.open(path, "rt", encoding="utf-8", errors="replace") as handle:
                return handle.read()
        return path.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeError):
        return ""


def insert_body_rows(
    client: RetryingClickHouseHttpClient, target: NewsBodyV3TargetConfig, rows: list[dict[str, Any]],
    *, execute: bool, max_rows: int,
) -> None:
    products = [
        (target.event_table, EVENT_COLUMNS, "event"), (target.source_table, SOURCE_COLUMNS, "sources"),
        (target.block_table, BLOCK_COLUMNS, "blocks"), (target.rendered_table, RENDERED_COLUMNS, "rendered"),
        (target.ticker_table, TICKER_COLUMNS, "tickers"), (target.lineage_table, LINEAGE_COLUMNS, "lineage"),
    ]
    for table, columns, key in products:
        flat: list[dict[str, Any]] = []
        for row in rows:
            value = row[key]
            flat.extend(value if isinstance(value, list) else [value])
        batches = list(json_each_row_batches(
            flat, table=table, max_rows=max_rows, target_bytes=4 * 1024 * 1024, max_row_bytes=8 * 1024 * 1024
        ))
        if not execute:
            continue
        for batch_index, batch in enumerate(batches, start=1):
            insert_json_each_row(
                client, target.database, table, columns, batch.rows,
                query_id=v2_batch_query_id(table, batch_index, batch.rows),
            )


def certify_body_authority(
    client: RetryingClickHouseHttpClient, target: NewsBodyV3TargetConfig, source_table: str, *, full_scope: bool,
) -> dict[str, Any]:
    db = quote_ident(target.database)
    rendered = f"{db}.{quote_ident(target.rendered_table)}"
    source = f"{db}.{quote_ident(source_table)}"
    source_parts = f"{db}.{quote_ident(target.source_table)}"
    blocks = f"{db}.{quote_ident(target.block_table)}"
    lineage = f"{db}.{quote_ident(target.lineage_table)}"
    values = json.loads(client.execute(f"""
SELECT
 (SELECT count() FROM {source} FINAL) AS expected_rows,
 (SELECT count() FROM {rendered} FINAL) AS rendered_rows,
 (SELECT countIf(renderer_version={sql_string(BODY_RENDERER_VERSION)}) FROM {db}.{quote_ident(target.event_table)} FINAL) AS current_event_rows,
 (SELECT countIf(renderer_version={sql_string(BODY_RENDERER_VERSION)}) FROM {rendered} FINAL) AS current_rendered_rows,
 (SELECT count() FROM {lineage} AS l FINAL INNER JOIN {rendered} AS r FINAL
   ON l.canonical_news_id=r.canonical_news_id AND l.source_revision_key=r.source_revision_key
   WHERE l.body_renderer_version={sql_string(BODY_RENDERER_VERSION)}
     AND r.renderer_version={sql_string(BODY_RENDERER_VERSION)}) AS current_lineage_rows,
 (SELECT sum(length(arrayDistinct(tickers))) FROM {db}.{quote_ident(target.event_table)} FINAL WHERE renderer_version={sql_string(BODY_RENDERER_VERSION)}) AS expected_ticker_rows,
 (SELECT count() FROM {db}.{quote_ident(target.ticker_table)} AS t FINAL INNER JOIN {rendered} AS r FINAL
   ON t.canonical_news_id=r.canonical_news_id AND t.source_revision_key=r.source_revision_key
   WHERE t.renderer_version={sql_string(BODY_RENDERER_VERSION)} AND r.renderer_version={sql_string(BODY_RENDERER_VERSION)}) AS current_ticker_rows,
 (SELECT sum(source_count) FROM {rendered} FINAL WHERE renderer_version={sql_string(BODY_RENDERER_VERSION)}) AS expected_source_parts,
 (SELECT count() FROM {source_parts} AS s FINAL INNER JOIN {rendered} AS r FINAL
   ON s.canonical_news_id=r.canonical_news_id AND s.source_revision_key=r.source_revision_key
   WHERE s.cleaner_version={sql_string(BODY_CLEANER_VERSION)} AND r.renderer_version={sql_string(BODY_RENDERER_VERSION)}) AS current_source_parts,
 (SELECT sum(included_block_count+excluded_block_count) FROM {rendered} FINAL WHERE renderer_version={sql_string(BODY_RENDERER_VERSION)}) AS expected_block_rows,
 (SELECT count() FROM {blocks} AS b FINAL INNER JOIN {rendered} AS r FINAL
   ON b.canonical_news_id=r.canonical_news_id AND b.source_revision_key=r.source_revision_key
   WHERE b.cleaner_version={sql_string(BODY_CLEANER_VERSION)} AND r.renderer_version={sql_string(BODY_RENDERER_VERSION)}) AS current_block_rows,
 (SELECT countIf(body_status='missing') FROM {rendered} FINAL) AS missing_body_rows,
 (SELECT countIf(body_status='partial') FROM {rendered} FINAL) AS partial_body_rows,
 (SELECT countIf(body_status!='missing' AND included_source_count!=1) FROM {rendered} FINAL) AS invalid_primary_rows,
 (SELECT countIf(body_status='missing' AND included_source_count!=0) FROM {rendered} FINAL) AS invalid_missing_rows,
 (SELECT countIf({body_binary_wrapper_sql('canonical_body_text')}) FROM {rendered} FINAL) AS wrapper_binary_rows,
 (SELECT countIf({body_marker_sql('canonical_body_text')}) FROM {rendered} FINAL) AS marker_rows,
 (SELECT count() FROM {lineage} FINAL WHERE body_renderer_version={sql_string(BODY_RENDERER_VERSION)} AND label_mutation_status!='not_mutated') AS label_mutation_rows,
 (SELECT count() FROM {source_parts} AS s FINAL INNER JOIN {rendered} AS r FINAL
   ON s.canonical_news_id=r.canonical_news_id AND s.source_revision_key=r.source_revision_key
   WHERE s.cleaner_version={sql_string(BODY_CLEANER_VERSION)} AND r.renderer_version={sql_string(BODY_RENDERER_VERSION)}
     AND s.disposition='included'
     AND (s.source_role!='primary_body' OR has(s.quality_flags,'legacy_flattened_enrichment'))) AS invalid_included_sources
FORMAT JSONEachRow
""").strip())
    values.update(certify_partition_relations(client, target))
    errors: list[str] = []
    if full_scope and int(values["expected_rows"]) != int(values["rendered_rows"]):
        errors.append("rendered_cardinality_mismatch")
    if full_scope:
        for field in ("current_event_rows", "current_rendered_rows", "current_lineage_rows"):
            if int(values[field]) != int(values["expected_rows"]):
                errors.append(f"{field}_mismatch")
        for expected_field, actual_field in (
            ("expected_ticker_rows", "current_ticker_rows"),
            ("expected_source_parts", "current_source_parts"),
            ("expected_block_rows", "current_block_rows"),
        ):
            if int(values[expected_field]) != int(values[actual_field]):
                errors.append(f"{actual_field}_mismatch")
    for field in ("invalid_primary_rows", "invalid_missing_rows", "wrapper_binary_rows", "marker_rows",
                  "label_mutation_rows", "orphan_sources", "orphan_blocks",
                  "duplicate_included_blocks", "invalid_included_sources",
                  "per_article_source_mismatches", "per_article_block_mismatches"):
        if int(values[field]):
            errors.append(field)
    status = "certified" if full_scope and not errors else "partial_validated" if not errors else "audit_failed"
    return {"status": status, "errors": errors, "metrics": values}


def certify_partition_relations(
    client: RetryingClickHouseHttpClient, target: NewsBodyV3TargetConfig,
) -> dict[str, int]:
    db = quote_ident(target.database)
    rendered = f"{db}.{quote_ident(target.rendered_table)}"
    sources = f"{db}.{quote_ident(target.source_table)}"
    blocks = f"{db}.{quote_ident(target.block_table)}"
    lineage = f"{db}.{quote_ident(target.lineage_table)}"
    scope = client.execute(
        f"SELECT min(published_date),max(published_date) FROM {rendered} FINAL "
        f"WHERE renderer_version={sql_string(BODY_RENDERER_VERSION)} FORMAT TSV"
    ).strip().split("\t")
    metrics = {
        "orphan_sources": 0,
        "orphan_blocks": 0,
        "duplicate_included_blocks": 0,
        "per_article_source_mismatches": 0,
        "per_article_block_mismatches": 0,
    }
    if len(scope) != 2 or not scope[0] or scope[0] == "1970-01-01":
        return metrics
    month = date.fromisoformat(scope[0]).replace(day=1)
    final_month = date.fromisoformat(scope[1]).replace(day=1)
    while month <= final_month:
        next_month = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
        source_predicate = f"s.published_date>=toDate('{month}') AND s.published_date<toDate('{next_month}')"
        block_predicate = f"b.published_date>=toDate('{month}') AND b.published_date<toDate('{next_month}')"
        rendered_predicate = f"r.published_date>=toDate('{month}') AND r.published_date<toDate('{next_month}')"
        plain_predicate = f"published_date>=toDate('{month}') AND published_date<toDate('{next_month}')"
        row = json.loads(client.execute(f"""
SELECT
 (SELECT count() FROM {sources} AS s FINAL LEFT JOIN {lineage} AS l FINAL
   ON s.canonical_news_id=l.canonical_news_id AND s.source_revision_key=l.source_revision_key
   WHERE {source_predicate} AND s.cleaner_version={sql_string(BODY_CLEANER_VERSION)} AND l.canonical_news_id='') orphan_sources,
 (SELECT count() FROM {blocks} AS b FINAL LEFT JOIN {lineage} AS l FINAL
   ON b.canonical_news_id=l.canonical_news_id AND b.source_revision_key=l.source_revision_key
   WHERE {block_predicate} AND b.cleaner_version={sql_string(BODY_CLEANER_VERSION)} AND l.canonical_news_id='') orphan_blocks,
 (SELECT count() FROM
   (SELECT canonical_news_id,source_revision_key,source_kind,source_ordinal,cleaned_hash,count() rows
    FROM {blocks} FINAL WHERE {plain_predicate} AND cleaner_version={sql_string(BODY_CLEANER_VERSION)}
      AND disposition='included'
    GROUP BY canonical_news_id,source_revision_key,source_kind,source_ordinal,cleaned_hash HAVING rows>1)) duplicate_included_blocks,
 (SELECT count() FROM
   (SELECT r.canonical_news_id,r.source_revision_key,any(r.source_count) expected,countIf(notEmpty(s.canonical_news_id)) actual
    FROM {rendered} AS r FINAL LEFT JOIN {sources} AS s FINAL
      ON r.canonical_news_id=s.canonical_news_id AND r.source_revision_key=s.source_revision_key
      AND s.cleaner_version={sql_string(BODY_CLEANER_VERSION)}
    WHERE {rendered_predicate} AND r.renderer_version={sql_string(BODY_RENDERER_VERSION)}
    GROUP BY r.canonical_news_id,r.source_revision_key HAVING expected!=actual)) per_article_source_mismatches,
 (SELECT count() FROM
   (SELECT r.canonical_news_id,r.source_revision_key,
           any(r.included_block_count+r.excluded_block_count) expected,countIf(notEmpty(b.canonical_news_id)) actual
    FROM {rendered} AS r FINAL LEFT JOIN {blocks} AS b FINAL
      ON r.canonical_news_id=b.canonical_news_id AND r.source_revision_key=b.source_revision_key
      AND b.cleaner_version={sql_string(BODY_CLEANER_VERSION)}
    WHERE {rendered_predicate} AND r.renderer_version={sql_string(BODY_RENDERER_VERSION)}
    GROUP BY r.canonical_news_id,r.source_revision_key HAVING expected!=actual)) per_article_block_mismatches
FORMAT JSONEachRow
""").strip())
        for key in metrics:
            metrics[key] += int(row[key])
        month = next_month
    return metrics


def run_control_action(
    client: RetryingClickHouseHttpClient, target: NewsBodyV3TargetConfig, action: str, run_id: str, run_root: Path,
) -> int:
    if not table_exists(client, target.database, target.authority_table):
        raise RuntimeError("body authority table does not exist; run a full certified rebuild first")
    table = f"{quote_ident(target.database)}.{quote_ident(target.authority_table)}"
    rows = parse_json_each_rows(client.execute(
        f"SELECT * FROM {table} FINAL WHERE renderer_version={sql_string(BODY_RENDERER_VERSION)} FORMAT JSONEachRow"
    ))
    if not rows:
        raise RuntimeError(f"no authority row for {BODY_RENDERER_VERSION}")
    current = rows[-1]
    current_status = str(current.get("status") or "")
    if action == "promote" and current_status != "certified":
        raise RuntimeError(f"promotion requires certified status, found {current_status}")
    if action == "rollback" and current_status != "promoted":
        raise RuntimeError(f"rollback requires promoted status, found {current_status}")
    active_rows = parse_json_each_rows(client.execute(
        f"SELECT * FROM {table} FINAL WHERE is_active=1 FORMAT JSONEachRow"
    ))
    previous_active = next(
        (str(row.get("renderer_version") or "") for row in active_rows if row.get("renderer_version") != BODY_RENDERER_VERSION),
        str(current.get("previous_active_renderer_version") or ""),
    )
    status = "promoted" if action == "promote" else "rolled_back" if action == "rollback" else current_status
    counts = BodyBuildCounts(
        source_rows=int(current.get("source_rows") or 0), rendered_rows=int(current.get("rendered_rows") or 0),
        missing_body_rows=int(current.get("missing_body_rows") or 0), partial_body_rows=int(current.get("partial_body_rows") or 0),
        purity_error_rows=int(current.get("purity_error_rows") or 0),
    )
    write_authority(
        client, target, run_id, status, counts, str(current.get("audit_report_path") or ""),
        str(current.get("started_at_utc") or datetime64_utc_text()),
        relational_errors=int(current.get("relational_error_rows") or 0), is_active=action == "promote",
        previous_active=previous_active,
    )
    if action == "promote":
        for row in active_rows:
            version = str(row.get("renderer_version") or "")
            if version and version != BODY_RENDERER_VERSION:
                write_authority_from_existing(client, target, row, run_id, "superseded", is_active=False)
    elif previous_active:
        prior = client.execute(
            f"SELECT * FROM {table} FINAL WHERE renderer_version={sql_string(previous_active)} FORMAT JSONEachRow"
        ).strip()
        if prior:
            write_authority_from_existing(client, target, json.loads(prior), run_id, "promoted", is_active=True)
    (run_root / "control.json").write_text(json.dumps({"action": action, "status": status, "renderer_version": BODY_RENDERER_VERSION}, indent=2), encoding="utf-8")
    print(f"BODY AUTHORITY {action.upper()} | renderer={BODY_RENDERER_VERSION} status={status}", flush=True)
    return 0


def certify_existing(
    client: RetryingClickHouseHttpClient,
    target: NewsBodyV3TargetConfig,
    source_table: str,
    run_id: str,
    run_root: Path,
) -> int:
    audit = certify_body_authority(client, target, source_table, full_scope=True)
    audit.update({
        "contract": contract_manifest(),
        "run_id": run_id,
        "full_scope": True,
        "operator_labels_observed": operator_label_snapshot(client, target.database),
        "operator_label_note": "Read-only observation only; certification never writes operator label or note tables.",
    })
    report_path = run_root / "certification.json"
    report_path.write_text(json.dumps(audit, indent=2, sort_keys=True), encoding="utf-8")
    write_certified_samples(client, target, run_root / "certified_samples.jsonl")
    metrics = audit["metrics"]
    counts = BodyBuildCounts(
        source_rows=int(metrics["expected_rows"]),
        rendered_rows=int(metrics["rendered_rows"]),
        missing_body_rows=int(metrics["missing_body_rows"]),
        partial_body_rows=int(metrics["partial_body_rows"]),
        purity_error_rows=int(metrics["wrapper_binary_rows"]) + int(metrics["marker_rows"]),
    )
    status = "certified" if not audit["errors"] else "audit_failed"
    write_authority(
        client,
        target,
        run_id,
        status,
        counts,
        str(report_path),
        datetime64_utc_text(),
        relational_errors=len(audit["errors"]),
    )
    if status != "certified":
        raise RuntimeError(f"{BODY_RENDERER_VERSION} was not certified: {audit['errors']}; report={report_path}")
    print(f"BODY AUTHORITY CERTIFIED | renderer={BODY_RENDERER_VERSION} report={report_path}", flush=True)
    return 0


def write_certified_samples(
    client: RetryingClickHouseHttpClient,
    target: NewsBodyV3TargetConfig,
    path: Path,
    *,
    per_category: int = 20,
) -> None:
    table = f"{quote_ident(target.database)}.{quote_ident(target.rendered_table)}"
    categories = {
        "complete": "body_status='complete'",
        "partial": "body_status='partial'",
        "missing": "body_status='missing'",
        "supporting_source_promoted": "primary_source_kind IN ('external','pdf')",
        "excluded_blocks": "excluded_block_count>0",
    }
    for category, predicate in categories.items():
        rows = client.execute(f"""
SELECT canonical_news_id,published_date,title,body_status,primary_source_kind,body_hash,
       length(canonical_body_text) body_chars,excluded_block_count,canonical_body_text
FROM {table} FINAL
WHERE renderer_version={sql_string(BODY_RENDERER_VERSION)} AND {predicate}
ORDER BY cityHash64(canonical_news_id) LIMIT {int(per_category)} FORMAT JSONEachRow
""").split("\n")
        for line in rows:
            if line.strip():
                append_jsonl(path, {"category": category, **json.loads(line)})


def write_authority(
    client: RetryingClickHouseHttpClient, target: NewsBodyV3TargetConfig, run_id: str, status: str,
    counts: BodyBuildCounts, report_path: str, started_at: str, relational_errors: int = 0,
    is_active: bool = False, previous_active: str = "", renderer_version: str | None = None,
) -> None:
    effective_renderer_version = renderer_version or BODY_RENDERER_VERSION
    row = {
        "renderer_version": effective_renderer_version, "run_id": run_id, "status": status,
        "is_active": int(is_active), "source_table": target.source_table, "rendered_table": target.rendered_table,
        "source_rows": counts.source_rows, "rendered_rows": counts.rendered_rows,
        "missing_body_rows": counts.missing_body_rows, "partial_body_rows": counts.partial_body_rows,
        "purity_error_rows": counts.purity_error_rows, "relational_error_rows": relational_errors,
        "audit_report_path": report_path, "previous_active_renderer_version": previous_active,
        "started_at_utc": started_at, "updated_at_utc": datetime64_utc_text(),
    }
    insert_json_each_row(
        client, target.database, target.authority_table, list(row), [row],
        query_id=v2_batch_query_id(target.authority_table, 1, [row]),
    )


def write_authority_from_existing(
    client: RetryingClickHouseHttpClient,
    target: NewsBodyV3TargetConfig,
    existing: dict[str, Any],
    run_id: str,
    status: str,
    *,
    is_active: bool,
) -> None:
    counts = BodyBuildCounts(
        source_rows=int(existing.get("source_rows") or 0),
        rendered_rows=int(existing.get("rendered_rows") or 0),
        missing_body_rows=int(existing.get("missing_body_rows") or 0),
        partial_body_rows=int(existing.get("partial_body_rows") or 0),
        purity_error_rows=int(existing.get("purity_error_rows") or 0),
    )
    write_authority(
        client,
        target,
        run_id,
        status,
        counts,
        str(existing.get("audit_report_path") or ""),
        str(existing.get("started_at_utc") or datetime64_utc_text()),
        relational_errors=int(existing.get("relational_error_rows") or 0),
        is_active=is_active,
        previous_active=str(existing.get("previous_active_renderer_version") or ""),
        renderer_version=str(existing.get("renderer_version") or ""),
    )


def load_previous_hashes(
    client: RetryingClickHouseHttpClient, database: str, table: str, start: date, end: date | None = None,
) -> dict[str, tuple[str, str]]:
    if not table_exists(client, database, table):
        return {}
    end = end or start + timedelta(days=1)
    columns = {
        value
        for value in client.execute(
            f"SELECT name FROM system.columns WHERE database={sql_string(database)} "
            f"AND table={sql_string(table)} FORMAT TSV"
        ).splitlines()
        if value
    }
    hash_column = "body_hash" if "body_hash" in columns else "rendered_text_hash"
    if hash_column not in columns or "renderer_version" not in columns:
        raise RuntimeError(f"previous rendered table has no supported hash contract: {database}.{table}")
    sql = f"""
SELECT canonical_news_id, {quote_ident(hash_column)} AS previous_text_hash, renderer_version
FROM {quote_ident(database)}.{quote_ident(table)} FINAL
WHERE published_date >= toDate('{start.isoformat()}') AND published_date < toDate('{end.isoformat()}')
FORMAT JSONEachRow
"""
    return {
        str(row["canonical_news_id"]): (str(row["previous_text_hash"]), str(row["renderer_version"]))
        for line in client.execute(sql).split("\n") if line.strip() for row in [json.loads(line)]
    }


def operator_label_snapshot(client: RetryingClickHouseHttpClient, database: str) -> dict[str, Any]:
    table = "news_synthesis_v61_operator_label_history_v3"
    if not table_exists(client, database, table):
        return {"table": table, "present": False}
    row = client.execute(
        f"SELECT count() rows, max(updated_at_utc) latest_update FROM {quote_ident(database)}.{quote_ident(table)} FORMAT JSONEachRow"
    ).strip()
    return {"table": table, "present": True, **(json.loads(row) if row else {})}


def body_day_is_complete(
    client: RetryingClickHouseHttpClient, target: NewsBodyV3TargetConfig, day: date, expected: int,
) -> bool:
    if not all(table_exists(client, target.database, table) for table in (target.event_table, target.rendered_table, target.lineage_table)):
        return False
    values = client.execute(f"""
SELECT
 (SELECT count() FROM {quote_ident(target.database)}.{quote_ident(target.event_table)} FINAL WHERE published_date=toDate('{day}') AND renderer_version={sql_string(BODY_RENDERER_VERSION)}),
 (SELECT count() FROM {quote_ident(target.database)}.{quote_ident(target.rendered_table)} FINAL WHERE published_date=toDate('{day}') AND renderer_version={sql_string(BODY_RENDERER_VERSION)}),
 (SELECT count() FROM {quote_ident(target.database)}.{quote_ident(target.lineage_table)} AS l FINAL
   INNER JOIN {quote_ident(target.database)}.{quote_ident(target.rendered_table)} AS r FINAL
     ON l.canonical_news_id=r.canonical_news_id AND l.source_revision_key=r.source_revision_key
   WHERE l.published_date=toDate('{day}') AND l.body_renderer_version={sql_string(BODY_RENDERER_VERSION)}
     AND r.renderer_version={sql_string(BODY_RENDERER_VERSION)}),
 (SELECT count() FROM
   (SELECT r.canonical_news_id, r.source_revision_key, r.source_count, r.included_block_count+r.excluded_block_count expected_blocks,
           countIf(notEmpty(s.canonical_news_id)) actual_sources,
           any(b.actual_blocks) actual_blocks
    FROM {quote_ident(target.database)}.{quote_ident(target.rendered_table)} AS r FINAL
    LEFT JOIN {quote_ident(target.database)}.{quote_ident(target.source_table)} AS s FINAL
      ON r.canonical_news_id=s.canonical_news_id AND r.source_revision_key=s.source_revision_key
    LEFT JOIN
      (SELECT canonical_news_id,source_revision_key,count() actual_blocks
       FROM {quote_ident(target.database)}.{quote_ident(target.block_table)} FINAL
       WHERE published_date=toDate('{day}') AND cleaner_version={sql_string(BODY_CLEANER_VERSION)} GROUP BY canonical_news_id,source_revision_key) b
      ON r.canonical_news_id=b.canonical_news_id AND r.source_revision_key=b.source_revision_key
    WHERE r.published_date=toDate('{day}') AND r.renderer_version={sql_string(BODY_RENDERER_VERSION)}
    GROUP BY r.canonical_news_id,r.source_revision_key,r.source_count,r.included_block_count,r.excluded_block_count
    HAVING source_count!=actual_sources OR expected_blocks!=actual_blocks))
FORMAT TSV
""").strip().split("\t")
    return len(values) == 4 and all(int(value or 0) == expected for value in values[:3]) and int(values[3] or 0) == 0


def load_source_counts_by_day(
    client: RetryingClickHouseHttpClient, database: str, table: str, start: date, end: date,
) -> dict[date, int]:
    rows = client.execute(f"""
SELECT published_date,count() rows
FROM {quote_ident(database)}.{quote_ident(table)} FINAL
WHERE published_date>=toDate('{start.isoformat()}') AND published_date<toDate('{end.isoformat()}')
GROUP BY published_date FORMAT JSONEachRow
""").split("\n")
    return {
        date.fromisoformat(str(row["published_date"])): int(row["rows"])
        for line in rows if line.strip() for row in [json.loads(line)]
    }


def load_complete_days(
    client: RetryingClickHouseHttpClient,
    target: NewsBodyV3TargetConfig,
    expected_by_day: dict[date, int],
) -> set[date]:
    required = (target.event_table, target.rendered_table, target.lineage_table)
    if not all(table_exists(client, target.database, table) for table in required):
        return set()
    rows = client.execute(f"""
SELECT e.published_date AS published_date,e.rows event_rows,r.rows rendered_rows,l.rows lineage_rows
FROM
 (SELECT published_date,count() rows FROM {quote_ident(target.database)}.{quote_ident(target.event_table)} FINAL WHERE renderer_version={sql_string(BODY_RENDERER_VERSION)} GROUP BY published_date) e
INNER JOIN
 (SELECT published_date,count() rows FROM {quote_ident(target.database)}.{quote_ident(target.rendered_table)} FINAL WHERE renderer_version={sql_string(BODY_RENDERER_VERSION)} GROUP BY published_date) r
 USING published_date
INNER JOIN
 (SELECT l.published_date,count() rows
  FROM {quote_ident(target.database)}.{quote_ident(target.lineage_table)} AS l FINAL
  INNER JOIN {quote_ident(target.database)}.{quote_ident(target.rendered_table)} AS r FINAL
    ON l.canonical_news_id=r.canonical_news_id AND l.source_revision_key=r.source_revision_key
  WHERE l.body_renderer_version={sql_string(BODY_RENDERER_VERSION)}
    AND r.renderer_version={sql_string(BODY_RENDERER_VERSION)} GROUP BY l.published_date) l
 USING published_date
FORMAT JSONEachRow
""").split("\n")
    completed: set[date] = set()
    for line in rows:
        if not line.strip():
            continue
        row = json.loads(line)
        day = date.fromisoformat(str(row["published_date"]))
        expected = expected_by_day.get(day, -1)
        if expected >= 0 and all(int(row[field]) == expected for field in ("event_rows", "rendered_rows", "lineage_rows")):
            completed.add(day)
    return completed


def load_body_day_counts(
    client: RetryingClickHouseHttpClient, target: NewsBodyV3TargetConfig, day: date,
) -> BodyBuildCounts:
    row = client.execute(f"""
SELECT
 (SELECT count() FROM {quote_ident(target.database)}.{quote_ident(target.rendered_table)} FINAL WHERE published_date=toDate('{day}')) rendered_rows,
 (SELECT count() FROM {quote_ident(target.database)}.{quote_ident(target.source_table)} FINAL WHERE published_date=toDate('{day}') AND cleaner_version={sql_string(BODY_CLEANER_VERSION)}) source_parts,
 (SELECT count() FROM {quote_ident(target.database)}.{quote_ident(target.block_table)} FINAL WHERE published_date=toDate('{day}') AND cleaner_version={sql_string(BODY_CLEANER_VERSION)}) block_rows,
 (SELECT count() FROM {quote_ident(target.database)}.{quote_ident(target.ticker_table)} FINAL WHERE published_date=toDate('{day}') AND renderer_version={sql_string(BODY_RENDERER_VERSION)}) ticker_rows,
 (SELECT countIf(body_status='missing') FROM {quote_ident(target.database)}.{quote_ident(target.rendered_table)} FINAL WHERE published_date=toDate('{day}')) missing_body_rows,
 (SELECT countIf(body_status='partial') FROM {quote_ident(target.database)}.{quote_ident(target.rendered_table)} FINAL WHERE published_date=toDate('{day}')) partial_body_rows,
 (SELECT countIf({body_binary_wrapper_sql('canonical_body_text')} OR {body_marker_sql('canonical_body_text')}) FROM {quote_ident(target.database)}.{quote_ident(target.rendered_table)} FINAL WHERE published_date=toDate('{day}')) purity_error_rows
FORMAT JSONEachRow
""").strip()
    values = json.loads(row)
    return BodyBuildCounts(source_rows=int(values["rendered_rows"]), **{key: int(value) for key, value in values.items()})


def load_all_body_counts(
    client: RetryingClickHouseHttpClient, target: NewsBodyV3TargetConfig,
) -> BodyBuildCounts:
    row = client.execute(f"""
SELECT
 (SELECT count() FROM {quote_ident(target.database)}.{quote_ident(target.rendered_table)} FINAL) rendered_rows,
 (SELECT count() FROM {quote_ident(target.database)}.{quote_ident(target.source_table)} FINAL WHERE cleaner_version={sql_string(BODY_CLEANER_VERSION)}) source_parts,
 (SELECT count() FROM {quote_ident(target.database)}.{quote_ident(target.block_table)} FINAL WHERE cleaner_version={sql_string(BODY_CLEANER_VERSION)}) block_rows,
 (SELECT count() FROM {quote_ident(target.database)}.{quote_ident(target.ticker_table)} FINAL WHERE renderer_version={sql_string(BODY_RENDERER_VERSION)}) ticker_rows,
 (SELECT countIf(body_status='missing') FROM {quote_ident(target.database)}.{quote_ident(target.rendered_table)} FINAL) missing_body_rows,
 (SELECT countIf(body_status='partial') FROM {quote_ident(target.database)}.{quote_ident(target.rendered_table)} FINAL) partial_body_rows,
 (SELECT countIf({body_binary_wrapper_sql('canonical_body_text')} OR {body_marker_sql('canonical_body_text')}) FROM {quote_ident(target.database)}.{quote_ident(target.rendered_table)} FINAL) purity_error_rows
FORMAT JSONEachRow
""").strip()
    values = json.loads(row)
    return BodyBuildCounts(source_rows=int(values["rendered_rows"]), **{key: int(value) for key, value in values.items()})


def add_counts(target: BodyBuildCounts, addition: BodyBuildCounts) -> None:
    for field in asdict(target):
        setattr(target, field, getattr(target, field) + getattr(addition, field))


def body_binary_wrapper_sql(column: str) -> str:
    needles = (
        "data:image/", "base64,", "\nTitle:", "\nTeaser:", "\nSource [", "�",
        "â€", "\u00e2\u00c3\u0082",
    )
    positions = [f"positionCaseInsensitive({column},{sql_string(needle)})>0" for needle in needles]
    positions.extend(
        f"startsWith(lowerUTF8({column}),{sql_string(prefix)})"
        for prefix in ("title:", "teaser:", "source [")
    )
    return " OR ".join(positions)


def body_marker_sql(column: str) -> str:
    needles = (
        "\nRead Also", "\nRead Next", "\nRead More", "\n- Read Also", "\n- Read Next", "\n- Read More",
        "\n• Read Also", "\n• Read Next", "\n• Read More", "\nRead full article", "\nContinue reading",
        "\nTo read more", "\nDisclosure:", "\nDisclaimer:",
        "\nReaders are advised", "\nSubscribe now", "\nSign up",
    )
    positions = [f"positionCaseInsensitive({column},{sql_string(needle)})>0" for needle in needles]
    positions.extend(
        f"startsWith(lowerUTF8({column}),{sql_string(prefix)})"
        for prefix in (
            "read also", "read next", "read more", "- read also", "- read next", "- read more",
            "• read also", "• read next", "• read more", "read full article", "continue reading",
            "to read more", "disclosure:", "disclaimer:", "readers are advised", "subscribe now", "sign up",
        )
    )
    positions.append(f"match({column},{sql_string(r'(?s).*[[:space:]][-–—|]?[[:space:]]*(READ|SEE|WATCH|LISTEN)[[:space:]]+(ALSO|NEXT|MORE|RELATED)[[:space:]]*:.*')})")
    return " OR ".join(positions)


def collect_audit_samples(
    samples: dict[str, list[dict[str, Any]]], built: dict[str, Any], *, per_category: int = 20,
) -> None:
    rendered = built["rendered"]
    categories = [str(rendered["body_status"])]
    if rendered["primary_source_kind"] in {"external", "pdf"}:
        categories.append("supporting_source_promoted")
    if rendered["excluded_block_count"]:
        categories.append("excluded_blocks")
    if body_purity_reasons(str(rendered["canonical_body_text"])):
        categories.append("purity_error")
    excluded_reasons = sorted({
        str(block["disposition_reason"])
        for block in built["blocks"]
        if block["disposition"] == "excluded"
    })
    item = {
        "canonical_news_id": rendered["canonical_news_id"],
        "published_date": rendered["published_date"],
        "title": rendered["title"],
        "body_status": rendered["body_status"],
        "primary_source_kind": rendered["primary_source_kind"],
        "body_hash": rendered["body_hash"],
        "body_chars": len(str(rendered["canonical_body_text"])),
        "excluded_reasons": excluded_reasons,
        "canonical_body_text": rendered["canonical_body_text"],
    }
    for category in categories:
        bucket = samples.setdefault(category, [])
        if len(bucket) < per_category:
            bucket.append(item)


def iter_days(start: date, end: date) -> Iterable[date]:
    current = start
    while current < end:
        yield current
        current += timedelta(days=1)


if __name__ == "__main__":
    raise SystemExit(main())
