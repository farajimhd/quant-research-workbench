from __future__ import annotations

import asyncio
import hashlib
import json
import os
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field

from research.mlops.clickhouse import (
    ClickHouseHttpClient,
    insert_json_each_row,
    sql_string,
)
from research.text_intelligence.scoped_labeling_v1.news_identity import (
    NewsIssuerResolver,
    load_news_issuer_resolver,
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
from research.text_intelligence.scoped_labeling_v1.pipeline import (
    classify_news_document,
    classify_sec_document,
)
from research.text_intelligence.scoped_labeling_v1.schema import (
    SCOPED_LABELING_VERSION,
    ScopedLabel,
)

from .live import LiveCandidate, LiveNewsRuntime, PreparedNewsCandidate


STATUS_TABLE = "scoped_text_live_status_v2"


class TextDocumentNotice(BaseModel):
    corpus: Literal["news", "sec"]
    source_id: str = Field(min_length=1, max_length=256)
    source_timestamp: str = ""


class TextDocumentNoticeBatch(BaseModel):
    documents: list[TextDocumentNotice] = Field(min_length=1, max_length=2_000)


@dataclass(frozen=True, slots=True)
class LoadedSource:
    notice: TextDocumentNotice
    rows: tuple[dict[str, Any], ...]
    source_hash: str


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
        self.issuer_resolver: NewsIssuerResolver | None = None
        self.sec_mappings: dict[str, list[dict[str, Any]]] = {}
        self.sec_mapping_lock = threading.Lock()
        self.metrics: dict[str, int | str] = {
            "deterministic_queued": 0,
            "deterministic_completed": 0,
            "deterministic_skipped_current": 0,
            "deterministic_failed": 0,
            "deterministic_news_labels": 0,
            "deterministic_sec_labels": 0,
            "deterministic_reconciled": 0,
            "deterministic_live_forwarded": 0,
            "deterministic_live_forward_failed": 0,
            "deterministic_last_error": "",
        }

    async def start(self) -> None:
        await asyncio.to_thread(create_tables, self.client, self.database)
        await asyncio.to_thread(self._ensure_status_table)
        self.issuer_resolver = await asyncio.to_thread(
            load_news_issuer_resolver, self.client, self.database
        )
        count = max(1, min(16, int(os.environ.get("TEXT_INTELLIGENCE_WORKERS", "4"))))
        self.workers = [
            asyncio.create_task(self._worker(index), name=f"text-intelligence-{index}")
            for index in range(count)
        ]
        self.reconcile_task = asyncio.create_task(
            self._reconcile_loop(), name="text-intelligence-reconcile"
        )

    async def stop(self) -> None:
        if self.reconcile_task:
            self.reconcile_task.cancel()
            await asyncio.gather(self.reconcile_task, return_exceptions=True)
        for _ in self.workers:
            await self.queue.put(None)
        await self.queue.join()
        await asyncio.gather(*self.workers, return_exceptions=True)

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

    async def _worker(self, _index: int) -> None:
        while True:
            item = await self.queue.get()
            try:
                if item is None:
                    return
                await asyncio.to_thread(
                    self._process_notice,
                    item.notice,
                    item.forward_current,
                )
            except Exception as exc:  # noqa: BLE001
                self.metrics["deterministic_failed"] = int(
                    self.metrics["deterministic_failed"]
                ) + 1
                self.metrics["deterministic_last_error"] = (
                    f"{type(exc).__name__}: {exc}"
                )[:500]
                if item is not None:
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
                if item is not None:
                    self.pending.discard(
                        (item.notice.corpus, item.notice.source_id)
                    )
                self.queue.task_done()

    def _process_notice(
        self, notice: TextDocumentNotice, forward_current: bool = False
    ) -> None:
        loaded = self._load_source(notice)
        if not loaded.rows:
            raise RuntimeError(
                f"canonical {notice.corpus} source is not ready: {notice.source_id}"
            )
        source_is_current = self._status_is_current(notice, loaded.source_hash)
        if source_is_current and not forward_current:
            self.metrics["deterministic_skipped_current"] = int(
                self.metrics["deterministic_skipped_current"]
            ) + 1
            return
        run_id = f"live-{uuid.uuid4().hex}"
        label_rows: list[dict[str, Any]] = []
        relation_rows: list[dict[str, Any]] = []
        prepared_news: list[PreparedNewsCandidate] = []
        for source_row in loaded.rows:
            document = row_to_document(source_row, notice.corpus)
            labels = (
                classify_news_document(
                    document, issuer_resolver=self.issuer_resolver
                )
                if notice.corpus == "news"
                else classify_sec_document(document)
            )
            label_rows.extend(
                persistence_row(document, label, run_id) for label in labels
            )
            for label in labels:
                relation_rows.extend(relationship_rows(document, label, run_id))
            if notice.corpus == "news" and self.live_news.enabled:
                prepared_news.append(
                    PreparedNewsCandidate(
                        candidate=_live_candidate(source_row),
                        scoped_labels=labels,
                    )
                )
        if label_rows and not source_is_current:
            insert_json_each_row(
                self.client,
                self.database,
                TARGET_TABLE,
                list(label_rows[0]),
                label_rows,
            )
        if relation_rows and not source_is_current:
            insert_json_each_row(
                self.client,
                self.database,
                RELATION_TABLE,
                list(relation_rows[0]),
                relation_rows,
            )
        if not source_is_current:
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

    def _load_source(self, notice: TextDocumentNotice) -> LoadedSource:
        return (
            self._load_news(notice)
            if notice.corpus == "news"
            else self._load_sec(notice)
        )

    def _load_news(self, notice: TextDocumentNotice) -> LoadedSource:
        rows = list(self.client.iter_json_each_row(f"""
SELECT
 e.canonical_news_id AS source_id,
 toString(e.published_at_utc) AS source_timestamp,
 e.title, r.rendered_text AS text, e.tickers AS entity_terms, e.tickers,
 e.channels, e.provider_tags, e.links, e.author, e.url_domain, e.article_url,
 e.content_quality_flags, r.renderer_version, r.text_contract,
 r.quality_flags, r.rendered_text_hash
FROM `{self.database}`.`benzinga_news_event_v2` AS e FINAL
INNER JOIN `{self.database}`.`benzinga_news_rendered_v2` AS r FINAL
 ON r.published_date=e.published_date
 AND r.provider_article_id=e.provider_article_id
 AND r.source_revision_key=e.source_revision_key
WHERE e.canonical_news_id={sql_string(notice.source_id)}
  AND notEmpty(r.rendered_text)
LIMIT 1
FORMAT JSONEachRow
"""))
        source_hash = str(rows[0].get("rendered_text_hash") or "") if rows else ""
        return LoadedSource(notice, tuple(rows), source_hash)

    def _load_sec(self, notice: TextDocumentNotice) -> LoadedSource:
        filings = list(self.client.iter_json_each_row(f"""
SELECT filing_id,cik,accession_number,
       cityHash64(cik) % 64 AS document_partition,
       toString(accepted_at_utc) source_timestamp,
       ifNull(company_name,'') company_name,ifNull(form_type,'') form_type,
       ifNull(items,'') filing_items,ifNull(toString(filing_date),'') filing_date,
       ifNull(toString(report_date),'') report_date,accepted_at_source
FROM `{self.database}`.`sec_filing_v3` FINAL
WHERE accession_number={sql_string(notice.source_id)}
LIMIT 1
FORMAT JSONEachRow
"""))
        if not filings:
            return LoadedSource(notice, (), "")
        filing = filings[0]
        cik = str(filing["cik"])
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
                partition=int(filing["document_partition"]),
            )
        )
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
        value = self.client.execute(f"""
SELECT count()
FROM `{self.database}`.`{STATUS_TABLE}` FINAL
WHERE corpus={sql_string(notice.corpus)}
  AND source_id={sql_string(notice.source_id)}
  AND source_hash={sql_string(source_hash)}
  AND labeling_version={sql_string(SCOPED_LABELING_VERSION)}
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
            "labeling_version": SCOPED_LABELING_VERSION,
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
            try:
                notices = await asyncio.to_thread(self._recent_notices)
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
            await asyncio.sleep(interval)

    def _recent_notices(self) -> list[TextDocumentNotice]:
        hours = max(
            1, min(24 * 30, int(os.environ.get("TEXT_INTELLIGENCE_RECONCILE_HOURS", "72")))
        )
        start = datetime.now(UTC) - timedelta(hours=hours)
        start_sql = start.strftime("%Y-%m-%d %H:%M:%S.%f")
        rows = list(self.client.iter_json_each_row(f"""
WITH
 complete_status AS
 (
  SELECT corpus,source_id,source_hash
  FROM `{self.database}`.`{STATUS_TABLE}` FINAL
  WHERE labeling_version={sql_string(SCOPED_LABELING_VERSION)}
    AND status='complete'
 ),
 recent_sec AS
 (
  SELECT
   f.accession_number source_id,
   f.accepted_at_utc source_timestamp,
   lower(hex(SHA256(arrayStringConcat(
    arrayMap(
     item -> concat(item.1, ':', item.2),
     arraySort(groupArray((r.document_id,r.text_sha256)))
    ),
    '|'
   )))) source_hash
  FROM
  (
   SELECT accession_number,accepted_at_utc
   FROM `{self.database}`.`sec_filing_v3` FINAL
   WHERE accepted_at_utc >= toDateTime64({sql_string(start_sql)},6,'UTC')
  ) f
  INNER JOIN
  (
   SELECT accession_number,document_id,text_sha256
   FROM `{self.database}`.`sec_filing_text_rendered_v3` FINAL
   WHERE notEmpty(text)
  ) r ON r.accession_number=f.accession_number
  GROUP BY f.accession_number,f.accepted_at_utc
 )
SELECT 'news' corpus,e.canonical_news_id source_id,
       toString(e.published_at_utc) source_timestamp
FROM
(
 SELECT canonical_news_id,published_date,provider_article_id,
        source_revision_key,published_at_utc
 FROM `{self.database}`.`benzinga_news_event_v2` FINAL
 WHERE published_at_utc >= toDateTime64({sql_string(start_sql)},6,'UTC')
) e
INNER JOIN
(
 SELECT published_date,provider_article_id,source_revision_key,
        rendered_text_hash
 FROM `{self.database}`.`benzinga_news_rendered_v2` FINAL
 WHERE notEmpty(rendered_text)
) r
 ON r.published_date=e.published_date
 AND r.provider_article_id=e.provider_article_id
 AND r.source_revision_key=e.source_revision_key
LEFT JOIN complete_status s
 ON s.corpus='news' AND s.source_id=e.canonical_news_id
 AND s.source_hash=r.rendered_text_hash
WHERE empty(s.source_id)
UNION ALL
SELECT 'sec' corpus,f.source_id,toString(f.source_timestamp) source_timestamp
FROM recent_sec f
LEFT JOIN complete_status s
 ON s.corpus='sec' AND s.source_id=f.source_id
 AND s.source_hash=f.source_hash
WHERE empty(s.source_id)
ORDER BY source_timestamp
LIMIT 5000
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
 'news' corpus,l.source_id,toString(l.source_timestamp) source_timestamp
FROM
(
 SELECT source_id,source_timestamp,ticker,unit_id
 FROM `{self.database}`.`{TARGET_TABLE}` FINAL
 WHERE corpus='news'
   AND labeling_version={sql_string(SCOPED_LABELING_VERSION)}
   AND forecast_trigger_eligible=1
   AND source_timestamp >= toDateTime64({sql_string(start_sql)},6,'UTC')
) l
INNER JOIN
(
 SELECT e.canonical_news_id,r.rendered_text_hash
 FROM
 (
  SELECT canonical_news_id,published_date,provider_article_id,source_revision_key
  FROM `{self.database}`.`benzinga_news_event_v2` FINAL
 ) e
 INNER JOIN
 (
  SELECT published_date,provider_article_id,source_revision_key,
         rendered_text_hash
  FROM `{self.database}`.`benzinga_news_rendered_v2` FINAL
  WHERE notEmpty(rendered_text)
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
 WHERE scoped_labeling_version={sql_string(SCOPED_LABELING_VERSION)}
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


def _sec_document_set_hash(documents: list[dict[str, Any]]) -> str:
    """Hash the exact ordered rendered-document authority for one filing."""
    material = "|".join(
        f"{row['source_id']}:{row.get('text_sha256') or ''}"
        for row in sorted(documents, key=lambda item: str(item["source_id"]))
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
