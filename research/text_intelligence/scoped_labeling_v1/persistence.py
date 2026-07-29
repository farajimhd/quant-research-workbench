from __future__ import annotations

import argparse
import datetime as dt
import json
import multiprocessing
import os
import queue
import signal
import time
import uuid
from dataclasses import dataclass, field
from multiprocessing.pool import Pool
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

from research.mlops.clickhouse import (
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
    quote_ident,
    sql_string,
)
from research.mlops.env import discover_env_files, load_env_files
from research.mlops.paths import MLOpsPathConfig
from research.text_intelligence.candidate_inventory_v1.config import (
    CandidateInventoryConfig,
)
from research.text_intelligence.semantic_label_authority_v1.schema import (
    SemanticDocument,
)

from .pipeline import classify_news_document, classify_sec_document
from .news_identity import NewsIssuerResolver, load_news_issuer_resolver
from .schema import SCOPED_LABELING_VERSION, ScopedLabel


TARGET_TABLE = "scoped_text_labels_v4"
STATUS_TABLE = "scoped_text_labels_v4_build_status"
RELATION_TABLE = "scoped_content_relations_v2"
DEFAULT_INSERT_BYTES = 8 * 1024 * 1024
DEFAULT_HEARTBEAT_SECONDS = 30.0
DEFAULT_TRANSIENT_RETRIES = 6
DEFAULT_RETRY_BASE_SECONDS = 2.0
MAX_WORKERS = 64

_WORKER_DATABASE = "q_live"
_WORKER_STOP_EVENT: Any = None
_WORKER_PROGRESS_QUEUE: Any = None
_WORKER_ISSUER_RESOLVER: NewsIssuerResolver | None = None


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    certification_manifest = (
        MLOpsPathConfig.from_env().runtimes_root
        / "text_intelligence"
        / "scoped_labeling_v4"
        / "certification"
        / "manifest.json"
    )
    parser = argparse.ArgumentParser(
        description=(
            "Build versioned scoped News/SEC labels. Defaults to a read-only "
            "plan; --execute is required to create or insert anything."
        )
    )
    parser.add_argument("--start-date", default="2010-01-01")
    parser.add_argument(
        "--end-date-exclusive",
        default=(dt.date.today() + dt.timedelta(days=1)).isoformat(),
    )
    parser.add_argument(
        "--corpus", choices=("news", "sec", "both"), default="both"
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=8,
        help=(
            "CPU worker processes (1-64). Higher is not always faster; "
            "ClickHouse transport retries remain bounded per weekly unit."
        ),
    )
    parser.add_argument(
        "--period-days",
        type=int,
        default=7,
        help="Bounded source window owned by one worker (1-31 days).",
    )
    parser.add_argument("--database", default="q_live")
    parser.add_argument(
        "--insert-megabytes",
        type=int,
        default=8,
        help="Maximum serialized payload per label/relation insert (1-64 MiB).",
    )
    parser.add_argument(
        "--heartbeat-seconds",
        type=float,
        default=30.0,
        help="Durable in-progress status cadence (5-300 seconds).",
    )
    parser.add_argument(
        "--transient-retries",
        type=int,
        default=DEFAULT_TRANSIENT_RETRIES,
        help=(
            "Fresh-connection retries for a bounded unit after transient "
            "ClickHouse transport failures (0-20)."
        ),
    )
    parser.add_argument(
        "--retry-base-seconds",
        type=float,
        default=DEFAULT_RETRY_BASE_SECONDS,
        help="Initial retry delay; each repeated failure doubles it (0-300).",
    )
    parser.add_argument(
        "--certification-manifest",
        type=Path,
        default=certification_manifest,
    )
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--rebuild-completed", action="store_true")
    return parser.parse_args(list(argv) if argv is not None else None)


def run(args: argparse.Namespace) -> dict:
    _validate_dates(args.start_date, args.end_date_exclusive)
    if not 1 <= args.workers <= MAX_WORKERS:
        raise ValueError(f"--workers must be between 1 and {MAX_WORKERS}")
    if not 1 <= args.period_days <= 31:
        raise ValueError("--period-days must be between 1 and 31")
    if not 1 <= args.insert_megabytes <= 64:
        raise ValueError("--insert-megabytes must be between 1 and 64")
    if not 5 <= args.heartbeat_seconds <= 300:
        raise ValueError("--heartbeat-seconds must be between 5 and 300")
    if not 0 <= args.transient_retries <= 20:
        raise ValueError("--transient-retries must be between 0 and 20")
    if not 0 <= args.retry_base_seconds <= 300:
        raise ValueError("--retry-base-seconds must be between 0 and 300")
    load_env_files(discover_env_files(Path.cwd()), verbose=True)
    corpora = ("news", "sec") if args.corpus == "both" else (args.corpus,)
    periods = bounded_period_ranges(
        args.start_date,
        args.end_date_exclusive,
        args.period_days,
    )
    plan = interleaved_plan(corpora, periods)
    client = make_client()
    try:
        counts = source_counts(client, args.database, plan)
        print(
            f"SCOPED LABELING PLAN | version={SCOPED_LABELING_VERSION} "
            f"units={len(plan):,} workers={args.workers} execute={args.execute}",
            flush=True,
        )
        for corpus in corpora:
            print(
                f"  {corpus}: source rows={sum(value for (kind, _, _), value in counts.items() if kind == corpus):,}",
                flush=True,
            )
        if not args.execute:
            print("PLAN ONLY | no tables created and no rows written", flush=True)
            return {"execute": False, "source_counts": counts}
        assert_certification(args.certification_manifest)
        create_tables(client, args.database)
        completed = (
            set()
            if args.rebuild_completed
            else completed_units(client, args.database)
        )
    finally:
        client.close()

    run_id = uuid.uuid4().hex
    active = [
        item for item in plan
        if (item[0], item[1], item[2], SCOPED_LABELING_VERSION)
        not in completed
    ]
    completed_before = len(plan) - len(active)
    print(
        f"BACKFILL START | durable={completed_before:,}/{len(plan):,} "
        f"remaining={len(active):,} processes={args.workers} "
        f"insert_limit={args.insert_megabytes}MiB",
        flush=True,
    )
    if not active:
        print("BACKFILL COMPLETE | every planned unit is durable", flush=True)
        return {
            "execute": True,
            "run_id": run_id,
            "completed_units": 0,
            "label_rows": 0,
            "relation_rows": 0,
        }

    results: list[dict] = []
    started_at = time.perf_counter()
    context = multiprocessing.get_context("spawn")
    stop_event = context.Event()
    progress_queue = context.Queue()
    pool = context.Pool(
        processes=args.workers,
        initializer=initialize_worker,
        initargs=(args.database, stop_event, progress_queue),
        # Recycle long-lived spawned workers before Python/regex/HTTP allocator
        # fragmentation becomes material during a multi-million-document run.
        maxtasksperchild=32,
    )
    try:
        results = execute_bounded_plan(
            pool,
            active,
            run_id=run_id,
            insert_bytes=args.insert_megabytes * 1024 * 1024,
            heartbeat_seconds=args.heartbeat_seconds,
            worker_count=args.workers,
            stop_event=stop_event,
            progress_queue=progress_queue,
            completed_before=completed_before,
            total_units=len(plan),
            started_at=started_at,
            transient_retries=args.transient_retries,
            retry_base_seconds=args.retry_base_seconds,
        )
    except KeyboardInterrupt:
        stop_event.set()
        print(
            "\nINTERRUPT | stopping workers at row boundaries; completed "
            "periods remain durable and partial periods will replay safely",
            flush=True,
        )
        pool.terminate()
        pool.join()
        raise
    except Exception:
        stop_event.set()
        print(
            "BACKFILL FAILED | stopping other active workers; the failed "
            "period remains resumable",
            flush=True,
        )
        pool.terminate()
        pool.join()
        raise
    else:
        pool.close()
        pool.join()
    finally:
        progress_queue.close()

    elapsed = time.perf_counter() - started_at
    print(
        f"BACKFILL COMPLETE | this_run={len(results):,} "
        f"durable={completed_before + len(results):,}/{len(plan):,} "
        f"elapsed={format_duration(elapsed)}",
        flush=True,
    )
    return {
        "execute": True,
        "run_id": run_id,
        "completed_units": len(results),
        "label_rows": sum(row["label_rows"] for row in results),
        "relation_rows": sum(row["relation_rows"] for row in results),
    }


def initialize_worker(
    database: str,
    stop_event: Any,
    progress_queue: Any = None,
) -> None:
    """Initialize one isolated CPU worker without copying source documents."""
    global _WORKER_DATABASE, _WORKER_STOP_EVENT
    global _WORKER_PROGRESS_QUEUE, _WORKER_ISSUER_RESOLVER
    _WORKER_DATABASE = database
    _WORKER_STOP_EVENT = stop_event
    _WORKER_PROGRESS_QUEUE = progress_queue
    _WORKER_ISSUER_RESOLVER = None
    signal.signal(signal.SIGINT, signal.SIG_IGN)


def execute_bounded_plan(
    pool: Pool,
    active: Sequence[tuple[str, str, str]],
    *,
    run_id: str,
    insert_bytes: int,
    heartbeat_seconds: float,
    worker_count: int,
    stop_event: Any,
    progress_queue: Any,
    completed_before: int,
    total_units: int,
    started_at: float,
    transient_retries: int = DEFAULT_TRANSIENT_RETRIES,
    retry_base_seconds: float = DEFAULT_RETRY_BASE_SECONDS,
) -> list[dict]:
    """Execute bounded units with fresh-client retries for transient I/O.

    A unit is the durability boundary. Its label and relationship writes use
    ReplacingMergeTree identities, so replaying the complete unit after an
    ambiguous or partial transport failure is safe. Completed units advance
    progress exactly once; retries never inflate durable coverage.
    """
    pending_items = [
        (identity, 0, 0.0) for identity in active
    ]
    jobs: list[tuple[Any, tuple[str, str, str], int]] = []
    results: list[dict] = []
    max_in_flight = max(1, worker_count)
    active_progress: dict[tuple[str, str], dict] = {}
    last_active_print = started_at
    retry_count = 0

    def submit_available() -> None:
        if not pending_items or len(jobs) >= max_in_flight:
            return
        while pending_items and len(jobs) < max_in_flight:
            now = time.monotonic()
            available = [
                (index, item)
                for index, item in enumerate(pending_items)
                if item[2] <= now
            ]
            if not available:
                break
            # A failed unit is replayed before untouched work once its backoff
            # expires, preventing one early transport failure from remaining
            # non-durable until the end of a multi-year plan.
            selected_index, selected = max(
                available,
                key=lambda value: (value[1][1], -value[0]),
            )
            identity, attempt, _ready_at = selected
            pending_items.pop(selected_index)
            corpus, start, end = identity
            result = pool.apply_async(
                process_unit,
                (
                    corpus,
                    start,
                    end,
                    run_id,
                    insert_bytes,
                    heartbeat_seconds,
                    attempt,
                    transient_retries,
                ),
            )
            jobs.append((result, identity, attempt))

    submit_available()
    while jobs or pending_items:
        ready = [item for item in jobs if item[0].ready()]
        drain_progress_queue(progress_queue, active_progress)
        now = time.perf_counter()
        if now - last_active_print >= 30.0 and active_progress:
            print_active_progress(
                active_progress,
                durable=completed_before + len(results),
                total=total_units,
                elapsed=now - started_at,
            )
            last_active_print = now
        for async_result, identity, attempt in ready:
            jobs.remove((async_result, identity, attempt))
            corpus, start, _ = identity
            try:
                result = async_result.get()
            except Exception as exc:
                if (
                    attempt >= transient_retries
                    or not is_transient_clickhouse_error(exc)
                ):
                    raise
                retry_count += 1
                delay = retry_base_seconds * float(2**attempt)
                next_attempt = attempt + 1
                pending_items.append(
                    (identity, next_attempt, time.monotonic() + delay)
                )
                active_progress[(corpus, start)] = {
                    "corpus": corpus,
                    "start": start,
                    "stage": "retry",
                    "source_rows": 0,
                    "label_rows": 0,
                    "relation_rows": 0,
                    "attempt": next_attempt,
                }
                print(
                    f"RETRY {corpus.upper():4} {start} | "
                    f"attempt={next_attempt}/{transient_retries} "
                    f"in={format_retry_delay(delay)} | "
                    f"reason={safe_error_summary(exc)}",
                    flush=True,
                )
                continue
            results.append(result)
            active_progress.pop((corpus, start), None)
            elapsed = time.perf_counter() - started_at
            durable = completed_before + len(results)
            run_rate = len(results) / elapsed if elapsed > 0 else 0.0
            eta_seconds = (
                (total_units - durable) / run_rate
                if run_rate > 0
                else 0.0
            )
            print(
                f"[{durable:,}/{total_units:,}] "
                f"{corpus.upper():4} {start} COMPLETE | "
                f"src={result['source_rows']:,} "
                f"labels={result['label_rows']:,} "
                f"relations={result['relation_rows']:,} | "
                f"source={result['source_seconds']:.1f}s "
                f"classify={result['classify_seconds']:.1f}s "
                f"write={result['write_seconds']:.1f}s "
                f"total={result['total_seconds']:.1f}s | "
                f"retries={retry_count:,} | "
                f"ETA={format_duration(eta_seconds)}",
                flush=True,
            )
        if stop_event.is_set():
            break
        submit_available()
        if not ready:
            wait_seconds = 0.25
            if not jobs and pending_items:
                next_ready = min(item[2] for item in pending_items)
                wait_seconds = min(
                    0.25,
                    max(0.01, next_ready - time.monotonic()),
                )
            time.sleep(wait_seconds)
    return results


def drain_progress_queue(
    progress_queue: Any,
    active: dict[tuple[str, str], dict],
) -> None:
    while True:
        try:
            item = progress_queue.get_nowait()
        except queue.Empty:
            return
        key = (str(item["corpus"]), str(item["start"]))
        if item.get("stage") == "completed":
            active.pop(key, None)
        else:
            active[key] = item


def print_active_progress(
    active: dict[tuple[str, str], dict],
    *,
    durable: int,
    total: int,
    elapsed: float,
) -> None:
    summaries = []
    for corpus in ("news", "sec"):
        rows = [
            row for row in active.values() if row["corpus"] == corpus
        ]
        if not rows:
            continue
        focus = min(rows, key=lambda row: row["start"])
        retrying = sum(row.get("stage") == "retry" for row in rows)
        summaries.append(
            f"{corpus.upper()} active={len(rows) - retrying} "
            f"retry={retrying} "
            f"src={sum(int(row['source_rows']) for row in rows):,} "
            f"labels={sum(int(row['label_rows']) for row in rows):,} "
            f"oldest={focus['start']}"
        )
    print(
        f"ACTIVE [{durable:,}/{total:,}] elapsed={format_duration(elapsed)}"
        f" | {' | '.join(summaries)}",
        flush=True,
    )


@dataclass
class JsonInsertBuffer:
    """Serialize once and flush on both byte and row safety bounds."""

    client: ClickHouseHttpClient
    database: str
    table: str
    max_bytes: int = DEFAULT_INSERT_BYTES
    max_rows: int = 20_000
    _rows: list[str] = field(default_factory=list)
    _bytes: int = 0

    def add(self, row: dict) -> None:
        encoded = json.dumps(
            row,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        encoded_bytes = len(encoded.encode("utf-8")) + 1
        if self._rows and (
            self._bytes + encoded_bytes > self.max_bytes
            or len(self._rows) >= self.max_rows
        ):
            self.flush()
        self._rows.append(encoded)
        self._bytes += encoded_bytes
        if self._bytes >= self.max_bytes or len(self._rows) >= self.max_rows:
            self.flush()

    def extend(self, rows: Iterable[dict]) -> None:
        for row in rows:
            self.add(row)

    def flush(self) -> None:
        if not self._rows:
            return
        insert_serialized_rows(
            self.client,
            self.database,
            self.table,
            self._rows,
        )
        self._rows.clear()
        self._bytes = 0


def worker_issuer_resolver(
    client: ClickHouseHttpClient,
) -> NewsIssuerResolver:
    global _WORKER_ISSUER_RESOLVER
    if _WORKER_ISSUER_RESOLVER is None:
        _WORKER_ISSUER_RESOLVER = load_news_issuer_resolver(
            client,
            _WORKER_DATABASE,
        )
    return _WORKER_ISSUER_RESOLVER


def _stop_requested() -> bool:
    return bool(
        _WORKER_STOP_EVENT is not None
        and _WORKER_STOP_EVENT.is_set()
    )


def publish_worker_progress(
    *,
    corpus: str,
    start: str,
    stage: str,
    source_rows: int,
    label_rows: int,
    relation_rows: int,
) -> None:
    if _WORKER_PROGRESS_QUEUE is None:
        return
    _WORKER_PROGRESS_QUEUE.put(
        {
            "corpus": corpus,
            "start": start,
            "stage": stage,
            "source_rows": source_rows,
            "label_rows": label_rows,
            "relation_rows": relation_rows,
            "worker_pid": os.getpid(),
        }
    )


def process_unit(
    corpus: str,
    start: str,
    end: str,
    run_id: str,
    insert_bytes: int = DEFAULT_INSERT_BYTES,
    heartbeat_seconds: float = DEFAULT_HEARTBEAT_SECONDS,
    attempt: int = 0,
    transient_retries: int = DEFAULT_TRANSIENT_RETRIES,
) -> dict:
    database = _WORKER_DATABASE
    client = make_client()
    source_count = 0
    label_count = 0
    relation_count = 0
    classify_seconds = 0.0
    write_seconds = 0.0
    unit_started = time.perf_counter()
    heartbeat_at = unit_started
    label_buffer = JsonInsertBuffer(
        client, database, TARGET_TABLE, insert_bytes
    )
    relation_buffer = JsonInsertBuffer(
        client, database, RELATION_TABLE, insert_bytes
    )
    try:
        insert_status(
            client, database, corpus, start, end, run_id,
            0, 0, 0, "running", "", stage="source",
            worker_pid=os.getpid(),
        )
        publish_worker_progress(
            corpus=corpus,
            start=start,
            stage="source",
            source_rows=0,
            label_rows=0,
            relation_rows=0,
        )
        issuer_resolver = (
            worker_issuer_resolver(client) if corpus == "news" else None
        )
        rows: Iterator[dict] = (
            iter_news_period(client, database, start, end)
            if corpus == "news"
            else iter_sec_period(client, database, start, end)
        )
        for row in rows:
            if _stop_requested():
                raise InterruptedError("operator interruption requested")
            source_count += 1
            document = row_to_document(row, corpus)
            classify_started = time.perf_counter()
            labels = (
                classify_news_document(
                    document,
                    issuer_resolver=issuer_resolver,
                )
                if corpus == "news"
                else classify_sec_document(document)
            )
            classify_seconds += time.perf_counter() - classify_started
            write_started = time.perf_counter()
            for label in labels:
                label_buffer.add(persistence_row(document, label, run_id))
            for label in labels:
                relations = relationship_rows(document, label, run_id)
                relation_buffer.extend(relations)
                relation_count += len(relations)
            label_count += len(labels)
            write_seconds += time.perf_counter() - write_started
            now = time.perf_counter()
            if now - heartbeat_at >= heartbeat_seconds:
                write_started = time.perf_counter()
                insert_status(
                    client, database, corpus, start, end, run_id,
                    source_count, label_count, relation_count,
                    "running", "", stage="classify",
                    worker_pid=os.getpid(),
                    classify_seconds=classify_seconds,
                    write_seconds=write_seconds,
                    total_seconds=now - unit_started,
                )
                write_seconds += time.perf_counter() - write_started
                publish_worker_progress(
                    corpus=corpus,
                    start=start,
                    stage="classify",
                    source_rows=source_count,
                    label_rows=label_count,
                    relation_rows=relation_count,
                )
                heartbeat_at = now
        write_started = time.perf_counter()
        label_buffer.flush()
        relation_buffer.flush()
        write_seconds += time.perf_counter() - write_started
        total_seconds = time.perf_counter() - unit_started
        source_seconds = max(
            0.0, total_seconds - classify_seconds - write_seconds
        )
        insert_status(
            client, database, corpus, start, end, run_id,
            source_count, label_count, relation_count, "completed", "",
            stage="completed", worker_pid=os.getpid(),
            source_seconds=source_seconds,
            classify_seconds=classify_seconds,
            write_seconds=write_seconds,
            total_seconds=total_seconds,
        )
        publish_worker_progress(
            corpus=corpus,
            start=start,
            stage="completed",
            source_rows=source_count,
            label_rows=label_count,
            relation_rows=relation_count,
        )
        return {
            "corpus": corpus,
            "start": start,
            "source_rows": source_count,
            "label_rows": label_count,
            "relation_rows": relation_count,
            "source_seconds": source_seconds,
            "classify_seconds": classify_seconds,
            "write_seconds": write_seconds,
            "total_seconds": total_seconds,
        }
    except Exception as exc:
        try:
            status = (
                "interrupted"
                if isinstance(exc, InterruptedError)
                else (
                    "retrying"
                    if (
                        attempt < transient_retries
                        and is_transient_clickhouse_error(exc)
                    )
                    else "failed"
                )
            )
            total_seconds = time.perf_counter() - unit_started
            insert_status(
                client, database, corpus, start, end, run_id,
                source_count, label_count, relation_count, status,
                f"{type(exc).__name__}: {exc}"[:1000],
                stage=status, worker_pid=os.getpid(),
                source_seconds=max(
                    0.0, total_seconds - classify_seconds - write_seconds
                ),
                classify_seconds=classify_seconds,
                write_seconds=write_seconds,
                total_seconds=total_seconds,
            )
        finally:
            raise
    finally:
        client.close()


def iter_news_period(
    client: ClickHouseHttpClient,
    database: str,
    start: str,
    end: str,
) -> Iterator[dict]:
    config = CandidateInventoryConfig()
    db = quote_ident(database)
    sql = f"""
SELECT
 e.canonical_news_id AS source_id,
 toString(e.published_at_utc) AS source_timestamp,
 e.title,
 r.rendered_text AS text,
 e.tickers AS entity_terms,
 e.tickers,
 e.channels,
 e.provider_tags,
 e.links,
 e.author,
 e.url_domain,
 e.article_url,
 e.content_quality_flags,
 r.renderer_version,
 r.text_contract,
 r.quality_flags,
 r.rendered_text_hash
FROM {db}.{quote_ident(config.news_event_table)} AS e FINAL
INNER JOIN {db}.{quote_ident(config.news_rendered_table)} AS r FINAL
 ON r.published_date=e.published_date
 AND r.provider_article_id=e.provider_article_id
 AND r.source_revision_key=e.source_revision_key
WHERE e.published_at_utc >= toDateTime64({sql_string(start)}, 9, 'UTC')
  AND e.published_at_utc < toDateTime64({sql_string(end)}, 9, 'UTC')
  AND notEmpty(r.rendered_text)
ORDER BY e.published_at_utc, e.canonical_news_id
FORMAT JSONEachRow
"""
    yield from client.iter_json_each_row(sql)


def iter_sec_period(
    client: ClickHouseHttpClient,
    database: str,
    start: str,
    end: str,
) -> Iterator[dict]:
    config = CandidateInventoryConfig()
    db = quote_ident(database)
    filing_sql = f"""
SELECT
 filing_id, cik, accession_number,
 toString(accepted_at_utc) AS source_timestamp,
 ifNull(company_name, '') AS company_name,
 ifNull(form_type, '') AS form_type,
 ifNull(items, '') AS filing_items,
 ifNull(toString(filing_date), '') AS filing_date,
 ifNull(toString(report_date), '') AS report_date,
 accepted_at_source
FROM {db}.{quote_ident(config.sec_filing_table)} FINAL
WHERE accepted_at_utc >= toDateTime64({sql_string(start)}, 9, 'UTC')
  AND accepted_at_utc < toDateTime64({sql_string(end)}, 9, 'UTC')
ORDER BY accepted_at_utc, cik, accession_number
FORMAT JSONEachRow
"""
    mappings: dict[str, list[dict]] = {}
    stream = client.iter_json_each_row(filing_sql)
    while True:
        filings = list(next_rows(stream, 64))
        if not filings:
            break
        unseen_ciks = {
            str(row.get("cik") or "")
            for row in filings
            if row.get("cik") and str(row["cik"]) not in mappings
        }
        extend_sec_ticker_mappings(
            client,
            database,
            unseen_ciks,
            mappings,
        )
        filing_by_key = {
            (str(row["cik"]), str(row["accession_number"])): row
            for row in filings
        }
        documents = list(
            iter_sec_documents_for_filings(
                client,
                database,
                tuple(filing_by_key),
            )
        )
        enriched: list[dict] = []
        for row in documents:
            filing = filing_by_key.get(
                (str(row["cik"]), str(row["accession_number"]))
            )
            if filing is None:
                raise RuntimeError(
                    "SEC bounded document query returned an unrequested "
                    "filing identity"
                )
            row.update(
                {
                    "source_timestamp": filing["source_timestamp"],
                    "company_name": filing["company_name"],
                    "form_type": filing["form_type"],
                    "filing_items": filing["filing_items"],
                    "filing_date": filing["filing_date"],
                    "report_date": filing["report_date"],
                    "accepted_at_source": filing["accepted_at_source"],
                    "entity_terms": [
                        row["cik"],
                        filing["company_name"],
                    ],
                    "title": " ".join(
                        value
                        for value in (
                            filing["company_name"],
                            filing["form_type"],
                            row["document_type"],
                            row["description"],
                        )
                        if value
                    ),
                }
            )
            attach_sec_ticker(row, mappings)
            enriched.append(row)
        enriched.sort(
            key=lambda row: (
                str(row["source_timestamp"]),
                str(row["source_id"]),
            )
        )
        yield from enriched


def iter_sec_documents_for_filings(
    client: ClickHouseHttpClient,
    database: str,
    keys: Sequence[tuple[str, str]],
) -> Iterator[dict]:
    if not keys:
        return
    config = CandidateInventoryConfig()
    db = quote_ident(database)
    key_sql = ",".join(
        f"({sql_string(cik)},{sql_string(accession)})"
        for cik, accession in keys
    )
    yield from client.iter_json_each_row(f"""
SELECT
 r.document_id AS source_id,
 r.text,
 [] AS tickers,
 r.cik,
 r.accession_number,
 r.filing_id,
 r.text_kind,
 r.text_char_count,
 r.text_sha256,
 r.normalizer_version AS source_normalizer_version,
 r.extraction_method,
 r.quality_flags,
 ifNull(d.document_type, '') AS document_type,
 ifNull(d.document_role, '') AS document_role,
 ifNull(d.description, '') AS description,
 ifNull(d.document_name, '') AS document_name
FROM
(
 SELECT *
 FROM {db}.{quote_ident(config.sec_rendered_table)} FINAL
 WHERE (cik, accession_number) IN ({key_sql})
   AND notEmpty(text)
) AS r
LEFT JOIN
(
 SELECT document_id, cik, accession_number,
        document_type, document_role, description, document_name
 FROM {db}.{quote_ident(config.sec_document_table)} FINAL
 WHERE (cik, accession_number) IN ({key_sql})
) AS d
 ON d.document_id=r.document_id
 AND d.cik=r.cik
 AND d.accession_number=r.accession_number
ORDER BY r.cik, r.accession_number, r.document_id
FORMAT JSONEachRow
""")


def extend_sec_ticker_mappings(
    client: ClickHouseHttpClient,
    database: str,
    ciks: Iterable[str],
    mappings: dict[str, list[dict]],
) -> None:
    """Populate an incremental CIK cache without rescanning SEC documents."""
    db = quote_ident(database)
    pending = sorted({str(value) for value in ciks if value})
    for cik in pending:
        mappings.setdefault(cik, [])
    for offset in range(0, len(pending), 500):
        values = ",".join(
            sql_string(value) for value in pending[offset : offset + 500]
        )
        for row in client.iter_json_each_row(f"""
SELECT cik, ifNull(ticker, '') AS ticker,
       ifNull(toString(valid_from_date), '') AS valid_from_date,
       ifNull(toString(valid_to_date_exclusive), '')
           AS valid_to_date_exclusive,
       mapping_status, ambiguity_status, confidence_score
FROM {db}.id_sec_market_bridge_v1 FINAL
WHERE cik IN ({values}) AND notEmpty(ifNull(ticker, ''))
FORMAT JSONEachRow
"""):
            mappings.setdefault(str(row["cik"]), []).append(row)


def next_rows(rows: Iterator[dict], size: int) -> Iterator[dict]:
    for _ in range(size):
        try:
            yield next(rows)
        except StopIteration:
            return


def attach_sec_ticker(
    row: dict,
    mappings: dict[str, list[dict]],
) -> None:
    accepted = parse_utc(str(row["source_timestamp"])).date()
    eligible = [
        value
        for value in mappings.get(str(row.get("cik") or ""), ())
        if date_contains(value, accepted)
        and str(value.get("mapping_status") or "") in {"resolved", "active"}
        and str(value.get("ambiguity_status") or "")
        not in {"ambiguous", "unresolved"}
    ]
    eligible.sort(
        key=lambda value: (
            -float(value.get("confidence_score") or 0.0),
            str(value.get("ticker") or ""),
        )
    )
    ticker = str(eligible[0]["ticker"]).upper() if eligible else ""
    row["tickers"] = [ticker] if ticker else []
    row["ticker_mapping_status"] = (
        "resolved_point_in_time" if ticker else "missing"
    )


def fetch_news_period(
    client: ClickHouseHttpClient,
    database: str,
    start: str,
    end: str,
) -> list[dict]:
    """Compatibility helper for bounded tests and audits."""
    return list(iter_news_period(client, database, start, end))


def fetch_sec_period(
    client: ClickHouseHttpClient,
    database: str,
    start: str,
    end: str,
) -> list[dict]:
    """Compatibility helper for bounded tests and audits."""
    return list(iter_sec_period(client, database, start, end))


def row_to_document(row: dict, corpus: str) -> SemanticDocument:
    excluded = {
        "source_id", "source_timestamp", "title", "text", "entity_terms"
    }
    return SemanticDocument(
        corpus=corpus,
        source_id=str(row["source_id"]),
        timestamp=str(row["source_timestamp"]),
        title=str(row.get("title") or ""),
        text=str(row.get("text") or ""),
        entity_terms=tuple(str(value) for value in row.get("entity_terms") or []),
        tickers=tuple(
            str(value).upper() for value in row.get("tickers") or [] if value
        ),
        metadata={key: value for key, value in row.items() if key not in excluded},
    )


def persistence_row(
    document: SemanticDocument,
    label: ScopedLabel,
    run_id: str,
) -> dict:
    semantic = label.semantic
    classification = label.classification
    return {
        "corpus": document.corpus,
        "source_id": document.source_id,
        "source_timestamp": _clickhouse_time(document.timestamp),
        "unit_id": label.unit_id,
        "ticker": label.ticker,
        "unit_role": label.unit_role,
        # The canonical rendered-news table remains the one non-redundant
        # publication-text authority. Labels retain its hash plus only the
        # ticker-specific evidence used for deterministic semantics.
        "publication_text_hash": label.publication_text_hash,
        "event_id": label.event_id,
        "event_tickers": label.event_tickers,
        "issuer_role": label.issuer_role,
        "evidence_scope": label.evidence_scope,
        "semantic_evidence_text": label.semantic_evidence_text,
        "relevant_text": semantic["normalized_semantic_text"],
        "observed_direction": label.observed_reaction.direction,
        "observed_move_pct": label.observed_reaction.move_pct,
        "observed_resulting_price": label.observed_reaction.resulting_price,
        "observed_market_session": label.observed_reaction.market_session,
        "observed_evidence": label.observed_reaction.evidence,
        "reported_catalyst": label.reported_catalyst,
        "content_role": classification["content_role"],
        "source_origin": classification["source_origin"],
        "event_concepts": classification["event_concepts"],
        "semantic_direction": classification["semantic_direction"],
        "semantic_score": classification["semantic_score"],
        "forecast_trigger_eligible": int(label.forecast_trigger_eligible),
        "reaction_evaluation_eligible": int(
            label.reaction_evaluation_eligible
        ),
        "issuer_history_context_eligible": int(
            label.issuer_history_context_eligible
        ),
        "semantic_json": json.dumps(semantic, separators=(",", ":")),
        "classification_json": json.dumps(
            classification, separators=(",", ":")
        ),
        "labeling_version": label.version,
        "run_id": run_id,
        "updated_at_utc": _clickhouse_time(
            dt.datetime.now(dt.timezone.utc).isoformat()
        ),
    }


def relationship_rows(
    document: SemanticDocument,
    label: ScopedLabel,
    run_id: str,
) -> list[dict]:
    """Create normalized graph edges without copying publication text."""
    source_node = f"{document.corpus}:source:{document.source_id}"
    unit_node = f"{document.corpus}:unit:{label.unit_id}"
    event_node = f"event:{label.event_id}" if label.event_id else ""
    ticker_node = f"issuer:ticker:{label.ticker}" if label.ticker else ""
    edges = [(source_node, unit_node, "contains_unit", "")]
    if ticker_node:
        edges.append(
            (unit_node, ticker_node, "about_issuer", label.issuer_role)
        )
    if event_node:
        edges.append(
            (
                unit_node,
                event_node,
                "evidence_for_event",
                label.evidence_scope,
            )
        )
        if ticker_node:
            edges.append(
                (
                    event_node,
                    ticker_node,
                    "affects_issuer",
                    label.issuer_role,
                )
            )
    for concept in label.classification["event_concepts"]:
        edges.append(
            (unit_node, f"concept:{concept}", "expresses_concept", "")
        )
    updated = _clickhouse_time(dt.datetime.now(dt.timezone.utc).isoformat())
    return [
        {
            "corpus": document.corpus,
            "source_id": document.source_id,
            "source_timestamp": _clickhouse_time(document.timestamp),
            "from_node": left,
            "to_node": right,
            "relation_type": relation,
            "relation_role": role,
            "labeling_version": label.version,
            "run_id": run_id,
            "updated_at_utc": updated,
        }
        for left, right, relation, role in edges
    ]


def create_tables(client: ClickHouseHttpClient, database: str) -> None:
    db = quote_ident(database)
    client.execute(f"""
CREATE TABLE IF NOT EXISTS {db}.{quote_ident(TARGET_TABLE)}
(
 corpus LowCardinality(String),
 source_id String,
 source_timestamp DateTime64(9, 'UTC'),
 unit_id String,
 ticker LowCardinality(String),
 unit_role LowCardinality(String),
 publication_text_hash FixedString(64),
 event_id String,
 event_tickers Array(LowCardinality(String)),
 issuer_role LowCardinality(String),
 evidence_scope LowCardinality(String),
 semantic_evidence_text String,
 relevant_text String,
 observed_direction LowCardinality(String),
 observed_move_pct Nullable(Float64),
 observed_resulting_price Nullable(Float64),
 observed_market_session LowCardinality(String),
 observed_evidence String,
 reported_catalyst String,
 content_role LowCardinality(String),
 source_origin LowCardinality(String),
 event_concepts Array(String),
 semantic_direction LowCardinality(String),
 semantic_score Float64,
 forecast_trigger_eligible UInt8,
 reaction_evaluation_eligible UInt8,
 issuer_history_context_eligible UInt8,
 semantic_json String,
 classification_json String,
 labeling_version LowCardinality(String),
 run_id String,
 updated_at_utc DateTime64(6, 'UTC')
)
ENGINE = ReplacingMergeTree(updated_at_utc)
PARTITION BY (corpus, toYYYYMM(source_timestamp))
ORDER BY (corpus, ticker, source_timestamp, source_id, unit_id, labeling_version)
""")
    client.execute(f"""
CREATE TABLE IF NOT EXISTS {db}.{quote_ident(STATUS_TABLE)}
(
 corpus LowCardinality(String),
 period_start Date,
 period_end_exclusive Date,
 labeling_version LowCardinality(String),
 run_id String,
 source_rows UInt64,
 label_rows UInt64,
 relation_rows UInt64,
 status LowCardinality(String),
 stage LowCardinality(String),
 worker_pid UInt32,
 source_seconds Float64,
 classify_seconds Float64,
 write_seconds Float64,
 total_seconds Float64,
 error String,
 updated_at_utc DateTime64(6, 'UTC')
)
ENGINE = ReplacingMergeTree(updated_at_utc)
ORDER BY (
 corpus, period_start, period_end_exclusive, labeling_version
)
""")
    client.execute(f"""
CREATE TABLE IF NOT EXISTS {db}.{quote_ident(RELATION_TABLE)}
(
 corpus LowCardinality(String),
 source_id String,
 source_timestamp DateTime64(9, 'UTC'),
 from_node String,
 to_node String,
 relation_type LowCardinality(String),
 relation_role LowCardinality(String),
 labeling_version LowCardinality(String),
 run_id String,
 updated_at_utc DateTime64(6, 'UTC')
)
ENGINE = ReplacingMergeTree(updated_at_utc)
PARTITION BY (corpus, toYYYYMM(source_timestamp))
ORDER BY (
 corpus, source_id, from_node, to_node, relation_type, labeling_version
)
""")
    client.execute(
        f"ALTER TABLE {db}.{quote_ident(STATUS_TABLE)} "
        "ADD COLUMN IF NOT EXISTS relation_rows UInt64 AFTER label_rows"
    )
    for definition in (
        "stage LowCardinality(String) AFTER status",
        "worker_pid UInt32 AFTER stage",
        "source_seconds Float64 AFTER worker_pid",
        "classify_seconds Float64 AFTER source_seconds",
        "write_seconds Float64 AFTER classify_seconds",
        "total_seconds Float64 AFTER write_seconds",
    ):
        client.execute(
            f"ALTER TABLE {db}.{quote_ident(STATUS_TABLE)} "
            f"ADD COLUMN IF NOT EXISTS {definition}"
        )


def insert_rows(
    client: ClickHouseHttpClient,
    database: str,
    table: str,
    rows: list[dict],
) -> None:
    if not rows:
        return
    serialized = [
        json.dumps(row, ensure_ascii=False, separators=(",", ":"))
        for row in rows
    ]
    insert_serialized_rows(client, database, table, serialized)


def insert_serialized_rows(
    client: ClickHouseHttpClient,
    database: str,
    table: str,
    serialized_rows: Sequence[str],
) -> None:
    if not serialized_rows:
        return
    body = "\n".join(serialized_rows)
    client.execute(
        f"INSERT INTO {quote_ident(database)}.{quote_ident(table)} "
        f"FORMAT JSONEachRow\n{body}"
    )


def insert_status(
    client: ClickHouseHttpClient,
    database: str,
    corpus: str,
    start: str,
    end: str,
    run_id: str,
    source_rows: int,
    label_rows: int,
    relation_rows: int,
    status: str,
    error: str,
    *,
    stage: str = "",
    worker_pid: int = 0,
    source_seconds: float = 0.0,
    classify_seconds: float = 0.0,
    write_seconds: float = 0.0,
    total_seconds: float = 0.0,
) -> None:
    insert_rows(
        client,
        database,
        STATUS_TABLE,
        [{
            "corpus": corpus,
            "period_start": start,
            "period_end_exclusive": end,
            "labeling_version": SCOPED_LABELING_VERSION,
            "run_id": run_id,
            "source_rows": source_rows,
            "label_rows": label_rows,
            "relation_rows": relation_rows,
            "status": status,
            "stage": stage or status,
            "worker_pid": worker_pid,
            "source_seconds": source_seconds,
            "classify_seconds": classify_seconds,
            "write_seconds": write_seconds,
            "total_seconds": total_seconds,
            "error": error,
            "updated_at_utc": _clickhouse_time(
                dt.datetime.now(dt.timezone.utc).isoformat()
            ),
        }],
    )


def completed_units(
    client: ClickHouseHttpClient,
    database: str,
) -> set[tuple[str, str, str, str]]:
    sql = f"""
SELECT corpus, toString(period_start) AS period_start,
       toString(period_end_exclusive) AS period_end_exclusive,
       labeling_version
FROM {quote_ident(database)}.{quote_ident(STATUS_TABLE)} FINAL
WHERE status='completed'
  AND labeling_version={sql_string(SCOPED_LABELING_VERSION)}
  AND (label_rows=0 OR relation_rows>0)
FORMAT JSONEachRow
"""
    return {
        (
            str(row["corpus"]),
            str(row["period_start"]),
            str(row["period_end_exclusive"]),
            str(row["labeling_version"]),
        )
        for row in json_rows(client.execute(sql))
    }


def source_counts(
    client: ClickHouseHttpClient,
    database: str,
    plan: list[tuple[str, str, str]],
) -> dict:
    config = CandidateInventoryConfig()
    tables = {
        "news": config.news_event_table,
        "sec": config.sec_filing_table,
    }
    columns = {
        "news": "published_at_utc",
        "sec": "accepted_at_utc",
    }
    result = {(corpus, start, end): 0 for corpus, start, end in plan}
    by_corpus = {
        corpus: [(start, end) for kind, start, end in plan if kind == corpus]
        for corpus in {kind for kind, _, _ in plan}
    }
    for corpus, periods in by_corpus.items():
        overall_start = min(start for start, _ in periods)
        overall_end = max(end for _, end in periods)
        sql = f"""
SELECT toDate({columns[corpus]}) AS day, count() AS rows
FROM {quote_ident(database)}.{quote_ident(tables[corpus])}
WHERE {columns[corpus]} >= toDateTime64({sql_string(overall_start)}, 9, 'UTC')
  AND {columns[corpus]} < toDateTime64({sql_string(overall_end)}, 9, 'UTC')
GROUP BY day
FORMAT JSONEachRow
"""
        daily = {
            str(row["day"]): int(row["rows"])
            for row in json_rows(client.execute(sql))
        }
        for start, end in periods:
            cursor = dt.date.fromisoformat(start)
            finish = dt.date.fromisoformat(end)
            total = 0
            while cursor < finish:
                total += daily.get(cursor.isoformat(), 0)
                cursor += dt.timedelta(days=1)
            result[(corpus, start, end)] = total
    return result


def bounded_period_ranges(
    start: str,
    end: str,
    period_days: int,
) -> list[tuple[str, str]]:
    cursor = dt.date.fromisoformat(start)
    finish = dt.date.fromisoformat(end)
    output = []
    while cursor < finish:
        right = min(cursor + dt.timedelta(days=period_days), finish)
        output.append((cursor.isoformat(), right.isoformat()))
        cursor = right
    return output


def interleaved_plan(
    corpora: Sequence[str],
    periods: Sequence[tuple[str, str]],
) -> list[tuple[str, str, str]]:
    """Advance requested corpora together instead of starving later corpora."""
    return [
        (corpus, start, end)
        for start, end in periods
        for corpus in corpora
    ]


def make_client() -> ClickHouseHttpClient:
    query_threads = max(
        1,
        min(
            8,
            int(
                os.environ.get(
                    "SCOPED_LABELING_CLICKHOUSE_MAX_THREADS",
                    "1",
                )
            ),
        ),
    )
    return ClickHouseHttpClient(
        default_clickhouse_url(),
        default_clickhouse_user(),
        default_clickhouse_password(),
        timeout_seconds=1800,
        # At 64 CPU workers the source scans must not each fan out across all
        # ClickHouse cores. One server thread per bounded query keeps database
        # concurrency proportional to the explicitly chosen worker count.
        default_query_params={"max_threads": query_threads},
    )


def _clickhouse_time(value: str) -> str:
    clean = str(value).replace("Z", "+00:00")
    parsed = dt.datetime.fromisoformat(clean)
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(dt.timezone.utc).replace(tzinfo=None)
    return parsed.strftime("%Y-%m-%d %H:%M:%S.%f")


def _validate_dates(start: str, end: str) -> None:
    if dt.date.fromisoformat(start) >= dt.date.fromisoformat(end):
        raise ValueError("start date must precede end date")


def format_duration(seconds: float) -> str:
    if not seconds or seconds < 0:
        return "unknown"
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        return f"{seconds / 60:.1f}m"
    return f"{seconds / 3600:.1f}h"


def format_retry_delay(seconds: float) -> str:
    if seconds <= 0:
        return "now"
    return format_duration(seconds)


def is_transient_clickhouse_error(exc: BaseException) -> bool:
    """Return true only for transport failures safe for whole-unit replay."""
    parts: list[str] = []
    current: BaseException | None = exc
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        parts.append(f"{type(current).__name__}: {current!r}")
        current = current.__cause__ or current.__context__
    text = " | ".join(parts)
    if any(
        marker in text
        for marker in (
            "DB::Exception",
            "ClickHouse HTTP 4",
            "QUERY_WAS_CANCELLED",
            "operator interruption requested",
        )
    ):
        return False
    return any(
        marker in text
        for marker in (
            "IncompleteRead",
            "RemoteDisconnected",
            "ConnectionResetError",
            "ConnectionAbortedError",
            "BrokenPipeError",
            "URLError",
            "ConnectionRefusedError",
            "Connection reset",
            "Connection broken",
            "No connection could be made",
            "WinError 10054",
            "WinError 10060",
            "Read timed out",
            "TimeoutError",
            "timed out",
            "ClickHouse HTTP 502",
            "ClickHouse HTTP 503",
            "ClickHouse HTTP 504",
            "ClickHouse JSONEachRow decode failed at response line",
        )
    )


def safe_error_summary(exc: BaseException) -> str:
    """Stable operator-facing reason without SQL, payloads, or source text."""
    text = repr(exc)
    if "IncompleteRead" in text:
        return "source stream closed before completion"
    if "JSONEachRow decode failed" in text:
        return "source stream ended inside a row"
    if "10054" in text or "ConnectionReset" in text:
        return "ClickHouse connection reset"
    if "10060" in text or "timed out" in text or "Timeout" in text:
        return "ClickHouse request timed out"
    if "RemoteDisconnected" in text or "Connection broken" in text:
        return "ClickHouse disconnected"
    return f"transient {type(exc).__name__}"


def parse_utc(value: Any) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def date_contains(mapping: dict[str, Any], day: dt.date) -> bool:
    start = str(mapping.get("valid_from_date") or "")
    end = str(mapping.get("valid_to_date_exclusive") or "")
    return (
        (not start or dt.date.fromisoformat(start) <= day)
        and (not end or day < dt.date.fromisoformat(end))
    )


def json_rows(value: str) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in value.splitlines()
        if line.strip()
    ]


def assert_certification(path: Path) -> None:
    if not path.exists():
        raise RuntimeError(
            f"certification manifest is missing: {path}. "
            "Run and review run_certification before persistence."
        )
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("labeling_version") != SCOPED_LABELING_VERSION:
        raise RuntimeError(
            "certification version does not match the persistence version"
        )
    if int(payload.get("news_audits") or 0) < 5 \
            or int(payload.get("sec_audits") or 0) < 5:
        raise RuntimeError("certification does not contain five News and five SEC audits")
    if int(payload.get("review_attention") or 0) != 0:
        raise RuntimeError("certification self-review still has attention items")
    if payload.get("missing_news_scope_cases") != []:
        raise RuntimeError(
            "certification is missing one or more required News issuer-scope cases"
        )
    if payload.get("expected_outcome_failures") != []:
        raise RuntimeError(
            "certification failed mandatory issuer-level semantic outcomes"
        )
