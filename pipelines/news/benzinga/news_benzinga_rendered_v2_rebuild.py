from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
import hashlib
import html
import json
import os
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Iterator
from urllib import error as url_error

from pipelines.news.benzinga.core.clickhouse_values import datetime64_utc_text
from pipelines.news.benzinga.core.clickhouse_writer import NORMALIZED_COLUMNS, insert_json_each_row
from pipelines.news.benzinga.core.clickhouse_writer_v2 import (
    BLOCK_COLUMNS,
    DEFAULT_INSERT_MAX_ROW_BYTES,
    DEFAULT_INSERT_TARGET_BYTES,
    EVENT_COLUMNS,
    OversizedNewsRowError,
    RENDERED_COLUMNS,
    SOURCE_COLUMNS,
    TICKER_COLUMNS,
    NewsV2TargetConfig,
    create_v2_tables,
    json_each_row_batches,
    v2_batch_query_id,
)
from pipelines.news.benzinga.news_benzinga_render_v2 import (
    NEWS_RENDERER_VERSION,
    build_v2_rows,
    render_news_article,
)
from pipelines.news.benzinga.news_benzinga_url_policy import (
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
)
from research.mlops.clickhouse import ClickHouseHttpClient, quote_ident, sql_string
from research.mlops.env import discover_env_files, load_env_files


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT_ROOT = Path("D:/TradingML/runtimes/news/benzinga_news_rendered_v2")
DEFAULT_PATH_MAP = (r"D:\market-data", r"\\DESKTOP-SAAI85T\Workstation-D\market-data")
DEFAULT_CLICKHOUSE_TIMEOUT_SECONDS = 180.0
TRANSIENT_HTTP_STATUS_CODES = frozenset({408, 425, 429, 502, 503, 504})
TRANSIENT_WINDOWS_SOCKET_ERRORS = frozenset({10053, 10054, 10060, 10061, 10065})


@dataclass(slots=True)
class BuildCounts:
    source_rows: int = 0
    event_rows: int = 0
    rendered_rows: int = 0
    source_parts: int = 0
    block_rows: int = 0
    ticker_rows: int = 0
    raw_artifacts_missing: int = 0
    failures: int = 0


class RetryingClickHouseHttpClient(ClickHouseHttpClient):
    """Bounded retry client for this idempotent, versioned rebuild.

    Every v2 data table is a ReplacingMergeTree with a deterministic identity
    key. Retrying the exact same INSERT payload is therefore safe even when a
    transport failure leaves the request outcome unknown.
    """

    def __init__(
        self,
        base_url: str,
        user: str,
        password: str,
        *,
        attempts: int,
        retry_base_seconds: float,
        retry_max_seconds: float,
        request_timeout_seconds: float,
        status_path: Path,
    ) -> None:
        super().__init__(
            base_url,
            user,
            password,
            timeout_seconds=request_timeout_seconds,
            persistent=True,
            default_query_params={
                # Renderer writes are authoritative data products. Require the
                # server to complete each INSERT before acknowledging it.
                "async_insert": 0,
                "wait_end_of_query": 1,
            },
        )
        self.attempts = max(1, int(attempts))
        self.retry_base_seconds = max(0.0, float(retry_base_seconds))
        self.retry_max_seconds = max(self.retry_base_seconds, float(retry_max_seconds))
        self.status_path = status_path
        self._diagnostic_context: dict[str, Any] = {}

    @contextlib.contextmanager
    def diagnostic_context(self, **values: Any) -> Iterator[None]:
        previous = self._diagnostic_context
        self._diagnostic_context = {
            key: value
            for key, value in values.items()
            if key in {
                "day",
                "table",
                "batch",
                "batch_count",
                "rows",
                "body_bytes",
                "max_row_bytes",
                "query_id",
            }
        }
        try:
            yield
        finally:
            self._diagnostic_context = previous

    def execute(self, sql: str, *, query_id: str | None = None) -> str:
        operation = clickhouse_operation(sql)
        for attempt in range(1, self.attempts + 1):
            try:
                return super().execute(sql, query_id=query_id)
            except Exception as exc:
                if not is_transient_clickhouse_error(exc) or attempt >= self.attempts:
                    raise
                if query_id and operation.startswith("insert:"):
                    try:
                        reconciled = self._reconcile_insert(query_id)
                    except Exception as reconcile_exc:
                        if not is_transient_clickhouse_error(reconcile_exc):
                            raise
                        reconciled = False
                        append_jsonl(
                            self.status_path,
                            {
                                "event": "clickhouse_reconciliation_retry",
                                "at_utc": datetime64_utc_text(),
                                "operation": operation,
                                "query_id": query_id,
                                "error_type": type(reconcile_exc).__name__,
                                "error": bounded_error_text(reconcile_exc),
                                **self._diagnostic_context,
                            },
                        )
                    if reconciled:
                        event = {
                            "event": "clickhouse_insert_reconciled",
                            "at_utc": datetime64_utc_text(),
                            "operation": operation,
                            "query_id": query_id,
                            **self._diagnostic_context,
                        }
                        append_jsonl(self.status_path, event)
                        print(
                            f"CLICKHOUSE RECONCILED | operation={operation} "
                            f"query_id={query_id} "
                            f"{diagnostic_context_text(self._diagnostic_context)}",
                            flush=True,
                        )
                        return ""
                delay = min(
                    self.retry_base_seconds * (2 ** (attempt - 1)),
                    self.retry_max_seconds,
                )
                event = {
                    "event": "clickhouse_retry",
                    "at_utc": datetime64_utc_text(),
                    "operation": operation,
                    "attempt": attempt,
                    "max_attempts": self.attempts,
                    "wait_seconds": round(delay, 3),
                    "error_type": type(exc).__name__,
                    "error": bounded_error_text(exc),
                    **self._diagnostic_context,
                }
                append_jsonl(self.status_path, event)
                context_text = diagnostic_context_text(self._diagnostic_context)
                print(
                    f"CLICKHOUSE RETRY | operation={operation} "
                    f"attempt={attempt}/{self.attempts} wait={delay:.1f}s "
                    f"{context_text}"
                    f"error={type(exc).__name__}: {bounded_error_text(exc)}",
                    flush=True,
                )
                time.sleep(delay)
        raise AssertionError("unreachable")

    def _reconcile_insert(self, query_id: str) -> bool:
        """Resolve a lost response before the exact INSERT is sent again."""
        deadline = time.monotonic() + min(30.0, float(self.timeout_seconds or 30.0))
        while True:
            running = int(
                ClickHouseHttpClient.execute(
                    self,
                    "SELECT count() FROM system.processes "
                    f"WHERE query_id={sql_string(query_id)} FORMAT TSV",
                ).strip()
                or 0
            )
            if not running:
                ClickHouseHttpClient.execute(self, "SYSTEM FLUSH LOGS")
                rows = ClickHouseHttpClient.execute(
                    self,
                    "SELECT toString(type) AS event_type, exception_code, exception "
                    "FROM system.query_log "
                    f"WHERE query_id={sql_string(query_id)} "
                    "AND type IN ('QueryFinish', 'ExceptionBeforeStart', 'ExceptionWhileProcessing') "
                    "ORDER BY event_time_microseconds DESC LIMIT 1 FORMAT JSONEachRow",
                ).splitlines()
                if not rows:
                    return False
                result = json.loads(rows[0])
                event_type = str(result.get("event_type") or "")
                exception_code = int(result.get("exception_code") or 0)
                if event_type == "QueryFinish" and exception_code == 0:
                    return True
                raise RuntimeError(
                    "ClickHouse recorded a failed renderer INSERT after transport loss: "
                    f"query_id={query_id} exception_code={exception_code} "
                    f"error={bounded_error_text(RuntimeError(str(result.get('exception') or 'unknown')))}"
                )
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    "ClickHouse renderer INSERT remains active after its response was lost; "
                    f"refusing an ambiguous retry query_id={query_id}"
                )
            time.sleep(0.5)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rebuild the certified Benzinga structured-rendering v2 authority and create a stratified audit."
    )
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--database", default=os.environ.get("NEWS_BENZINGA_CLICKHOUSE_DATABASE", "q_live"))
    parser.add_argument("--source-table", default="benzinga_news_normalized_v1")
    parser.add_argument("--start-date", default="")
    parser.add_argument("--end-date-exclusive", default="")
    parser.add_argument("--workers", type=int, default=max(4, min(32, os.cpu_count() or 16)))
    parser.add_argument("--insert-batch-size", type=int, default=500)
    parser.add_argument(
        "--insert-target-bytes",
        type=int,
        default=DEFAULT_INSERT_TARGET_BYTES,
        help="Soft encoded JSONEachRow body target; row count and bytes both close a batch.",
    )
    parser.add_argument(
        "--insert-max-row-bytes",
        type=int,
        default=DEFAULT_INSERT_MAX_ROW_BYTES,
        help="Hard encoded size limit for one product row; oversized rows fail with identity-only diagnostics.",
    )
    parser.add_argument("--sample-per-category", type=int, default=5)
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--path-prefix-map", action="append", default=[])
    parser.add_argument("--limit-days", type=int, default=0, help="Audit/smoke only; a limited run can never certify v2.")
    parser.add_argument("--clickhouse-attempts", type=int, default=20)
    parser.add_argument("--clickhouse-retry-base-seconds", type=float, default=2.0)
    parser.add_argument("--clickhouse-retry-max-seconds", type=float, default=30.0)
    parser.add_argument(
        "--clickhouse-timeout-seconds",
        type=float,
        default=DEFAULT_CLICKHOUSE_TIMEOUT_SECONDS,
        help="Finite socket deadline for each ClickHouse request.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    load_env_files(discover_env_files(REPO_ROOT))
    args = parse_args(argv)
    validate_operational_args(args)
    run_id = datetime.now(UTC).strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
    run_root = Path(args.output_root) / run_id
    run_root.mkdir(parents=True, exist_ok=True)
    status_path = run_root / "status.jsonl"
    errors_path = run_root / "errors.jsonl"
    client = RetryingClickHouseHttpClient(
        default_clickhouse_url(),
        default_clickhouse_user(),
        default_clickhouse_password(),
        attempts=args.clickhouse_attempts,
        retry_base_seconds=args.clickhouse_retry_base_seconds,
        retry_max_seconds=args.clickhouse_retry_max_seconds,
        request_timeout_seconds=args.clickhouse_timeout_seconds,
        status_path=status_path,
    )
    target = NewsV2TargetConfig(database=args.database, execute=args.execute, require_ready=False, skip_table_validation=True)
    started_at = datetime.now(UTC)
    path_maps = parse_path_maps(args.path_prefix_map)

    source_min, source_max, expected_rows = source_scope(client, args.database, args.source_table)
    start = date.fromisoformat(args.start_date) if args.start_date else source_min
    end = date.fromisoformat(args.end_date_exclusive) if args.end_date_exclusive else source_max + timedelta(days=1)
    days = list(iter_days(start, end))
    if args.limit_days:
        days = days[: args.limit_days]
    full_scope = not args.limit_days and start == source_min and end == source_max + timedelta(days=1)

    print(
        f"NEWS RENDER V2 | renderer={NEWS_RENDERER_VERSION} source={args.database}.{args.source_table} "
        f"days={len(days):,} rows={expected_rows:,} workers={args.workers} execute={args.execute}",
        flush=True,
    )
    print(
        "CLICKHOUSE GUARDRAILS | "
        f"timeout={args.clickhouse_timeout_seconds:g}s "
        f"attempts={args.clickhouse_attempts} "
        f"batch_rows<={args.insert_batch_size:,} "
        f"target_body<={format_bytes(args.insert_target_bytes)} "
        f"single_row<={format_bytes(args.insert_max_row_bytes)}",
        flush=True,
    )
    if args.execute:
        create_v2_tables(client, target)
        write_authority(client, target, run_id, "building", BuildCounts(), "", started_at)

    counts = BuildCounts()
    audit_samples: dict[str, list[dict[str, Any]]] = {}
    wall_start = time.perf_counter()
    for day_index, day in enumerate(days, start=1):
        expected_day = source_day_count(client, args.database, args.source_table, day)
        if args.execute and day_is_complete(
            client,
            target,
            day,
            expected_day,
            source_database=args.database,
            source_table=args.source_table,
        ):
            counts.source_rows += expected_day
            counts.event_rows += expected_day
            counts.rendered_rows += expected_day
            append_jsonl(status_path, {"day": day.isoformat(), "status": "skipped_complete", "rows": expected_day})
            continue
        rows = load_source_day(client, args.database, args.source_table, day)
        rendered_rows: list[dict[str, Any]] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
            futures = [executor.submit(render_one, row, path_maps) for row in rows]
            for row, future in zip(rows, futures):
                try:
                    built, raw_html = future.result()
                    rendered_rows.append(built)
                    collect_samples(audit_samples, row, built, raw_html, args.sample_per_category)
                    if "provider_raw_artifact_unavailable" in built["event"]["content_quality_flags"]:
                        counts.raw_artifacts_missing += 1
                except Exception as exc:  # noqa: BLE001
                    counts.failures += 1
                    append_jsonl(
                        errors_path,
                        {
                            "day": day.isoformat(),
                            "canonical_news_id": row.get("canonical_news_id", ""),
                            "provider_article_id": row.get("provider_article_id", ""),
                            "error": repr(exc),
                        },
                    )
        if len(rendered_rows) != len(rows):
            raise RuntimeError(f"{day}: rendered {len(rendered_rows):,}/{len(rows):,}; see {errors_path}")
        insert_built_rows(
            client,
            target,
            rendered_rows,
            max_rows=args.insert_batch_size,
            target_bytes=args.insert_target_bytes,
            max_row_bytes=args.insert_max_row_bytes,
            day=day,
            status_path=status_path,
            execute=args.execute,
        )
        counts.source_rows += len(rows)
        counts.event_rows += len(rendered_rows)
        counts.rendered_rows += len(rendered_rows)
        counts.source_parts += sum(len(item["sources"]) for item in rendered_rows)
        counts.block_rows += sum(len(item["blocks"]) for item in rendered_rows)
        counts.ticker_rows += sum(len(item["tickers"]) for item in rendered_rows)
        elapsed = time.perf_counter() - wall_start
        eta = elapsed / day_index * (len(days) - day_index) if day_index else 0.0
        append_jsonl(
            status_path,
            {
                "day": day.isoformat(),
                "status": "completed" if args.execute else "dry_run",
                "rows": len(rows),
                "elapsed_seconds": round(elapsed, 3),
            },
        )
        print(
            f"[{day_index:,}/{len(days):,}] {day} rows={len(rows):,} total={counts.event_rows:,} "
            f"elapsed={elapsed / 60:.1f}m eta={eta / 60:.1f}m",
            flush=True,
        )

    audit = audit_build(client, args, target, counts, full_scope=full_scope)
    report_path = write_audit(run_root, audit, audit_samples)
    if args.execute:
        status = "ready" if full_scope and not audit["errors"] else "audit_failed"
        write_authority(client, target, run_id, status, counts, str(report_path), started_at, len(audit["errors"]))
        if status != "ready":
            raise RuntimeError(f"V2 was not certified: {audit['errors']}; report={report_path}")
    print(f"COMPLETED | status={'ready' if args.execute and full_scope else 'not_certified'} report={report_path}", flush=True)
    return 0


def render_one(row: dict[str, Any], path_maps: list[tuple[str, str]]) -> tuple[dict[str, Any], str]:
    row = dict(row)
    row["pdf_artifact_paths"] = [
        str(resolved)
        for value in row.get("pdf_artifact_paths") or []
        if (resolved := resolve_path(str(value), path_maps)) is not None
    ]
    raw_html = ""
    payload: dict[str, Any] = {}
    raw_path = resolve_path(str(row.get("raw_artifact_path") or ""), path_maps)
    if raw_path and raw_path.exists():
        payload = json.loads(raw_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"raw payload is not an object: {raw_path}")
        raw_html = str(payload.get("body") or "")
    else:
        payload = {
            "id": row.get("provider_article_id"),
            "title": row.get("title"),
            "teaser": row.get("teaser"),
            "url": row.get("article_url"),
            "tickers": row.get("tickers") or [],
        }
    rendered = render_news_article(payload, normalized_row=row)
    return build_v2_rows(payload, row, rendered), raw_html


def load_source_day(
    client: ClickHouseHttpClient, database: str, table: str, day: date
) -> list[dict[str, Any]]:
    columns = [name for name in NORMALIZED_COLUMNS if name not in {"normalized_full_text", "text_hash"}]
    projection = ", ".join(quote_ident(name) for name in columns)
    next_day = day + timedelta(days=1)
    sql = f"""
SELECT {projection}
FROM {quote_ident(database)}.{quote_ident(table)} FINAL
WHERE published_at_utc >= toDateTime64('{day.isoformat()} 00:00:00', 9, 'UTC')
  AND published_at_utc < toDateTime64('{next_day.isoformat()} 00:00:00', 9, 'UTC')
ORDER BY published_at_utc, provider_article_id
FORMAT JSONEachRow
"""
    return [json.loads(line) for line in client.execute(sql).splitlines() if line.strip()]


def insert_built_rows(
    client: RetryingClickHouseHttpClient,
    target: NewsV2TargetConfig,
    rows: list[dict[str, Any]],
    *,
    max_rows: int,
    target_bytes: int,
    max_row_bytes: int,
    day: date,
    status_path: Path,
    execute: bool,
) -> None:
    tables = [
        (target.event_table, EVENT_COLUMNS, "event"),
        (target.source_table, SOURCE_COLUMNS, "sources"),
        (target.block_table, BLOCK_COLUMNS, "blocks"),
        (target.rendered_table, RENDERED_COLUMNS, "rendered"),
        (target.ticker_table, TICKER_COLUMNS, "tickers"),
    ]
    planned: list[tuple[str, list[str], list[Any]]] = []
    try:
        for table, columns, key in tables:
            flat: list[dict[str, Any]] = []
            for item in rows:
                value = item[key]
                flat.extend(value if isinstance(value, list) else [value])
            batches = list(
                json_each_row_batches(
                    flat,
                    table=table,
                    max_rows=max_rows,
                    target_bytes=target_bytes,
                    max_row_bytes=max_row_bytes,
                )
            )
            planned.append((table, columns, batches))
    except OversizedNewsRowError as exc:
        event = {
            "event": "insert_validation_failed",
            "day": day.isoformat(),
            "error_type": type(exc).__name__,
            "error": bounded_error_text(exc),
        }
        append_jsonl(status_path, event)
        print(
            f"INSERT VALIDATION FAILED | day={day} "
            f"error={bounded_error_text(exc)}",
            flush=True,
        )
        raise

    # Validate every product before inserting the first table. A deterministic
    # data-contract failure must not create a newly partial day.
    for table, columns, batches in planned:
        if not execute:
            append_jsonl(
                status_path,
                {
                    "event": "insert_plan_validated",
                    "day": day.isoformat(),
                    "table": table,
                    "batches": len(batches),
                    "rows": sum(len(batch.rows) for batch in batches),
                    "body_bytes": sum(batch.body_bytes for batch in batches),
                    "max_row_bytes": max(
                        (batch.max_row_bytes for batch in batches),
                        default=0,
                    ),
                },
            )
            continue
        for batch_index, batch in enumerate(batches, start=1):
            query_id = v2_batch_query_id(table, batch_index, batch.rows)
            context = {
                "day": day.isoformat(),
                "table": table,
                "batch": batch_index,
                "batch_count": len(batches),
                "rows": len(batch.rows),
                "body_bytes": batch.body_bytes,
                "max_row_bytes": batch.max_row_bytes,
                "query_id": query_id,
            }
            append_jsonl(
                status_path,
                {
                    "event": "insert_batch_started",
                    "at_utc": datetime64_utc_text(),
                    **context,
                },
            )
            with client.diagnostic_context(**context):
                insert_json_each_row(
                    client,
                    target.database,
                    table,
                    columns,
                    batch.rows,
                    query_id=query_id,
                )
            append_jsonl(
                status_path,
                {
                    "event": "insert_batch_completed",
                    "at_utc": datetime64_utc_text(),
                    **context,
                },
            )
def validate_operational_args(args: argparse.Namespace) -> None:
    positive = {
        "--workers": args.workers,
        "--insert-batch-size": args.insert_batch_size,
        "--insert-target-bytes": args.insert_target_bytes,
        "--insert-max-row-bytes": args.insert_max_row_bytes,
        "--clickhouse-attempts": args.clickhouse_attempts,
        "--clickhouse-timeout-seconds": args.clickhouse_timeout_seconds,
    }
    invalid = [name for name, value in positive.items() if float(value) <= 0]
    if invalid:
        raise SystemExit(f"{', '.join(invalid)} must be greater than zero")


def diagnostic_context_text(values: dict[str, Any]) -> str:
    if not values:
        return ""
    ordered = (
        "day",
        "table",
        "batch",
        "batch_count",
        "rows",
        "body_bytes",
        "max_row_bytes",
        "query_id",
    )
    return " ".join(f"{key}={values[key]}" for key in ordered if key in values) + " "


def format_bytes(value: int) -> str:
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024 or unit == "GiB":
            return f"{size:.0f}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024
    raise AssertionError("unreachable")


def audit_build(
    client: ClickHouseHttpClient,
    args: argparse.Namespace,
    target: NewsV2TargetConfig,
    counts: BuildCounts,
    *,
    full_scope: bool,
) -> dict[str, Any]:
    errors: list[str] = []
    checks: dict[str, Any] = {"processed": asdict(counts), "full_scope": full_scope}
    if counts.failures:
        errors.append(f"render_failures={counts.failures}")
    if args.execute and full_scope:
        source_rows = scalar(client, f"SELECT count() FROM {quote_ident(args.database)}.{quote_ident(args.source_table)} FINAL")
        event_rows = scalar(
            client,
            f"SELECT count() FROM {quote_ident(target.database)}.{quote_ident(target.event_table)} FINAL "
            f"WHERE renderer_version='{NEWS_RENDERER_VERSION}'",
        )
        rendered_rows = scalar(
            client,
            f"SELECT count() FROM {quote_ident(target.database)}.{quote_ident(target.rendered_table)} FINAL "
            f"WHERE renderer_version='{NEWS_RENDERER_VERSION}'",
        )
        empty_rendered = scalar(
            client,
            f"SELECT count() FROM {quote_ident(target.database)}.{quote_ident(target.rendered_table)} FINAL "
            f"WHERE renderer_version='{NEWS_RENDERER_VERSION}' AND empty(rendered_text)",
        )
        orphan_rendered = scalar(
            client,
            f"SELECT count() FROM {quote_ident(target.database)}.{quote_ident(target.rendered_table)} AS r FINAL "
            f"LEFT ANTI JOIN {quote_ident(target.database)}.{quote_ident(target.event_table)} AS e FINAL "
            "USING (published_date, provider_article_id)",
        )
        source_parts = scalar(
            client,
            f"SELECT count() FROM {quote_ident(target.database)}.{quote_ident(target.source_table)} AS s FINAL "
            f"INNER JOIN {quote_ident(target.database)}.{quote_ident(target.event_table)} AS e FINAL "
            "ON e.published_date=s.published_date AND e.canonical_news_id=s.canonical_news_id "
            "AND e.source_revision_key=s.source_revision_key "
            f"WHERE s.renderer_version='{NEWS_RENDERER_VERSION}'",
        )
        block_rows = scalar(
            client,
            f"SELECT count() FROM {quote_ident(target.database)}.{quote_ident(target.block_table)} AS b FINAL "
            f"INNER JOIN {quote_ident(target.database)}.{quote_ident(target.event_table)} AS e FINAL "
            "ON e.published_date=b.published_date AND e.canonical_news_id=b.canonical_news_id "
            "AND e.source_revision_key=b.source_revision_key "
            f"WHERE b.renderer_version='{NEWS_RENDERER_VERSION}'",
        )
        ticker_rows = scalar(
            client,
            f"SELECT count() FROM {quote_ident(target.database)}.{quote_ident(target.ticker_table)} FINAL "
            f"WHERE renderer_version='{NEWS_RENDERER_VERSION}'",
        )
        expected_ticker_rows = scalar(
            client,
            f"SELECT sum(length(tickers)) FROM {quote_ident(args.database)}.{quote_ident(args.source_table)} FINAL",
        )
        current_ticker_rows = scalar(
            client,
            f"SELECT count() FROM {quote_ident(target.database)}.{quote_ident(target.ticker_table)} AS t FINAL "
            f"INNER JOIN {quote_ident(target.database)}.{quote_ident(target.event_table)} AS e FINAL "
            "ON e.published_date=t.published_date AND e.provider_article_id=t.provider_article_id "
            "AND e.source_revision_key=t.source_revision_key "
            f"WHERE t.renderer_version='{NEWS_RENDERER_VERSION}'",
        )
        source_block_total = scalar(
            client,
            f"SELECT sum(s.block_count) FROM {quote_ident(target.database)}.{quote_ident(target.source_table)} AS s FINAL "
            f"INNER JOIN {quote_ident(target.database)}.{quote_ident(target.event_table)} AS e FINAL "
            "ON e.published_date=s.published_date AND e.canonical_news_id=s.canonical_news_id "
            "AND e.source_revision_key=s.source_revision_key "
            f"WHERE s.renderer_version='{NEWS_RENDERER_VERSION}'",
        )
        counts.source_rows = source_rows
        counts.event_rows = event_rows
        counts.rendered_rows = rendered_rows
        counts.source_parts = source_parts
        counts.block_rows = block_rows
        counts.ticker_rows = current_ticker_rows
        checks.update(
            source_rows=source_rows,
            event_rows=event_rows,
            rendered_rows=rendered_rows,
            source_parts=source_parts,
            block_rows=block_rows,
            ticker_rows=ticker_rows,
            expected_ticker_rows=expected_ticker_rows,
            current_ticker_rows=current_ticker_rows,
            source_block_total=source_block_total,
            empty_rendered=empty_rendered,
            orphan_rendered=orphan_rendered,
        )
        if not (source_rows == event_rows == rendered_rows):
            errors.append(f"cardinality_mismatch source={source_rows} event={event_rows} rendered={rendered_rows}")
        if empty_rendered:
            errors.append(f"empty_rendered={empty_rendered}")
        if orphan_rendered:
            errors.append(f"orphan_rendered={orphan_rendered}")
        if expected_ticker_rows != current_ticker_rows:
            errors.append(
                f"ticker_cardinality_mismatch expected={expected_ticker_rows} current={current_ticker_rows}"
            )
        if source_block_total != block_rows:
            errors.append(f"block_cardinality_mismatch source_sum={source_block_total} blocks={block_rows}")
    checks["errors"] = errors
    return checks


def collect_samples(
    samples: dict[str, list[dict[str, Any]]],
    source: dict[str, Any],
    built: dict[str, Any],
    original_html: str,
    limit: int,
) -> None:
    block_kinds = {row["block_kind"] for row in built["blocks"]}
    flags = set(built["event"]["content_quality_flags"])
    categories = ["ordinary"]
    if "table_row" in block_kinds:
        categories.append("table")
    if block_kinds & {"list_item", "ordered_list_item"}:
        categories.append("list")
    if "image" in block_kinds:
        categories.append("image")
    if source.get("is_title_only"):
        categories.append("title_only")
    if source.get("has_external_text"):
        categories.append("external")
    if source.get("has_pdf"):
        categories.append("pdf")
    if len(source.get("tickers") or []) > 1:
        categories.append("multi_ticker")
    if "provider_raw_artifact_unavailable" in flags:
        categories.append("raw_missing")
    for category in categories:
        bucket = samples.setdefault(category, [])
        if len(bucket) >= limit:
            continue
        # Stable hash sampling prevents the first rows of each day dominating.
        score = int(hashlib.sha256(str(source["canonical_news_id"]).encode()).hexdigest()[:8], 16)
        if category != "ordinary" or score % 17 == 0:
            bucket.append({"source": source, "built": built, "original_html": original_html})


def write_audit(run_root: Path, audit: dict[str, Any], samples: dict[str, list[dict[str, Any]]]) -> Path:
    sample_root = run_root / "audit_samples"
    sample_root.mkdir(parents=True, exist_ok=True)
    index = ["# Benzinga structured renderer v2 audit", "", "## Integrity", "", "```json", json.dumps(audit, indent=2), "```", ""]
    for category in sorted(samples):
        category_root = sample_root / category
        category_root.mkdir(parents=True, exist_ok=True)
        index.extend([f"## {category}", ""])
        for sample in samples[category]:
            source = sample["source"]
            filename = f"{source['canonical_news_id']}.md"
            path = category_root / filename
            path.write_text(sample_markdown(category, sample), encoding="utf-8")
            title = sample["built"]["event"].get("title") or source["canonical_news_id"]
            index.append(f"- [{title}](audit_samples/{category}/{filename})")
        index.append("")
    report = run_root / "AUDIT.md"
    report.write_text("\n".join(index), encoding="utf-8")
    (run_root / "audit.json").write_text(json.dumps(audit, indent=2), encoding="utf-8")
    return report


def sample_markdown(category: str, sample: dict[str, Any]) -> str:
    source, built = sample["source"], sample["built"]
    original = sample["original_html"] or "(raw source artifact unavailable)"
    rendered = built["rendered"]["rendered_text"]
    return "\n".join(
        [
            f"# {built['event'].get('title') or source['canonical_news_id']}",
            "",
            f"- Category: `{category}`",
            f"- Canonical news ID: `{source['canonical_news_id']}`",
            f"- Published UTC: `{source['published_at_utc']}`",
            f"- Tickers: `{', '.join(source.get('tickers') or [])}`",
            f"- Renderer: `{NEWS_RENDERER_VERSION}`",
            f"- Quality flags: `{', '.join(built['rendered']['quality_flags']) or 'none'}`",
            "",
            "## Original provider HTML",
            "",
            "````html",
            original.replace("````", "&#96;&#96;&#96;&#96;"),
            "````",
            "",
            "## Normalized structured text",
            "",
            "````text",
            rendered.replace("````", "&#96;&#96;&#96;&#96;"),
            "````",
            "",
        ]
    )


def write_authority(
    client: ClickHouseHttpClient,
    target: NewsV2TargetConfig,
    run_id: str,
    status: str,
    counts: BuildCounts,
    report_path: str,
    started_at: datetime,
    audit_errors: int = 0,
) -> None:
    now = datetime64_utc_text()
    row = {
        "authority_version": NEWS_RENDERER_VERSION,
        "run_id": run_id,
        "status": status,
        **asdict(counts),
        "audit_errors": audit_errors,
        "audit_report_path": report_path,
        "started_at_utc": datetime64_utc_text(started_at),
        "updated_at_utc": now,
    }
    row.pop("raw_artifacts_missing")
    row.pop("failures")
    insert_json_each_row(
        client,
        target.database,
        target.authority_table,
        list(row),
        [row],
        query_id=v2_batch_query_id(target.authority_table, 1, [row]),
    )


def source_scope(client: ClickHouseHttpClient, database: str, table: str) -> tuple[date, date, int]:
    sql = (
        f"SELECT toString(min(published_date)), toString(max(published_date)), count() "
        f"FROM {quote_ident(database)}.{quote_ident(table)} FINAL FORMAT TSV"
    )
    first, last, count = client.execute(sql).strip().split("\t")
    if not first or not last:
        raise RuntimeError(f"source table is empty: {database}.{table}")
    return date.fromisoformat(first), date.fromisoformat(last), int(count)


def source_day_count(client: ClickHouseHttpClient, database: str, table: str, day: date) -> int:
    return scalar(
        client,
        f"SELECT count() FROM {quote_ident(database)}.{quote_ident(table)} FINAL "
        f"WHERE published_date=toDate('{day.isoformat()}')",
    )


def day_is_complete(
    client: ClickHouseHttpClient,
    target: NewsV2TargetConfig,
    day: date,
    expected: int,
    *,
    source_database: str,
    source_table: str,
) -> bool:
    if expected == 0:
        return True
    day_sql = f"toDate('{day.isoformat()}')"
    version_sql = NEWS_RENDERER_VERSION.replace("'", "\\'")
    sql = f"""
SELECT
 (SELECT count() FROM {quote_ident(target.database)}.{quote_ident(target.event_table)} FINAL
  WHERE published_date={day_sql} AND renderer_version='{version_sql}') AS event_rows,
 (SELECT count() FROM {quote_ident(target.database)}.{quote_ident(target.rendered_table)} FINAL
  WHERE published_date={day_sql} AND renderer_version='{version_sql}') AS rendered_rows,
 (SELECT count() FROM {quote_ident(target.database)}.{quote_ident(target.rendered_table)} FINAL
  WHERE published_date={day_sql} AND renderer_version='{version_sql}' AND empty(rendered_text)) AS empty_rendered,
 (SELECT sum(length(tickers)) FROM {quote_ident(source_database)}.{quote_ident(source_table)} FINAL
  WHERE published_date={day_sql}) AS expected_tickers,
 (SELECT count()
  FROM {quote_ident(target.database)}.{quote_ident(target.ticker_table)} AS t FINAL
  INNER JOIN {quote_ident(target.database)}.{quote_ident(target.event_table)} AS e FINAL
   ON e.published_date=t.published_date AND e.provider_article_id=t.provider_article_id
   AND e.source_revision_key=t.source_revision_key
  WHERE e.published_date={day_sql} AND e.renderer_version='{version_sql}'
    AND t.renderer_version='{version_sql}') AS current_tickers,
 (SELECT sum(source_count) FROM {quote_ident(target.database)}.{quote_ident(target.rendered_table)} FINAL
  WHERE published_date={day_sql} AND renderer_version='{version_sql}') AS expected_sources,
 (SELECT count()
  FROM {quote_ident(target.database)}.{quote_ident(target.source_table)} AS s FINAL
  INNER JOIN {quote_ident(target.database)}.{quote_ident(target.event_table)} AS e FINAL
   ON e.published_date=s.published_date AND e.canonical_news_id=s.canonical_news_id
   AND e.source_revision_key=s.source_revision_key
  WHERE e.published_date={day_sql} AND e.renderer_version='{version_sql}') AS current_sources,
 (SELECT sum(s.block_count)
  FROM {quote_ident(target.database)}.{quote_ident(target.source_table)} AS s FINAL
  INNER JOIN {quote_ident(target.database)}.{quote_ident(target.event_table)} AS e FINAL
   ON e.published_date=s.published_date AND e.canonical_news_id=s.canonical_news_id
   AND e.source_revision_key=s.source_revision_key
  WHERE e.published_date={day_sql} AND e.renderer_version='{version_sql}') AS expected_blocks,
 (SELECT count()
  FROM {quote_ident(target.database)}.{quote_ident(target.block_table)} AS b FINAL
  INNER JOIN {quote_ident(target.database)}.{quote_ident(target.event_table)} AS e FINAL
   ON e.published_date=b.published_date AND e.canonical_news_id=b.canonical_news_id
   AND e.source_revision_key=b.source_revision_key
  WHERE e.published_date={day_sql} AND e.renderer_version='{version_sql}') AS current_blocks
FORMAT TSV
"""
    values = [int(value or 0) for value in client.execute(sql).strip().split("\t")]
    if len(values) != 9:
        raise RuntimeError(f"unexpected daily completion audit result for {day}: {values}")
    event_rows, rendered_rows, empty_rendered, expected_tickers, current_tickers, expected_sources, current_sources, expected_blocks, current_blocks = values
    return (
        event_rows == expected
        and rendered_rows == expected
        and empty_rendered == 0
        and current_tickers == expected_tickers
        and current_sources == expected_sources
        and current_blocks == expected_blocks
    )


def scalar(client: ClickHouseHttpClient, sql: str) -> int:
    return int(client.execute(sql + "\nFORMAT TSV").strip() or 0)


def parse_path_maps(values: Iterable[str]) -> list[tuple[str, str]]:
    maps = [DEFAULT_PATH_MAP]
    for value in values:
        if "=" not in value:
            raise ValueError(f"invalid --path-prefix-map {value!r}; expected FROM=TO")
        maps.append(tuple(value.split("=", 1)))
    return maps


def resolve_path(value: str, mappings: list[tuple[str, str]]) -> Path | None:
    if not value:
        return None
    direct = Path(value)
    if path_is_accessible(direct):
        return direct
    lowered = value.lower().replace("/", "\\")
    for source, target in mappings:
        source_key = source.lower().replace("/", "\\").rstrip("\\")
        if lowered == source_key or lowered.startswith(source_key + "\\"):
            suffix = value[len(source) :].lstrip("\\/")
            candidate = Path(target) / Path(suffix.replace("\\", "/"))
            if path_is_accessible(candidate):
                return candidate
    return None


def path_is_accessible(path: Path) -> bool:
    try:
        return path.exists()
    except OSError:
        return False


def iter_days(start: date, end: date) -> Iterable[date]:
    current = start
    while current < end:
        yield current
        current += timedelta(days=1)


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def is_transient_clickhouse_error(exc: BaseException) -> bool:
    pending: list[BaseException] = [exc]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, url_error.HTTPError):
            if int(current.code) in TRANSIENT_HTTP_STATUS_CODES:
                return True
        elif int(getattr(current, "status_code", 0) or 0) in TRANSIENT_HTTP_STATUS_CODES:
            return True
        elif isinstance(current, (url_error.URLError, TimeoutError, ConnectionError)):
            return True
        elif isinstance(current, OSError):
            if getattr(current, "winerror", None) in TRANSIENT_WINDOWS_SOCKET_ERRORS:
                return True
        for nested in (
            getattr(current, "reason", None),
            getattr(current, "__cause__", None),
            getattr(current, "__context__", None),
        ):
            if isinstance(nested, BaseException):
                pending.append(nested)
    return False


def clickhouse_operation(sql: str) -> str:
    normalized = " ".join(sql.lstrip().split())
    if not normalized:
        return "empty"
    words = normalized.split(" ", 4)
    verb = words[0].upper()
    if verb == "INSERT" and len(words) >= 3:
        target = words[2].replace("`", "")
        return f"insert:{target[:96]}"
    if verb == "CREATE":
        return "create_table"
    if verb in {"SELECT", "WITH"}:
        return "read"
    return verb.lower()[:32]


def bounded_error_text(exc: BaseException, limit: int = 320) -> str:
    text = " ".join(str(exc).split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


if __name__ == "__main__":
    raise SystemExit(main())
