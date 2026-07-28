from __future__ import annotations

import asyncio
import hashlib
import json
import os
import urllib.request
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from research.mlops.env import discover_env_files, load_env_files
from research.mlops.clickhouse import (
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
    insert_json_each_row,
)
from research.news_labeling.trade_hypothesis_v2.contract import (
    CONTRACT_VERSION,
    HYPOTHESIS_SCHEMA,
    build_messages,
    validate_hypothesis,
)


load_env_files(
    discover_env_files(Path(__file__).resolve().parents[4]),
    verbose=False,
)


class HypothesisRequest(BaseModel):
    canonical_news_id: str
    ticker: str
    published_at_utc: str
    title: str
    rendered_text: str
    semantic_label: dict[str, Any]
    point_in_time_snapshot: dict[str, Any] = Field(default_factory=dict)
    market_status: dict[str, Any] = Field(default_factory=dict)
    sec_context: list[dict[str, Any]] = Field(default_factory=list)
    fundamental_context: dict[str, Any] = Field(default_factory=dict)
    prior_news: list[dict[str, Any]] = Field(default_factory=list)
    session_id: str = ""


class ContextualMarketAi:
    def __init__(self) -> None:
        self.model_gateway_url = os.environ.get("MARKET_AI_MODEL_GATEWAY_URL", "http://127.0.0.1:8802").rstrip("/")
        self.qmd_url = os.environ.get("MARKET_AI_QMD_URL", "http://127.0.0.1:8795").rstrip("/")
        self.backend_url = os.environ.get("MARKET_AI_BACKEND_URL", "").rstrip("/")
        self.expiry_seconds = int(os.environ.get("MARKET_AI_HYPOTHESIS_EXPIRY_SECONDS", "120"))
        self.queue: asyncio.Queue[HypothesisRequest | None] = asyncio.Queue(
            maxsize=int(os.environ.get("MARKET_AI_QUEUE_MAX", "2048"))
        )
        self.workers: list[asyncio.Task[None]] = []
        self.reconcile_task: asyncio.Task[None] | None = None
        self.pending_keys: set[tuple[str, str]] = set()
        self.metrics = {"queued": 0, "completed": 0, "failed": 0}
        self.client = ClickHouseHttpClient(
            default_clickhouse_url(), default_clickhouse_user(), default_clickhouse_password(), timeout_seconds=20
        )
        self.database = os.environ.get("MARKET_AI_DATABASE", "q_live")
        self.table = os.environ.get("MARKET_AI_HYPOTHESIS_TABLE", "news_market_hypothesis_v1")

    async def start(self) -> None:
        await asyncio.to_thread(self._ensure_table)
        self.workers = [
            asyncio.create_task(self._worker())
            for _ in range(max(1, int(os.environ.get("MARKET_AI_WORKERS", "2"))))
        ]
        self.reconcile_task = asyncio.create_task(self._reconcile_loop())

    async def stop(self) -> None:
        if self.reconcile_task:
            self.reconcile_task.cancel()
            await asyncio.gather(self.reconcile_task, return_exceptions=True)
        for _ in self.workers:
            await self.queue.put(None)
        await self.queue.join()
        await asyncio.gather(*self.workers, return_exceptions=True)
        self.client.close()

    def enqueue(self, request: HypothesisRequest) -> None:
        key = (request.canonical_news_id, request.ticker.upper())
        if key in self.pending_keys:
            return
        self.pending_keys.add(key)
        try:
            self.queue.put_nowait(request)
        except asyncio.QueueFull:
            self.pending_keys.discard(key)
            raise
        self.metrics["queued"] += 1

    async def _worker(self) -> None:
        while True:
            item = await self.queue.get()
            try:
                if item is None:
                    return
                await self._process(item)
            except Exception:
                self.metrics["failed"] += 1
            finally:
                if item is not None:
                    self.pending_keys.discard((item.canonical_news_id, item.ticker.upper()))
                self.queue.task_done()

    async def _process(self, item: HypothesisRequest) -> None:
        context = await self._freeze_context(item)
        context_hash = hashlib.sha256(
            json.dumps(context, sort_keys=True, separators=(",", ":"), default=str).encode()
        ).hexdigest()
        payload = {
            "route": "news.trade_hypothesis.v2",
            "idempotency_key": hashlib.sha256(
                f"{item.canonical_news_id}|{item.ticker}|{context_hash}|hypothesis-v2".encode()
            ).hexdigest(),
            "messages": build_messages(context),
            "response_schema": HYPOTHESIS_SCHEMA,
            "metadata": {
                "canonical_news_id": item.canonical_news_id,
                "ticker": item.ticker,
                "as_of_utc": item.published_at_utc,
                "context_hash": context_hash,
            },
        }
        response = await asyncio.to_thread(_post_json, f"{self.model_gateway_url}/infer", payload, 35.0)
        result = response["result"]
        validate_hypothesis(result)
        now = datetime.now(UTC)
        row = {
            "canonical_news_id": item.canonical_news_id,
            "ticker": item.ticker.upper(),
            "published_at_utc": item.published_at_utc,
            "context_as_of_utc": _clickhouse_ts(
                datetime.fromisoformat(context["context_as_of_utc"].replace("Z", "+00:00"))
            ),
            "context_hash": context_hash,
            "contract_version": CONTRACT_VERSION,
            "hypothesis_json": json.dumps(result, separators=(",", ":")),
            "provider": response.get("provider", ""),
            "model": response.get("model", ""),
            "cost_usd": response.get("cost_usd", 0),
            "latency_ms": response.get("latency_ms", 0),
            "session_id": item.session_id,
            "expires_at_utc": _clickhouse_ts(now + timedelta(seconds=self.expiry_seconds)),
            "created_at_utc": _clickhouse_ts(now),
        }
        await asyncio.to_thread(insert_json_each_row, self.client, self.database, self.table, list(row), [row])
        self.metrics["completed"] += 1

    async def _reconcile_loop(self) -> None:
        interval = max(3.0, float(os.environ.get("MARKET_AI_RECONCILE_SECONDS", "10")))
        while True:
            await asyncio.sleep(interval)
            try:
                for request in await asyncio.to_thread(self._pending_live_hypotheses):
                    try:
                        self.enqueue(request)
                    except asyncio.QueueFull:
                        break
            except Exception:
                self.metrics["failed"] += 1

    def _pending_live_hypotheses(self) -> list[HypothesisRequest]:
        sql = f"""
        SELECT l.canonical_news_id, l.ticker, toString(l.published_at_utc) AS published_at_utc,
               e.title, r.rendered_text, l.semantic_json, l.qmd_snapshot_json,
               l.market_status_json, l.session_id
        FROM `{self.database}`.`news_semantic_label_v2` FINAL AS l
        INNER JOIN `{self.database}`.`benzinga_news_event_v2` FINAL AS e
          ON l.canonical_news_id=e.canonical_news_id
        INNER JOIN `{self.database}`.`benzinga_news_rendered_v2` FINAL AS r
          ON l.canonical_news_id=r.canonical_news_id
         AND l.rendered_text_hash=r.rendered_text_hash
        LEFT JOIN
          (SELECT canonical_news_id,ticker FROM `{self.database}`.`{self.table}` FINAL
           WHERE created_at_utc >= now64(6)-INTERVAL {self.expiry_seconds} SECOND) AS h
          ON l.canonical_news_id=h.canonical_news_id AND l.ticker=h.ticker
        WHERE l.created_at_utc >= now64(6)-INTERVAL {self.expiry_seconds} SECOND
          AND h.canonical_news_id=''
        ORDER BY l.created_at_utc
        LIMIT 200
        FORMAT JSONEachRow
        """
        rows: list[HypothesisRequest] = []
        for line in self.client.execute(sql).splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            rows.append(
                HypothesisRequest(
                    canonical_news_id=row["canonical_news_id"],
                    ticker=row["ticker"],
                    published_at_utc=row["published_at_utc"],
                    title=row["title"],
                    rendered_text=row["rendered_text"],
                    semantic_label=json.loads(row["semantic_json"]),
                    point_in_time_snapshot=json.loads(row.get("qmd_snapshot_json") or "{}"),
                    market_status=json.loads(row.get("market_status_json") or "{}"),
                    session_id=row.get("session_id", ""),
                )
            )
        return rows

    async def _freeze_context(self, item: HypothesisRequest) -> dict[str, Any]:
        ticker = item.ticker.upper()
        snapshot_task = asyncio.create_task(self._market_snapshot(item, ticker))
        fundamentals_task = asyncio.create_task(self._fundamentals(item, ticker))
        sec_task = asyncio.create_task(self._sec_context(item, ticker))
        snapshot, fundamentals, sec_context = await asyncio.gather(
            snapshot_task, fundamentals_task, sec_task
        )
        market_status = dict(item.market_status)
        if not market_status:
            from services.market_hours import get_market_hours_client

            status = await asyncio.to_thread(
                get_market_hours_client("MARKET_AI").snapshot, datetime.now(UTC)
            )
            raw_status = asdict(status) if is_dataclass(status) else dict(status)
            market_status = _json_safe(raw_status)
        return {
            "contract": "frozen_market_context_v2",
            "context_as_of_utc": str(
                snapshot.get("last_event_ts") or item.published_at_utc
            ),
            "news_as_of_utc": item.published_at_utc,
            "ticker": ticker,
            "title": item.title,
            "rendered_text": item.rendered_text,
            "semantic_label": item.semantic_label,
            "qmd_snapshot": snapshot,
            "market_status": market_status,
            "sec_context": sec_context,
            "fundamental_context": fundamentals,
            "prior_news": list(item.prior_news[:3])
            if item.prior_news
            else await asyncio.to_thread(
                self._prior_news_context,
                item.canonical_news_id,
                ticker,
                item.published_at_utc,
            ),
        }

    async def _market_snapshot(
        self, item: HypothesisRequest, ticker: str
    ) -> dict[str, Any]:
        if item.point_in_time_snapshot:
            return dict(item.point_in_time_snapshot)
        return await asyncio.to_thread(
            _get_json, f"{self.qmd_url}/snapshot/ticker/{ticker}", 2.0
        )

    async def _fundamentals(
        self, item: HypothesisRequest, ticker: str
    ) -> dict[str, Any]:
        if item.fundamental_context:
            return dict(item.fundamental_context)
        if self.backend_url:
            return await asyncio.to_thread(
                _get_json,
                f"{self.backend_url}/api/trading/ticker-facts/{ticker}?as_of={item.published_at_utc}",
                5.0,
            )
        # Reuse the app's point-in-time facts authority directly when
        # co-located, avoiding a second implementation and port coupling.
        from src.backend.ticker_facts_service import ticker_facts_payload

        return await asyncio.to_thread(
            ticker_facts_payload, ticker, as_of=item.published_at_utc
        )

    async def _sec_context(
        self, item: HypothesisRequest, ticker: str
    ) -> list[dict[str, Any]]:
        if item.sec_context:
            return list(item.sec_context[:12])
        from src.backend.sec_canvas_service import sec_filings_payload

        payload = await asyncio.to_thread(
            sec_filings_payload,
            as_of=item.published_at_utc,
            ticker=ticker,
            limit=5,
            lookback_hours=24 * 366,
        )
        return list(payload.get("rows") or [])[:5]

    def _ensure_table(self) -> None:
        self.client.execute(
            f"""CREATE TABLE IF NOT EXISTS `{self.database}`.`{self.table}` (
            canonical_news_id String, ticker LowCardinality(String),
            published_at_utc DateTime64(9,'UTC'), context_as_of_utc DateTime64(6,'UTC'),
            context_hash String, contract_version LowCardinality(String),
            hypothesis_json String, provider LowCardinality(String), model String,
            cost_usd Float64, latency_ms UInt32, session_id String,
            expires_at_utc DateTime64(6,'UTC'), created_at_utc DateTime64(6,'UTC')
            ) ENGINE=ReplacingMergeTree(created_at_utc)
            PARTITION BY toYYYYMM(published_at_utc)
            ORDER BY (canonical_news_id,ticker,contract_version)"""
        )

    def _prior_news_context(
        self, canonical_news_id: str, ticker: str, published_at_utc: str
    ) -> list[dict[str, Any]]:
        from src.backend.news_prior_context import prior_news_context

        return prior_news_context(
            self.client,
            canonical_news_id=canonical_news_id,
            ticker=ticker,
            as_of_utc=published_at_utc,
            limit=3,
            database=self.database,
            include_semantic=True,
        )


def _get_json(url: str, timeout: float) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode())


def _post_json(url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, default=str).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode())


def _clickhouse_ts(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))
