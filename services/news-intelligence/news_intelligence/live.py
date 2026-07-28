from __future__ import annotations

import asyncio
import hashlib
import json
import os
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from research.mlops.clickhouse import (
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
    insert_json_each_row,
    sql_string,
)
from research.news_labeling.gpt_oss_v1.prompt import build_messages
from research.news_labeling.gpt_oss_v1.schema import TRANSPORT_SCHEMA, validate_label
from research.text_intelligence.scoped_labeling_v1.news_identity import (
    NewsIssuerResolver,
    load_news_issuer_resolver,
)
from research.text_intelligence.scoped_labeling_v1.pipeline import (
    classify_news_document,
)
from research.text_intelligence.scoped_labeling_v1.schema import (
    SCOPED_LABELING_VERSION,
    ScopedLabel,
)
from research.text_intelligence.semantic_label_authority_v1.schema import (
    SemanticDocument,
)
from services.market_hours import get_market_hours_client


class LiveCandidate(BaseModel):
    canonical_news_id: str
    published_at_utc: str
    title: str
    rendered_text: str
    rendered_text_hash: str = ""
    author: str = ""
    url_domain: str = ""
    tickers: list[str] = Field(default_factory=list)
    channels: list[str] = Field(default_factory=list)
    provider_tags: list[str] = Field(default_factory=list)
    links: list[str] = Field(default_factory=list)
    quality_flags: list[str] = Field(default_factory=list)


class LiveCandidateBatch(BaseModel):
    candidates: list[LiveCandidate] = Field(max_length=1000)


class LiveSessionUpdate(BaseModel):
    active: bool
    session_id: str = ""
    started_at_utc: str = ""


@dataclass
class LiveSession:
    active: bool = False
    session_id: str = ""
    started_at_utc: str = ""
    updated_at_utc: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


class LiveNewsRuntime:
    """Durable live semantic routing; raw/canonical news remains News Gateway-owned."""

    def __init__(self) -> None:
        self.model_gateway_url = os.environ.get(
            "NEWS_INTELLIGENCE_MODEL_GATEWAY_URL", "http://127.0.0.1:8802"
        ).rstrip("/")
        self.market_ai_url = os.environ.get(
            "NEWS_INTELLIGENCE_MARKET_AI_URL", "http://127.0.0.1:8803"
        ).rstrip("/")
        self.backend_url = os.environ.get(
            "NEWS_INTELLIGENCE_BACKEND_URL", "http://127.0.0.1:8000"
        ).rstrip("/")
        self.qmd_url = os.environ.get("NEWS_INTELLIGENCE_QMD_URL", "http://127.0.0.1:8795").rstrip("/")
        self.max_price = float(os.environ.get("NEWS_INTELLIGENCE_MAX_PRICE", "50"))
        self.allowed_kinds = {
            value.strip()
            for value in os.environ.get(
                "NEWS_INTELLIGENCE_ALLOWED_KINDS", "company,regulatory,analyst,editorial"
            ).split(",")
            if value.strip()
        }
        self.queue: asyncio.Queue[LiveCandidate | None] = asyncio.Queue(
            maxsize=int(os.environ.get("NEWS_INTELLIGENCE_QUEUE_MAX", "4096"))
        )
        self.workers: list[asyncio.Task[None]] = []
        self.reconcile_task: asyncio.Task[None] | None = None
        self.session_sync_task: asyncio.Task[None] | None = None
        self.pending_ids: set[str] = set()
        self.session = LiveSession()
        self.metrics = {
            "queued": 0, "processed": 0, "filtered": 0, "failed": 0,
            "reconciled": 0, "session_sync_failures": 0,
        }
        self.client = ClickHouseHttpClient(
            default_clickhouse_url(), default_clickhouse_user(), default_clickhouse_password(), timeout_seconds=15
        )
        self.database = os.environ.get("NEWS_INTELLIGENCE_DATABASE", "q_live")
        self.table = os.environ.get("NEWS_INTELLIGENCE_LABEL_TABLE", "news_semantic_label_v2")
        self.issuer_resolver: NewsIssuerResolver | None = None
        self.market_hours = get_market_hours_client("NEWS_INTELLIGENCE")

    async def start(self) -> None:
        await asyncio.to_thread(self._ensure_table)
        self.issuer_resolver = await asyncio.to_thread(
            load_news_issuer_resolver, self.client, self.database
        )
        count = max(1, int(os.environ.get("NEWS_INTELLIGENCE_WORKERS", "4")))
        self.workers = [asyncio.create_task(self._worker(i)) for i in range(count)]
        self.reconcile_task = asyncio.create_task(self._reconcile_loop())
        self.session_sync_task = asyncio.create_task(self._session_sync_loop())

    async def stop(self) -> None:
        if self.session_sync_task:
            self.session_sync_task.cancel()
            await asyncio.gather(self.session_sync_task, return_exceptions=True)
        if self.reconcile_task:
            self.reconcile_task.cancel()
            await asyncio.gather(self.reconcile_task, return_exceptions=True)
        for _ in self.workers:
            await self.queue.put(None)
        await self.queue.join()
        await asyncio.gather(*self.workers, return_exceptions=True)
        self.client.close()

    def update_session(self, update: LiveSessionUpdate) -> LiveSession:
        self.session = LiveSession(
            active=update.active,
            session_id=update.session_id,
            started_at_utc=update.started_at_utc,
            updated_at_utc=datetime.now(UTC).isoformat(),
        )
        return self.session

    async def _session_sync_loop(self) -> None:
        interval = max(
            2.0, float(os.environ.get("NEWS_INTELLIGENCE_SESSION_SYNC_SECONDS", "5"))
        )
        while True:
            try:
                status = await asyncio.to_thread(
                    _get_json,
                    f"{self.backend_url}/api/real-live-trading/market-gateway/status",
                    2.0,
                )
                self.update_session(
                    LiveSessionUpdate(
                        active=bool(status.get("running")),
                        session_id=str(status.get("trading_session_id") or ""),
                        started_at_utc=str(status.get("started_at_utc") or ""),
                    )
                )
            except Exception:
                # Live authorization is fail-closed. The next successful poll
                # restores an active session without requiring service restart.
                self.session.active = False
                self.session.updated_at_utc = datetime.now(UTC).isoformat()
                self.metrics["session_sync_failures"] += 1
            await asyncio.sleep(interval)

    def enqueue(self, candidate: LiveCandidate) -> None:
        if candidate.canonical_news_id in self.pending_ids:
            return
        self.pending_ids.add(candidate.canonical_news_id)
        try:
            self.queue.put_nowait(candidate)
        except asyncio.QueueFull:
            self.pending_ids.discard(candidate.canonical_news_id)
            raise
        self.metrics["queued"] += 1

    async def _worker(self, _index: int) -> None:
        while True:
            candidate = await self.queue.get()
            try:
                if candidate is None:
                    return
                await self._process(candidate)
            except Exception:
                self.metrics["failed"] += 1
            finally:
                if candidate is not None:
                    self.pending_ids.discard(candidate.canonical_news_id)
                self.queue.task_done()

    async def _process(self, candidate: LiveCandidate) -> None:
        market_clock = await asyncio.to_thread(self.market_hours.snapshot, datetime.now(UTC))
        if not self.session.active or not market_clock.active_collection_window:
            self.metrics["filtered"] += 1
            return
        if not candidate.rendered_text.strip():
            self.metrics["filtered"] += 1
            return
        document = SemanticDocument(
            corpus="news",
            source_id=candidate.canonical_news_id,
            timestamp=candidate.published_at_utc,
            title=candidate.title,
            text=candidate.rendered_text,
            entity_terms=tuple(candidate.tickers),
            tickers=tuple(value.upper() for value in candidate.tickers),
            metadata={
                "author": candidate.author,
                "url_domain": candidate.url_domain,
                "channels": candidate.channels,
                "provider_tags": candidate.provider_tags,
                "links": candidate.links,
                "quality_flags": candidate.quality_flags,
                "rendered_text_hash": candidate.rendered_text_hash,
            },
        )
        scoped_labels = classify_news_document(
            document,
            issuer_resolver=self.issuer_resolver,
        )
        eligible = [
            item for item in scoped_labels if item.forecast_trigger_eligible
        ]
        if not eligible:
            self.metrics["filtered"] += 1
            return
        for scoped in eligible:
            try:
                await self._process_scoped(
                    candidate, scoped, market_clock
                )
            except Exception:
                # One issuer in a shared event must not suppress another
                # issuer's independent semantic result.
                self.metrics["failed"] += 1

    async def _process_scoped(
        self,
        candidate: LiveCandidate,
        scoped: ScopedLabel,
        market_clock: Any,
    ) -> None:
        ticker = scoped.ticker.upper()
        if await asyncio.to_thread(
            self._already_labeled,
            candidate.canonical_news_id,
            ticker,
            scoped.unit_id,
            candidate.rendered_text_hash,
        ):
            return
        snapshot = await asyncio.to_thread(_get_json, f"{self.qmd_url}/snapshot/ticker/{ticker}", 1.5)
        price = float((snapshot or {}).get("last_price") or 0)
        if price <= 0 or price >= self.max_price:
            self.metrics["filtered"] += 1
            return
        deterministic = {
            **scoped.classification,
            "scoped_labeling_version": SCOPED_LABELING_VERSION,
            "target_ticker": ticker,
            "event_id": scoped.event_id,
            "event_tickers": list(scoped.event_tickers),
            "issuer_role": scoped.issuer_role,
            "evidence_scope": scoped.evidence_scope,
            "semantic_evidence_text": scoped.semantic_evidence_text,
        }
        row = candidate.model_dump()
        row["text"] = candidate.rendered_text
        article = {
            **row,
            "target_ticker": ticker,
            "issuer_scoped_semantic_evidence": scoped.semantic_evidence_text,
            "deterministic": {**deterministic, "point_in_time_price": price},
        }
        messages = build_messages(article)
        key_source = (
            f"{candidate.canonical_news_id}|{scoped.unit_id}|{ticker}|"
            f"{candidate.rendered_text_hash}|{SCOPED_LABELING_VERSION}|"
            "news-label-prompt-v1|news.semantic_fast.v1"
        )
        response = await asyncio.to_thread(
            _post_json,
            f"{self.model_gateway_url}/infer",
            {
                "route": "news.semantic_fast.v1",
                "idempotency_key": hashlib.sha256(key_source.encode()).hexdigest(),
                "messages": messages,
                "response_schema": TRANSPORT_SCHEMA,
                "metadata": {
                    "canonical_news_id": candidate.canonical_news_id,
                    "ticker": ticker,
                    "as_of_utc": candidate.published_at_utc,
                },
            },
            12.0,
        )
        label = response["result"]
        errors = validate_label(label, f"{candidate.title}\n{candidate.rendered_text}")
        if errors:
            raise ValueError("semantic label validation failed: " + "; ".join(errors[:5]))
        await asyncio.to_thread(
            self._persist,
            candidate,
            scoped,
            ticker,
            price,
            deterministic,
            label,
            response,
            snapshot,
            _json_safe(asdict(market_clock)),
        )
        self.metrics["processed"] += 1
        # Deep analysis is independent and may arrive later. Market AI owns its
        # expiry; this service never waits for it before persisting the fast label.
        asyncio.create_task(
            self._dispatch_market_ai(
                candidate=candidate,
                ticker=ticker,
                label=label,
                snapshot=snapshot,
                market_status=_json_safe(asdict(market_clock)),
            )
        )

    def _already_labeled(
        self,
        canonical_news_id: str,
        ticker: str,
        unit_id: str,
        rendered_text_hash: str,
    ) -> bool:
        sql = f"""
SELECT count()
FROM `{self.database}`.`{self.table}` FINAL
WHERE canonical_news_id={sql_string(canonical_news_id)}
  AND ticker={sql_string(ticker)}
  AND unit_id={sql_string(unit_id)}
  AND rendered_text_hash={sql_string(rendered_text_hash)}
  AND scoped_labeling_version={sql_string(SCOPED_LABELING_VERSION)}
"""
        return int(self.client.execute(sql).strip() or "0") > 0

    async def _dispatch_market_ai(
        self,
        *,
        candidate: LiveCandidate,
        ticker: str,
        label: dict[str, Any],
        snapshot: dict[str, Any],
        market_status: dict[str, Any],
    ) -> None:
        try:
            await asyncio.to_thread(
                _post_json,
                f"{self.market_ai_url}/hypothesize",
                {
                    "canonical_news_id": candidate.canonical_news_id,
                    "ticker": ticker,
                    "published_at_utc": candidate.published_at_utc,
                    "title": candidate.title,
                    "rendered_text": candidate.rendered_text,
                    "semantic_label": label,
                    "point_in_time_snapshot": snapshot,
                    "market_status": market_status,
                    "session_id": self.session.session_id,
                },
                2.0,
            )
        except Exception:
            # Fast semantic persistence is authoritative for this service.
            # Market AI separately reconciles/retries deep work by idempotency.
            return

    async def _reconcile_loop(self) -> None:
        interval = max(2.0, float(os.environ.get("NEWS_INTELLIGENCE_RECONCILE_SECONDS", "10")))
        while True:
            await asyncio.sleep(interval)
            if not self.session.active or not self.session.started_at_utc:
                continue
            try:
                candidates = await asyncio.to_thread(self._unlabeled_live_candidates)
                for candidate in candidates:
                    try:
                        self.enqueue(candidate)
                        self.metrics["reconciled"] += 1
                    except asyncio.QueueFull:
                        break
            except Exception:
                self.metrics["failed"] += 1

    def _unlabeled_live_candidates(self) -> list[LiveCandidate]:
        started = _parse_datetime(self.session.started_at_utc)
        if started is None:
            return []
        start_sql = started.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")
        sql = f"""
        SELECT e.canonical_news_id, toString(e.published_at_utc) AS published_at_utc,
               e.title, r.rendered_text, r.rendered_text_hash, e.author, e.url_domain,
               e.tickers, e.channels, e.provider_tags, e.links, r.quality_flags
        FROM `{self.database}`.`benzinga_news_event_v2` FINAL AS e
        INNER JOIN `{self.database}`.`benzinga_news_rendered_v2` FINAL AS r
          ON e.canonical_news_id=r.canonical_news_id
        LEFT JOIN
          (SELECT canonical_news_id,rendered_text_hash FROM `{self.database}`.`{self.table}` FINAL
           WHERE published_at_utc >= toDateTime64('{start_sql}', 6, 'UTC')) AS l
          ON e.canonical_news_id=l.canonical_news_id
         AND r.rendered_text_hash=l.rendered_text_hash
        WHERE e.published_at_utc >= toDateTime64('{start_sql}', 6, 'UTC')
          AND l.canonical_news_id=''
        ORDER BY e.published_at_utc
        LIMIT 500
        FORMAT JSONEachRow
        """
        return [
            LiveCandidate.model_validate(json.loads(line))
            for line in self.client.execute(sql).splitlines()
            if line.strip()
        ]

    def _ensure_table(self) -> None:
        self.client.execute(
            f"""CREATE TABLE IF NOT EXISTS `{self.database}`.`{self.table}` (
            canonical_news_id String, ticker LowCardinality(String),
            published_at_utc DateTime64(9,'UTC'), rendered_text_hash String,
            unit_id String, event_id String, event_tickers Array(LowCardinality(String)),
            issuer_role LowCardinality(String), evidence_scope LowCardinality(String),
            semantic_evidence_text String,
            scoped_labeling_version LowCardinality(String),
            deterministic_version LowCardinality(String), deterministic_json String,
            semantic_contract LowCardinality(String), semantic_json String,
            point_in_time_price Float64, provider LowCardinality(String), model String,
            input_tokens UInt32, output_tokens UInt32, cost_usd Float64,
            latency_ms UInt32, session_id String, created_at_utc DateTime64(6,'UTC')
            ) ENGINE=ReplacingMergeTree(created_at_utc)
            PARTITION BY toYYYYMM(published_at_utc)
            ORDER BY (canonical_news_id,ticker,unit_id,semantic_contract)"""
        )
        self.client.execute(
            f"ALTER TABLE `{self.database}`.`{self.table}` "
            "ADD COLUMN IF NOT EXISTS qmd_snapshot_json String AFTER point_in_time_price"
        )
        self.client.execute(
            f"ALTER TABLE `{self.database}`.`{self.table}` "
            "ADD COLUMN IF NOT EXISTS market_status_json String AFTER qmd_snapshot_json"
        )

    def _persist(
        self,
        candidate: LiveCandidate,
        scoped: ScopedLabel,
        ticker: str,
        price: float,
        deterministic: dict[str, Any],
        label: dict[str, Any],
        response: dict[str, Any],
        snapshot: dict[str, Any],
        market_status: dict[str, Any],
    ) -> None:
        row = {
            "canonical_news_id": candidate.canonical_news_id,
            "ticker": ticker,
            "published_at_utc": candidate.published_at_utc,
            "rendered_text_hash": candidate.rendered_text_hash,
            "unit_id": scoped.unit_id,
            "event_id": scoped.event_id,
            "event_tickers": list(scoped.event_tickers),
            "issuer_role": scoped.issuer_role,
            "evidence_scope": scoped.evidence_scope,
            "semantic_evidence_text": scoped.semantic_evidence_text,
            "scoped_labeling_version": SCOPED_LABELING_VERSION,
            "deterministic_version": SCOPED_LABELING_VERSION,
            "deterministic_json": json.dumps(deterministic, separators=(",", ":")),
            "semantic_contract": "gpt_oss_news_semantics_v1",
            "semantic_json": json.dumps(label, separators=(",", ":")),
            "point_in_time_price": price,
            "qmd_snapshot_json": json.dumps(snapshot, separators=(",", ":"), default=str),
            "market_status_json": json.dumps(market_status, separators=(",", ":"), default=str),
            "provider": response.get("provider", ""),
            "model": response.get("model", ""),
            "input_tokens": response.get("input_tokens", 0),
            "output_tokens": response.get("output_tokens", 0),
            "cost_usd": response.get("cost_usd", 0),
            "latency_ms": response.get("latency_ms", 0),
            "session_id": self.session.session_id,
            "created_at_utc": _clickhouse_ts(datetime.now(UTC)),
        }
        insert_json_each_row(self.client, self.database, self.table, list(row), [row])


def _get_json(url: str, timeout: float) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode())


def _post_json(url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"{url} HTTP {exc.code}: {exc.read().decode(errors='replace')[:500]}") from exc


def _parse_datetime(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _clickhouse_ts(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")




def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))
