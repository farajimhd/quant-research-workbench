from __future__ import annotations

import asyncio
import hashlib
import json
import os
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field

from research.mlops.clickhouse import (
    ClickHouseHttpClient,
    insert_json_each_row,
    sql_string,
)
from research.text_intelligence.candidate_inventory_v1.config import (
    CandidateInventoryConfig,
)
from research.text_intelligence.scoped_labeling_v1.persistence import (
    RELATION_TABLE,
    TARGET_TABLE,
    attach_sec_ticker,
    create_tables,
    extend_sec_ticker_mappings,
    iter_sec_documents_for_filings,
    persistence_row,
    relationship_rows,
    row_to_document,
)
from research.text_intelligence.scoped_labeling_v1.pipeline import classify_sec_document
from research.text_intelligence.scoped_labeling_v1.schema import (
    SCOPED_LABELING_VERSION,
)
from research.text_intelligence.scoped_labeling_v1.sec_extractor import (
    sec_document_labeling_eligible,
)
from research.text_intelligence.news_synthesis_v1.engine import (
    ENGINE_VERSION as NEWS_SYNTHESIS_ENGINE_VERSION,
    NewsSynthesisEngine,
)
from research.text_intelligence.news_synthesis_v1.storage import (
    SYNTHESIS_TABLE,
    create_tables as create_news_synthesis_tables,
    load_identity_index,
    persist_documents,
)

from .live import LiveCandidate, LiveNewsRuntime, PreparedNewsCandidate, SynthesisLiveLabel


STATUS_TABLE = "scoped_text_live_status_v2"


class TextDocumentNotice(BaseModel):
    corpus: Literal["news", "sec"]
    source_id: str = Field(min_length=1, max_length=256)
    source_timestamp: str = ""
    source_cik: str = Field(default="", max_length=32)


class TextDocumentNoticeBatch(BaseModel):
    documents: list[TextDocumentNotice] = Field(min_length=1, max_length=2_000)


@dataclass(frozen=True, slots=True)
class LoadedSource:
    notice: TextDocumentNotice
    rows: tuple[dict[str, Any], ...]
    source_hash: str
    disposition: str = "ready"


@dataclass(frozen=True, slots=True)
class ScopedWorkItem:
    notice: TextDocumentNotice
    forward_current: bool = False


class ScopedTextRuntime:
    """One durable deterministic authority for newly completed News and SEC text."""

    def __init__(
        self,
        *,
        client: ClickHouseHttpClient,
        database: str,
        live_news: LiveNewsRuntime,
    ) -> None:
        self.client = client
        self.database = database
        self.live_news = live_news
        self.queue: asyncio.Queue[ScopedWorkItem | None] = asyncio.Queue(
            maxsize=max(100, int(os.environ.get("TEXT_INTELLIGENCE_QUEUE_MAX", "8192")))
        )
        self.pending: set[tuple[str, str]] = set()
        self.workers: list[asyncio.Task[None]] = []
        self.reconcile_task: asyncio.Task[None] | None = None
        self.news_engine: NewsSynthesisEngine | None = None
        self.sec_mappings: dict[str, list[dict[str, Any]]] = {}
        self.sec_mapping_lock = threading.Lock()
        self.state_lock = threading.Lock()
        self.worker_states: dict[int, dict[str, Any]] = {}
        self.recent_work: deque[dict[str, Any]] = deque(maxlen=50)
        self.active_failures: dict[tuple[str, str], dict[str, str]] = {}
        self.started_at_utc = datetime.now(UTC).isoformat()
        self.metrics: dict[str, int | str] = {
            "deterministic_queued": 0,
            "deterministic_completed": 0,
            "deterministic_skipped_current": 0,
            "deterministic_ineligible": 0,
            "deterministic_deferred_not_ready": 0,
            "deterministic_failed": 0,
            "deterministic_news_labels": 0,
            "deterministic_sec_labels": 0,
            "deterministic_reconciled": 0,
            "deterministic_live_forwarded": 0,
            "deterministic_live_forward_failed": 0,
            "deterministic_last_error": "",
            "deterministic_last_error_status": "",
            "deterministic_last_error_at_utc": "",
            "deterministic_worker_last_error": "",
            "deterministic_worker_error_status": "",
            "deterministic_last_success_at_utc": "",
            "deterministic_reconcile_runs": 0,
            "deterministic_reconcile_notices": 0,
            "deterministic_reconcile_seconds": 0,
            "deterministic_reconcile_last_at_utc": "",
            "deterministic_reconcile_last_error": "",
            "deterministic_reconcile_error_status": "",
            "deterministic_shutdown_deferred": 0,
            "deterministic_runtime_status": "starting",
        }

    async def start(self) -> None:
        await asyncio.to_thread(create_tables, self.client, self.database)
        await asyncio.to_thread(create_news_synthesis_tables, self.client, self.database)
        await asyncio.to_thread(self._ensure_status_table)
        identity_index = await asyncio.to_thread(load_identity_index, self.client, self.database)
        self.news_engine = NewsSynthesisEngine(identity_index)
        count = max(1, min(16, int(os.environ.get("TEXT_INTELLIGENCE_WORKERS", "4"))))
        with self.state_lock:
            self.worker_states = {
                index: self._idle_worker_state(index) for index in range(count)
            }
        self.workers = [
            asyncio.create_task(self._worker(index), name=f"text-intelligence-{index}")
            for index in range(count)
        ]
        self.reconcile_task = asyncio.create_task(
            self._reconcile_loop(), name="text-intelligence-reconcile"
        )
        self.metrics["deterministic_runtime_status"] = "running"

    async def stop(self) -> None:
        self.metrics["deterministic_runtime_status"] = "stopping"
        if self.reconcile_task:
            self.reconcile_task.cancel()
            await asyncio.gather(self.reconcile_task, return_exceptions=True)
        deferred = 0
        while True:
            try:
                item = self.queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if item is not None:
                self.pending.discard((item.notice.corpus, item.notice.source_id))
                deferred += 1
            self.queue.task_done()
        self.metrics["deterministic_shutdown_deferred"] = deferred
        for _ in self.workers:
            await self.queue.put(None)
        await self.queue.join()
        await asyncio.gather(*self.workers, return_exceptions=True)
        self.metrics["deterministic_runtime_status"] = "stopped"

    def enqueue(self, notice: TextDocumentNotice, *, reconciled: bool = False) -> None:
        identity = (notice.corpus, notice.source_id)
        if identity in self.pending:
            return
        self.pending.add(identity)
        try:
            self.queue.put_nowait(ScopedWorkItem(notice=notice))
        except asyncio.QueueFull:
            self.pending.discard(identity)
            raise
        self.metrics["deterministic_queued"] = int(
            self.metrics["deterministic_queued"]
        ) + 1
        if reconciled:
            self.metrics["deterministic_reconciled"] = int(
                self.metrics["deterministic_reconciled"]
            ) + 1

    async def _worker(self, index: int) -> None:
        while True:
            item = await self.queue.get()
            try:
                if item is None:
                    return
                self._set_worker_state(index, item.notice, "loading_source")
                result = await asyncio.to_thread(
                    self._process_notice,
                    item.notice,
                    item.forward_current,
                    index,
                )
                now = datetime.now(UTC).isoformat()
                self.metrics["deterministic_last_success_at_utc"] = now
                self.metrics["deterministic_last_error_status"] = "resolved"
                self._resolve_failure(item.notice)
                self._record_recent(
                    item.notice,
                    str(result),
                    "waiting" if result == "deferred_not_ready" else "complete",
                    (
                        "Canonical rendered text is not ready; reconciliation will retry."
                        if result == "deferred_not_ready"
                        else ""
                    ),
                )
            except Exception as exc:  # noqa: BLE001
                self.metrics["deterministic_failed"] = int(
                    self.metrics["deterministic_failed"]
                ) + 1
                self.metrics["deterministic_last_error"] = (
                    f"{type(exc).__name__}: {exc}"
                )[:500]
                self.metrics["deterministic_last_error_status"] = "active"
                self.metrics["deterministic_worker_last_error"] = self.metrics[
                    "deterministic_last_error"
                ]
                self.metrics["deterministic_worker_error_status"] = "active"
                self.metrics["deterministic_last_error_at_utc"] = datetime.now(
                    UTC
                ).isoformat()
                if item is not None:
                    self._record_failure(
                        item.notice,
                        str(self.metrics["deterministic_last_error"]),
                    )
                    self._record_recent(
                        item.notice,
                        "failed",
                        "failed",
                        str(self.metrics["deterministic_last_error"]),
                    )
                    await asyncio.to_thread(
                        self._write_status,
                        item.notice,
                        "",
                        "failed",
                        0,
                        0,
                        self.metrics["deterministic_last_error"],
                    )
            finally:
                self._set_worker_idle(index)
                if item is not None:
                    self.pending.discard(
                        (item.notice.corpus, item.notice.source_id)
                    )
                self.queue.task_done()

    def _process_notice(
        self,
        notice: TextDocumentNotice,
        forward_current: bool = False,
        worker_index: int | None = None,
    ) -> str:
        if notice.corpus == "news":
            return self._process_news_notice(notice, forward_current, worker_index)
        self._set_worker_stage(worker_index, "loading_source")
        loaded = self._load_source(notice)
        if loaded.disposition == "not_ready":
            self.metrics["deterministic_deferred_not_ready"] = int(
                self.metrics["deterministic_deferred_not_ready"]
            ) + 1
            return "deferred_not_ready"
        self._set_worker_stage(worker_index, "checking_status")
        source_is_current = self._status_is_current(notice, loaded.source_hash)
        if loaded.disposition == "ineligible":
            if source_is_current:
                self.metrics["deterministic_skipped_current"] = int(
                    self.metrics["deterministic_skipped_current"]
                ) + 1
                return "skipped_current"
            self._set_worker_stage(worker_index, "writing_status")
            self._write_status(notice, loaded.source_hash, "complete", 0, 0, "")
            self.metrics["deterministic_ineligible"] = int(
                self.metrics["deterministic_ineligible"]
            ) + 1
            self.metrics["deterministic_completed"] = int(
                self.metrics["deterministic_completed"]
            ) + 1
            return "skipped_ineligible"
        if not loaded.rows:
            raise RuntimeError(
                f"canonical {notice.corpus} source is not ready: {notice.source_id}"
            )
        if source_is_current and not forward_current:
            self.metrics["deterministic_skipped_current"] = int(
                self.metrics["deterministic_skipped_current"]
            ) + 1
            return "skipped_current"
        run_id = f"live-{uuid.uuid4().hex}"
        label_rows: list[dict[str, Any]] = []
        relation_rows: list[dict[str, Any]] = []
        prepared_news: list[PreparedNewsCandidate] = []
        self._set_worker_stage(worker_index, "classifying")
        for source_row in loaded.rows:
            document = row_to_document(source_row, notice.corpus)
            labels = classify_sec_document(document)
            label_rows.extend(
                persistence_row(document, label, run_id) for label in labels
            )
            for label in labels:
                relation_rows.extend(relationship_rows(document, label, run_id))
        if label_rows and not source_is_current:
            self._set_worker_stage(worker_index, "writing_labels")
            insert_json_each_row(
                self.client,
                self.database,
                TARGET_TABLE,
                list(label_rows[0]),
                label_rows,
            )
        if relation_rows and not source_is_current:
            self._set_worker_stage(worker_index, "writing_relations")
            insert_json_each_row(
                self.client,
                self.database,
                RELATION_TABLE,
                list(relation_rows[0]),
                relation_rows,
            )
        if not source_is_current:
            self._set_worker_stage(worker_index, "writing_status")
            self._write_status(
                notice,
                loaded.source_hash,
                "complete",
                len(label_rows),
                len(relation_rows),
                "",
            )
            key = (
                "deterministic_news_labels"
                if notice.corpus == "news"
                else "deterministic_sec_labels"
            )
            self.metrics[key] = int(self.metrics[key]) + len(label_rows)
            self.metrics["deterministic_completed"] = int(
                self.metrics["deterministic_completed"]
            ) + 1
        # Optional market inference is downstream of durable deterministic state.
        for item in (prepared_news if self.live_news.enabled else ()):
            self._set_worker_stage(worker_index, "forwarding_live")
            try:
                accepted = self.live_news.enqueue_prepared_threadsafe(item)
            except Exception:  # noqa: BLE001
                self.metrics["deterministic_live_forward_failed"] = int(
                    self.metrics["deterministic_live_forward_failed"]
                ) + 1
            else:
                if accepted:
                    self.metrics["deterministic_live_forwarded"] = int(
                        self.metrics["deterministic_live_forwarded"]
                    ) + 1
        return "complete" if not source_is_current else "forwarded_current"

    def _process_news_notice(
        self,
        notice: TextDocumentNotice,
        forward_current: bool,
        worker_index: int | None,
    ) -> str:
        self._set_worker_stage(worker_index, "loading_source")
        loaded = self._load_news(notice)
        if loaded.disposition == "not_ready":
            self.metrics["deterministic_deferred_not_ready"] = int(self.metrics["deterministic_deferred_not_ready"]) + 1
            return "deferred_not_ready"
        if not loaded.rows or self.news_engine is None:
            raise RuntimeError(f"canonical news source or synthesis engine is not ready: {notice.source_id}")
        self._set_worker_stage(worker_index, "checking_status")
        current = self._status_is_current(notice, loaded.source_hash)
        if current and not forward_current:
            self.metrics["deterministic_skipped_current"] = int(self.metrics["deterministic_skipped_current"]) + 1
            return "skipped_current"
        self._set_worker_stage(worker_index, "synthesizing")
        documents = [self.news_engine.synthesize(row) for row in loaded.rows]
        if not current:
            self._set_worker_stage(worker_index, "writing_synthesis")
            persist_documents(self.client, self.database, documents)
            self._write_status(notice, loaded.source_hash, "complete", len(documents), 0, "")
            self.metrics["deterministic_news_labels"] = int(self.metrics["deterministic_news_labels"]) + len(documents)
            self.metrics["deterministic_completed"] = int(self.metrics["deterministic_completed"]) + 1
        if self.live_news.enabled:
            for source_row, document in zip(loaded.rows, documents):
                item = PreparedNewsCandidate(
                    candidate=_live_candidate(source_row),
                    synthesis_labels=_live_synthesis_labels(document),
                )
                self._set_worker_stage(worker_index, "forwarding_live")
                try:
                    accepted = self.live_news.enqueue_prepared_threadsafe(item)
                except Exception:  # noqa: BLE001
                    self.metrics["deterministic_live_forward_failed"] = int(self.metrics["deterministic_live_forward_failed"]) + 1
                else:
                    if accepted:
                        self.metrics["deterministic_live_forwarded"] = int(self.metrics["deterministic_live_forwarded"]) + 1
        return "complete" if not current else "forwarded_current"

    def _load_source(self, notice: TextDocumentNotice) -> LoadedSource:
        return (
            self._load_news(notice)
            if notice.corpus == "news"
            else self._load_sec(notice)
        )

    def snapshot_metrics(self) -> dict[str, Any]:
        with self.state_lock:
            workers = [dict(row) for _, row in sorted(self.worker_states.items())]
            recent = [dict(row) for row in reversed(self.recent_work)]
            active_failures = [
                dict(row) for row in reversed(list(self.active_failures.values()))
            ]
        return {
            **self.metrics,
            "deterministic_queue_size": self.queue.qsize(),
            "deterministic_pending": len(self.pending),
            "deterministic_active_workers": sum(
                1 for row in workers if row.get("status") == "processing"
            ),
            "deterministic_workers": workers,
            "deterministic_recent_work": recent,
            "deterministic_active_failure_count": len(active_failures),
            "deterministic_active_failures": active_failures,
            "deterministic_worker_error_status": (
                "active" if active_failures else "resolved"
            ),
            "deterministic_worker_last_error": (
                str(active_failures[0].get("error") or "")
                if active_failures
                else str(self.metrics.get("deterministic_worker_last_error") or "")
            ),
            "deterministic_started_at_utc": self.started_at_utc,
        }

    @staticmethod
    def _idle_worker_state(index: int) -> dict[str, Any]:
        return {
            "worker": index + 1,
            "status": "waiting",
            "corpus": "",
            "source_id": "",
            "stage": "waiting_for_notice",
            "started_at_utc": "",
        }

    def _set_worker_state(
        self, index: int, notice: TextDocumentNotice, stage: str
    ) -> None:
        with self.state_lock:
            self.worker_states[index] = {
                "worker": index + 1,
                "status": "processing",
                "corpus": notice.corpus,
                "source_id": notice.source_id,
                "stage": stage,
                "started_at_utc": datetime.now(UTC).isoformat(),
            }

    def _set_worker_stage(self, index: int | None, stage: str) -> None:
        if index is None:
            return
        with self.state_lock:
            state = self.worker_states.get(index)
            if state is not None:
                state["stage"] = stage

    def _set_worker_idle(self, index: int) -> None:
        with self.state_lock:
            self.worker_states[index] = self._idle_worker_state(index)

    def _record_recent(
        self,
        notice: TextDocumentNotice,
        stage: str,
        status: str,
        detail: str,
    ) -> None:
        with self.state_lock:
            self.recent_work.append(
                {
                    "updated_at_utc": datetime.now(UTC).isoformat(),
                    "source_id": notice.source_id,
                    "corpus": notice.corpus,
                    "stage": stage,
                    "status": status,
                    "detail": detail[:300],
                }
            )

    def _record_failure(self, notice: TextDocumentNotice, error: str) -> None:
        with self.state_lock:
            self.active_failures[(notice.corpus, notice.source_id)] = {
                "corpus": notice.corpus,
                "source_id": notice.source_id,
                "error": error[:300],
                "updated_at_utc": datetime.now(UTC).isoformat(),
            }

    def _resolve_failure(self, notice: TextDocumentNotice) -> None:
        with self.state_lock:
            self.active_failures.pop((notice.corpus, notice.source_id), None)

    def _load_news(self, notice: TextDocumentNotice) -> LoadedSource:
        source_time = _parse_utc(notice.source_timestamp)
        if source_time is None:
            raise ValueError(
                "news notice requires source_timestamp for a bounded canonical read"
            )
        source_date = source_time.date().isoformat()
        rows = list(self.client.iter_json_each_row(f"""
SELECT
 e.canonical_news_id AS source_id,
 toString(e.published_at_utc) AS source_timestamp,
 e.title, if(empty(r.rendered_text),e.title,r.rendered_text) AS text, e.tickers AS entity_terms, e.tickers,
 e.channels, e.provider_tags, e.links, e.author, e.url_domain, e.article_url,
 e.content_quality_flags, r.renderer_version, r.text_contract,
 r.quality_flags, multiIf(empty(r.canonical_news_id),'unrendered',r.source_count=0,'title_only','rendered') render_status,
 if(empty(r.rendered_text_hash),hex(SHA256(e.title)),r.rendered_text_hash) rendered_text_hash
FROM
(
 SELECT *
 FROM `{self.database}`.`benzinga_news_event_v2` FINAL
 PREWHERE published_date=toDate({sql_string(source_date)})
 WHERE canonical_news_id={sql_string(notice.source_id)}
) AS e
LEFT JOIN
(
 SELECT *
 FROM `{self.database}`.`benzinga_news_rendered_v2` FINAL
 PREWHERE published_date=toDate({sql_string(source_date)})
) AS r
 ON r.published_date=e.published_date
 AND r.provider_article_id=e.provider_article_id
 AND r.source_revision_key=e.source_revision_key
LIMIT 1
SETTINGS max_execution_time=25
FORMAT JSONEachRow
"""))
        if not rows:
            return LoadedSource(notice, (), "", "not_ready")
        source_hash = str(rows[0].get("rendered_text_hash") or "")
        return LoadedSource(notice, tuple(rows), source_hash)

    def _load_sec(self, notice: TextDocumentNotice) -> LoadedSource:
        cik = notice.source_cik.strip()
        if not cik:
            raise ValueError(
                "SEC notice requires source_cik for an exact canonical read"
            )
        filings = list(self.client.iter_json_each_row(f"""
SELECT filing_id,cik,accession_number,
       cityHash64(cik) % 64 AS document_partition,
       toString(accepted_at_utc) source_timestamp,
       ifNull(company_name,'') company_name,ifNull(form_type,'') form_type,
       ifNull(items,'') filing_items,ifNull(toString(filing_date),'') filing_date,
       ifNull(toString(report_date),'') report_date,accepted_at_source,
       source_content_sha256
FROM `{self.database}`.`sec_filing_v3` FINAL
PREWHERE cik={sql_string(cik)}
WHERE accession_number={sql_string(notice.source_id)}
LIMIT 1
SETTINGS max_execution_time=25
FORMAT JSONEachRow
"""))
        if not filings:
            return LoadedSource(notice, (), "", "not_ready")
        filing = filings[0]
        cik = str(filing["cik"])
        partition = int(filing["document_partition"])
        config = CandidateInventoryConfig()
        metadata = list(self.client.iter_json_each_row(f"""
SELECT document_id,cik,accession_number,sequence_number,
       document_type,document_role,description,document_name
FROM
(
 SELECT document_id,cik,accession_number,sequence_number,
        document_type,document_role,description,document_name
 FROM `{self.database}`.`{config.sec_document_table}`
 PREWHERE cityHash64(cik) % 64 = {partition}
   AND cik={sql_string(cik)}
   AND accession_number={sql_string(notice.source_id)}
 ORDER BY inserted_at DESC
 LIMIT 1 BY cik,accession_number,sequence_number,document_id
)
ORDER BY sequence_number,document_id
SETTINGS max_execution_time=25
FORMAT JSONEachRow
"""))
        if not metadata:
            return LoadedSource(notice, (), "", "not_ready")
        eligible_metadata = [
            row for row in metadata if sec_document_labeling_eligible(row)
        ]
        if not eligible_metadata:
            return LoadedSource(
                notice,
                (),
                _ineligible_sec_hash(filing, metadata),
                "ineligible",
            )
        with self.sec_mapping_lock:
            if cik not in self.sec_mappings:
                extend_sec_ticker_mappings(
                    self.client, self.database, (cik,), self.sec_mappings
                )
            filing_mappings = {cik: list(self.sec_mappings.get(cik, ()))}
        documents = list(
            iter_sec_documents_for_filings(
                self.client,
                self.database,
                ((cik, str(filing["accession_number"])),),
                partition=partition,
            )
        )
        if not documents:
            return LoadedSource(notice, (), "", "not_ready")
        for row in documents:
            row.update(
                {
                    "source_timestamp": filing["source_timestamp"],
                    "company_name": filing["company_name"],
                    "form_type": filing["form_type"],
                    "filing_items": filing["filing_items"],
                    "filing_date": filing["filing_date"],
                    "report_date": filing["report_date"],
                    "accepted_at_source": filing["accepted_at_source"],
                    "entity_terms": [cik, filing["company_name"]],
                    "title": " ".join(
                        value
                        for value in (
                            filing["company_name"],
                            filing["form_type"],
                            row.get("document_type", ""),
                            row.get("description", ""),
                        )
                        if value
                    ),
                }
            )
            attach_sec_ticker(row, filing_mappings)
        return LoadedSource(
            notice, tuple(documents), _sec_document_set_hash(documents)
        )

    def _status_is_current(
        self, notice: TextDocumentNotice, source_hash: str
    ) -> bool:
        version = _authority_version(notice.corpus)
        value = self.client.execute(f"""
SELECT count()
FROM `{self.database}`.`{STATUS_TABLE}` FINAL
WHERE corpus={sql_string(notice.corpus)}
  AND source_id={sql_string(notice.source_id)}
  AND source_hash={sql_string(source_hash)}
  AND labeling_version={sql_string(version)}
  AND status='complete'
""").strip()
        return int(value or "0") > 0

    def _write_status(
        self,
        notice: TextDocumentNotice,
        source_hash: str,
        status: str,
        label_rows: int,
        relation_rows: int,
        error: str,
    ) -> None:
        row = {
            "corpus": notice.corpus,
            "source_id": notice.source_id,
            "source_timestamp": notice.source_timestamp or "1970-01-01 00:00:00",
            "source_hash": source_hash,
            "labeling_version": _authority_version(notice.corpus),
            "status": status,
            "label_rows": label_rows,
            "relation_rows": relation_rows,
            "error": error[:1_000],
            "updated_at_utc": datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f"),
        }
        insert_json_each_row(
            self.client, self.database, STATUS_TABLE, list(row), [row]
        )

    async def _reconcile_loop(self) -> None:
        interval = max(
            5.0, float(os.environ.get("TEXT_INTELLIGENCE_RECONCILE_SECONDS", "30"))
        )
        await asyncio.sleep(1.0)
        while True:
            reconcile_started = time.perf_counter()
            try:
                notices = await asyncio.to_thread(self._recent_notices)
                self.metrics["deterministic_reconcile_runs"] = int(
                    self.metrics["deterministic_reconcile_runs"]
                ) + 1
                self.metrics["deterministic_reconcile_notices"] = len(notices)
                self.metrics["deterministic_reconcile_last_at_utc"] = datetime.now(
                    UTC
                ).isoformat()
                self.metrics["deterministic_reconcile_last_error"] = ""
                self.metrics["deterministic_reconcile_error_status"] = "resolved"
                for notice in notices:
                    try:
                        self.enqueue(notice, reconciled=True)
                    except asyncio.QueueFull:
                        break
                if self.live_news.enabled and self.live_news.session.active:
                    live_notices = await asyncio.to_thread(
                        self._unmodeled_live_news_notices
                    )
                    for notice in live_notices:
                        try:
                            self._enqueue_live_forward(notice)
                        except asyncio.QueueFull:
                            break
            except Exception as exc:  # noqa: BLE001
                self.metrics["deterministic_failed"] = int(
                    self.metrics["deterministic_failed"]
                ) + 1
                self.metrics["deterministic_last_error"] = (
                    f"{type(exc).__name__}: {exc}"
                )[:500]
                self.metrics["deterministic_last_error_status"] = "active"
                self.metrics["deterministic_last_error_at_utc"] = datetime.now(
                    UTC
                ).isoformat()
                self.metrics["deterministic_reconcile_last_error"] = self.metrics[
                    "deterministic_last_error"
                ]
                self.metrics["deterministic_reconcile_error_status"] = "active"
            finally:
                self.metrics["deterministic_reconcile_seconds"] = round(
                    time.perf_counter() - reconcile_started, 3
                )
            await asyncio.sleep(interval)

    def _recent_notices(self) -> list[TextDocumentNotice]:
        hours = max(
            1, min(24 * 30, int(os.environ.get("TEXT_INTELLIGENCE_RECONCILE_HOURS", "72")))
        )
        start = datetime.now(UTC) - timedelta(hours=hours)
        start_sql = start.strftime("%Y-%m-%d %H:%M:%S.%f")
        start_date_sql = start.date().isoformat()
        start_partition = start.strftime("%Y%m")
        rows = list(self.client.iter_json_each_row(f"""
WITH
 complete_status AS
 (
  SELECT corpus,source_id,source_hash,labeling_version,updated_at_utc
  FROM `{self.database}`.`{STATUS_TABLE}` FINAL
  WHERE status='complete'
 ),
 recent_sec AS
 (
  SELECT
   accession_number source_id,
   cik source_cik,
   accepted_at_utc source_timestamp,
   inserted_at source_updated_at_utc
  FROM `{self.database}`.`sec_filing_v3` FINAL
  PREWHERE _partition_id >= {sql_string(start_partition)}
  WHERE accepted_at_utc >= toDateTime64({sql_string(start_sql)},6,'UTC')
 )
SELECT corpus,source_id,source_timestamp,source_cik
FROM
(
SELECT 'news' corpus,e.canonical_news_id source_id,
       toString(e.published_at_utc) source_timestamp,
       '' source_cik
FROM
(
 SELECT canonical_news_id,published_date,provider_article_id,title,
        source_revision_key,published_at_utc,updated_at_utc
 FROM `{self.database}`.`benzinga_news_event_v2` FINAL
 PREWHERE published_date >= toDate({sql_string(start_date_sql)})
 WHERE published_at_utc >= toDateTime64({sql_string(start_sql)},6,'UTC')
) e
LEFT JOIN
(
 SELECT published_date,provider_article_id,source_revision_key,
        rendered_text_hash,updated_at_utc
 FROM `{self.database}`.`benzinga_news_rendered_v2` FINAL
 PREWHERE published_date >= toDate({sql_string(start_date_sql)})
 WHERE published_at_utc >= toDateTime64({sql_string(start_sql)},6,'UTC')
) r
 ON r.published_date=e.published_date
 AND r.provider_article_id=e.provider_article_id
 AND r.source_revision_key=e.source_revision_key
LEFT JOIN complete_status s
 ON s.corpus='news' AND s.source_id=e.canonical_news_id
 AND s.source_hash=if(empty(r.rendered_text_hash),hex(SHA256(e.title)),r.rendered_text_hash)
 AND s.labeling_version={sql_string(NEWS_SYNTHESIS_ENGINE_VERSION)}
WHERE empty(s.source_id)
   OR s.source_hash != if(empty(r.rendered_text_hash),hex(SHA256(e.title)),r.rendered_text_hash)
   OR greatest(e.updated_at_utc,r.updated_at_utc) > s.updated_at_utc
UNION ALL
SELECT 'sec' corpus,f.source_id,toString(f.source_timestamp) source_timestamp,
       f.source_cik
FROM recent_sec f
LEFT JOIN complete_status s
 ON s.corpus='sec' AND s.source_id=f.source_id
 AND s.labeling_version={sql_string(SCOPED_LABELING_VERSION)}
WHERE empty(s.source_id)
   OR f.source_updated_at_utc > s.updated_at_utc
)
ORDER BY source_timestamp
LIMIT 5000
SETTINGS max_execution_time=25
FORMAT JSONEachRow
"""))
        return [TextDocumentNotice.model_validate(row) for row in rows]

    def _enqueue_live_forward(self, notice: TextDocumentNotice) -> None:
        identity = (notice.corpus, notice.source_id)
        if identity in self.pending:
            return
        self.pending.add(identity)
        try:
            self.queue.put_nowait(
                ScopedWorkItem(notice=notice, forward_current=True)
            )
        except asyncio.QueueFull:
            self.pending.discard(identity)
            raise

    def _unmodeled_live_news_notices(self) -> list[TextDocumentNotice]:
        started = _parse_utc(self.live_news.session.started_at_utc)
        if started is None:
            return []
        start_sql = started.strftime("%Y-%m-%d %H:%M:%S.%f")
        rows = list(self.client.iter_json_each_row(f"""
SELECT DISTINCT
 'news' corpus,l.canonical_news_id source_id,toString(l.published_at_utc) source_timestamp
FROM
(
 SELECT canonical_news_id,published_at_utc,arrayJoin(forecast_tickers) ticker,
        concat(canonical_news_id,':',ticker) unit_id
 FROM `{self.database}`.`{SYNTHESIS_TABLE}` FINAL
 WHERE engine_version={sql_string(NEWS_SYNTHESIS_ENGINE_VERSION)}
   AND published_at_utc >= toDateTime64({sql_string(start_sql)},6,'UTC')
) l
INNER JOIN
(
 SELECT e.canonical_news_id,if(empty(r.rendered_text_hash),hex(SHA256(e.title)),r.rendered_text_hash) rendered_text_hash
 FROM
 (
  SELECT canonical_news_id,published_date,provider_article_id,source_revision_key,title
  FROM `{self.database}`.`benzinga_news_event_v2` FINAL
 ) e
 LEFT JOIN
 (
  SELECT published_date,provider_article_id,source_revision_key,
         rendered_text_hash
  FROM `{self.database}`.`benzinga_news_rendered_v2` FINAL
 ) r
  ON r.published_date=e.published_date
  AND r.provider_article_id=e.provider_article_id
  AND r.source_revision_key=e.source_revision_key
) canonical
 ON canonical.canonical_news_id=l.source_id
LEFT JOIN
(
 SELECT canonical_news_id,ticker,unit_id,rendered_text_hash
 FROM `{self.database}`.`{self.live_news.table}` FINAL
 WHERE scoped_labeling_version={sql_string(NEWS_SYNTHESIS_ENGINE_VERSION)}
   AND published_at_utc >= toDateTime64({sql_string(start_sql)},6,'UTC')
) m
 ON m.canonical_news_id=l.source_id
 AND m.ticker=l.ticker
 AND m.unit_id=l.unit_id
 AND m.rendered_text_hash=canonical.rendered_text_hash
WHERE empty(m.canonical_news_id)
ORDER BY source_timestamp
LIMIT 500
FORMAT JSONEachRow
"""))
        return [TextDocumentNotice.model_validate(row) for row in rows]

    def _ensure_status_table(self) -> None:
        self.client.execute(f"""
CREATE TABLE IF NOT EXISTS `{self.database}`.`{STATUS_TABLE}` (
 corpus LowCardinality(String),
 source_id String,
 source_timestamp DateTime64(9,'UTC'),
 source_hash String,
 labeling_version LowCardinality(String),
 status LowCardinality(String),
 label_rows UInt32,
 relation_rows UInt32,
 error String,
 updated_at_utc DateTime64(6,'UTC')
) ENGINE=ReplacingMergeTree(updated_at_utc)
PARTITION BY corpus
ORDER BY (corpus,source_id,labeling_version)
""")


def _live_candidate(row: dict[str, Any]) -> LiveCandidate:
    return LiveCandidate(
        canonical_news_id=str(row["source_id"]),
        published_at_utc=str(row["source_timestamp"]),
        title=str(row.get("title") or ""),
        rendered_text=str(row.get("text") or ""),
        rendered_text_hash=str(row.get("rendered_text_hash") or ""),
        author=str(row.get("author") or ""),
        url_domain=str(row.get("url_domain") or ""),
        tickers=[str(value) for value in row.get("tickers") or []],
        channels=[str(value) for value in row.get("channels") or []],
        provider_tags=[str(value) for value in row.get("provider_tags") or []],
        links=[str(value) for value in row.get("links") or []],
        quality_flags=[str(value) for value in row.get("quality_flags") or []],
    )


def _authority_version(corpus: str) -> str:
    return NEWS_SYNTHESIS_ENGINE_VERSION if corpus == "news" else SCOPED_LABELING_VERSION


def _live_synthesis_labels(document: dict[str, Any]) -> tuple[SynthesisLiveLabel, ...]:
    entities = {str(row["entity_id"]): row for row in document["entities"]}
    statements = {str(row["statement_id"]): row for row in document["statements"]}
    participations: dict[str, list[dict[str, Any]]] = {}
    for row in document["participations"]:
        participations.setdefault(str(row["entity_id"]), []).append(row)
    eligibility = {
        (str(row["entity_id"]), str(row["product"])): bool(row["eligible"])
        for row in document["eligibility"]
    }
    event_tickers = tuple(
        str(row.get("ticker") or "") for row in document["entities"] if row.get("ticker")
    )
    output: list[SynthesisLiveLabel] = []
    for view in document["issuer_views"]:
        entity_id = str(view["entity_id"])
        entity = entities[entity_id]
        ticker = str(entity.get("ticker") or "")
        if not ticker:
            continue
        rows = participations.get(entity_id, [])
        statement_ids = [str(row["statement_id"]) for row in rows]
        concepts = sorted({str(statements[sid]["concept_leaf"]) for sid in statement_ids if sid in statements})
        quotes = [str(statements[sid]["evidence_spans"][0]["quote"]) for sid in statement_ids if sid in statements]
        role = next((str(row["semantic_role"]) for row in rows if row.get("semantic_role") != "none"), "affected_subject")
        output.append(SynthesisLiveLabel(
            ticker=ticker,
            unit_id=f"{document['source_id']}:{ticker}",
            event_id=str(document["source_id"]),
            event_tickers=event_tickers,
            issuer_role=role,
            evidence_scope="issuer_passages",
            semantic_evidence_text=" ".join(dict.fromkeys(quotes)),
            forecast_trigger_eligible=eligibility.get((entity_id, "forecast_trigger"), False),
            classification={
                "contract_version": document["contract_version"],
                "document_structure": document["envelope"]["document_structure"]["value"],
                "communication_purpose": document["envelope"]["communication_purpose"]["value"],
                "information_origin": document["envelope"]["information_origin"]["value"],
                "production_method": document["envelope"]["production_method"]["value"],
                "semantic_sentiment": view["composite_sentiment"],
                "positive_strength": view["positive_strength"],
                "negative_strength": view["negative_strength"],
                "concepts": concepts,
                "quality_flags": document["quality_flags"],
            },
            synthesis_version=NEWS_SYNTHESIS_ENGINE_VERSION,
        ))
    return tuple(output)


def _sec_document_set_hash(documents: list[dict[str, Any]]) -> str:
    """Hash the exact ordered rendered-document authority for one filing."""
    material = "|".join(
        f"{row['source_id']}:{row.get('text_sha256') or ''}"
        for row in sorted(documents, key=lambda item: str(item["source_id"]))
    )
    return hashlib.sha256(material.encode()).hexdigest()


def _ineligible_sec_hash(
    filing: dict[str, Any], metadata: list[dict[str, Any]]
) -> str:
    documents = "|".join(
        f"{row.get('document_id') or ''}:{row.get('document_type') or ''}"
        for row in sorted(metadata, key=lambda item: str(item.get("document_id") or ""))
    )
    material = "|".join(
        (
            "ineligible",
            str(filing.get("cik") or ""),
            str(filing.get("accession_number") or ""),
            str(filing.get("source_content_sha256") or ""),
            documents,
        )
    )
    return hashlib.sha256(material.encode()).hexdigest()


def _parse_utc(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return (
        parsed.replace(tzinfo=UTC)
        if parsed.tzinfo is None
        else parsed.astimezone(UTC)
    )
