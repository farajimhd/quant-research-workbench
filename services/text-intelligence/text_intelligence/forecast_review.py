from __future__ import annotations

import asyncio
import hashlib
import json
import re
import urllib.request
import urllib.error
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from pydantic import BaseModel, Field

from research.mlops.clickhouse import ClickHouseHttpClient, insert_json_each_row, sql_string
from research.text_intelligence.llm_issuer_labeling_v3.prompt import build_messages
from research.text_intelligence.llm_issuer_labeling_v3.schema import (
    SCHEMA_VERSION,
    TRANSPORT_SCHEMA,
    canonicalize_output,
    validate_output,
)
from research.text_intelligence.news_synthesis_v1.deepfm_serving import DeepFMServingRelease

from .config import IntelligenceConfig


FUNNEL_TABLE = "news_forecast_funnel_v1"
REVIEW_TABLE = "news_llm_issuer_review_v1"
REVIEW_HISTORY_TABLE = "news_llm_issuer_review_history_v1"
FUNNEL_CONTRACT = "news_forecast_funnel_serving_v1"
REVIEW_CONTRACT = "news_llm_issuer_review_serving_v1"
PROMPT_VERSION = "news_issuer_review_prompt_v1_gold_examples"
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")


class ReviewRequest(BaseModel):
    canonical_news_id: str
    published_at_utc: str
    requested_by: str = "operator"
    force: bool = False


class ReviewBatch(BaseModel):
    requests: list[ReviewRequest] = Field(min_length=1, max_length=25)


class ReactionRequest(BaseModel):
    canonical_news_id: str
    published_at_utc: str
    requested_by: str = "operator"
    ticker: str = ""


class ReviewWork(BaseModel):
    request: ReviewRequest
    trigger_mode: str


class ForecastReviewRuntime:
    def __init__(self, config: IntelligenceConfig, client: ClickHouseHttpClient, database: str) -> None:
        self.config = config
        self.client = client
        self.database = database
        if config.review_trigger_mode not in {"manual", "automatic"}:
            raise ValueError("TEXT_INTELLIGENCE_REVIEW_TRIGGER_MODE must be manual or automatic")
        self.trigger_mode = config.review_trigger_mode
        self.release: DeepFMServingRelease | None = None
        self.system_prompt = ""
        self.queue: asyncio.Queue[ReviewWork | None] = asyncio.Queue(maxsize=512)
        self.worker: asyncio.Task[None] | None = None
        self.pending: set[str] = set()
        self.metrics = {
            "funnel_scored": 0,
            "review_queued": 0,
            "review_completed": 0,
            "review_failed": 0,
            "reaction_queued": 0,
            "reaction_failed": 0,
            "hypothesis_enqueue_failed": 0,
            "last_error": "",
        }

    async def start(self) -> None:
        await asyncio.to_thread(self._ensure_tables)
        if self.config.forecast_funnel_enabled:
            self.release = await asyncio.to_thread(
                DeepFMServingRelease,
                self.config.forecast_release_manifest,
                device=self.config.forecast_model_device,
            )
        self.system_prompt = await asyncio.to_thread(
            _load_system_prompt, self.config.review_prompt_path
        )
        self.worker = asyncio.create_task(self._worker(), name="news-forecast-review")

    async def stop(self) -> None:
        if self.worker is None:
            return
        await self.queue.put(None)
        await self.queue.join()
        await self.worker
        self.worker = None

    def process_funnel(self, source_row: Mapping[str, Any], deterministic: Mapping[str, Any]) -> dict[str, Any]:
        """Score every canonical article with DeepFM.

        News Synthesis remains persisted reading context.  Its product-suitability
        opinion is deliberately not consulted by this live decision authority.
        """
        if self.release is None:
            raise RuntimeError("DeepFM serving release is unavailable")
        scored = self.release.score(
            source_row,
            ticker_history=self._ticker_history(source_row),
            market_cap=self._market_cap_context(source_row),
            threshold=self.config.forecast_eligibility_threshold,
        )
        result = {
            "stage": "deepfm_eligible" if scored["forecast_eligibility"] == "eligible" else "deepfm_filtered",
            "forecast_eligibility": scored["forecast_eligibility"],
            "eligible_probability": scored["eligible_probability"],
            "threshold": scored["threshold"],
            "model_release_id": scored["release_id"],
            "model_release_hash": scored["release_hash"],
        }
        now = _timestamp()
        row = {
            "canonical_news_id": str(source_row["source_id"]),
            "published_at_utc": _clickhouse_timestamp(source_row["source_timestamp"]),
            "rendered_text_hash": str(source_row.get("rendered_text_hash") or ""),
            "contract_version": FUNNEL_CONTRACT,
            "deterministic_engine_version": str(
                ((deterministic.get("production") or {}).get("engine_version")) or ""
            ),
            **result,
            "created_at_utc": now,
        }
        insert_json_each_row(self.client, self.database, FUNNEL_TABLE, list(row), [row])
        self.metrics["funnel_scored"] += 1
        if self.trigger_mode == "automatic" and result["forecast_eligibility"] == "eligible":
            self.enqueue(ReviewRequest(
                canonical_news_id=str(source_row["source_id"]),
                published_at_utc=str(source_row["source_timestamp"]),
                requested_by="automatic-funnel",
            ), trigger_mode="automatic")
        return row

    def request_reaction(self, request: ReactionRequest) -> dict[str, Any]:
        review = self.status(request.canonical_news_id)
        if str(review.get("status") or "") != "complete":
            raise ValueError("AI news review must complete before a reaction forecast")
        labels = json.loads(str(review.get("issuer_labels_json") or "{}"))
        source = self._load_source(ReviewRequest(
            canonical_news_id=request.canonical_news_id,
            published_at_utc=request.published_at_utc,
            requested_by=request.requested_by,
        ))
        requested_ticker = request.ticker.strip().upper()
        queued: list[str] = []
        for issuer in labels.get("issuers") or []:
            ticker = str(issuer.get("ticker") or "").strip().upper()
            if (
                not ticker
                or float(issuer.get("forecast_relevance_probability") or 0) < 0.5
                or (requested_ticker and ticker != requested_ticker)
            ):
                continue
            _post_json(
                f"{self.config.news_hypothesis_url}/hypothesize",
                {
                    "canonical_news_id": request.canonical_news_id,
                    "ticker": ticker,
                    "published_at_utc": str(source["source_timestamp"]),
                    "title": str(source.get("title") or ""),
                    "rendered_text": str(source.get("text") or ""),
                    "semantic_label": issuer,
                    "session_id": f"{request.requested_by}:{uuid.uuid4().hex[:16]}",
                },
                10.0,
            )
            queued.append(ticker)
        if not queued:
            raise ValueError("No AI-reviewed forecast-eligible issuer is available")
        self.metrics["reaction_queued"] += len(queued)
        return {"status": "queued", "canonical_news_id": request.canonical_news_id, "tickers": queued}

    def enqueue(self, request: ReviewRequest, *, trigger_mode: str = "manual") -> dict[str, str]:
        if trigger_mode == "automatic" and self.trigger_mode != "automatic":
            raise PermissionError("automatic LLM review is disabled")
        key = request.canonical_news_id
        if key in self.pending:
            return {"status": "already_queued", "canonical_news_id": key}
        if not request.force and self._review_complete(request.canonical_news_id):
            return {"status": "complete", "canonical_news_id": key}
        self.pending.add(key)
        try:
            self.queue.put_nowait(ReviewWork(request=request, trigger_mode=trigger_mode))
        except asyncio.QueueFull:
            self.pending.discard(key)
            raise
        self.metrics["review_queued"] += 1
        self._write_review_status(request, trigger_mode, "queued")
        return {"status": "queued", "canonical_news_id": key}

    def status(self, canonical_news_id: str) -> dict[str, Any]:
        rows = list(self.client.iter_json_each_row(f"""
SELECT * FROM `{self.database}`.`{REVIEW_TABLE}` FINAL
WHERE canonical_news_id={sql_string(canonical_news_id)}
ORDER BY updated_at_utc DESC LIMIT 1 FORMAT JSONEachRow
"""))
        return rows[0] if rows else {"canonical_news_id": canonical_news_id, "status": "not_reviewed"}

    def funnel_current(self, canonical_news_id: str, rendered_text_hash: str) -> bool:
        value = self.client.execute(f"""
SELECT count() FROM `{self.database}`.`{FUNNEL_TABLE}` FINAL
WHERE canonical_news_id={sql_string(canonical_news_id)}
  AND rendered_text_hash={sql_string(rendered_text_hash)}
  AND contract_version={sql_string(FUNNEL_CONTRACT)}
""").strip()
        return int(value or "0") > 0

    async def _worker(self) -> None:
        while True:
            item = await self.queue.get()
            try:
                if item is None:
                    return
                await self._process_review(item)
            except Exception as exc:  # noqa: BLE001
                if item is not None:
                    self._write_review_status(item.request, item.trigger_mode, "failed", error=f"{type(exc).__name__}: {exc}")
                self.metrics["review_failed"] += 1
                self.metrics["last_error"] = f"{type(exc).__name__}: {exc}"[:1000]
            finally:
                if item is not None:
                    self.pending.discard(item.request.canonical_news_id)
                self.queue.task_done()

    async def _process_review(self, work: ReviewWork) -> None:
        request = work.request
        self._write_review_status(request, work.trigger_mode, "labeling")
        source = await asyncio.to_thread(self._load_source, request)
        sample = _review_sample(source)
        messages = build_messages(self.system_prompt, sample)
        source_hash = str(source["rendered_text_hash"])
        force_nonce = uuid.uuid4().hex if request.force else ""
        idempotency = hashlib.sha256(
            f"{request.canonical_news_id}|{source_hash}|{PROMPT_VERSION}|{SCHEMA_VERSION}|{force_nonce}".encode()
        ).hexdigest()
        payload = {
            "route": "news.issuer_review.v1",
            "idempotency_key": idempotency,
            "messages": messages,
            "response_schema": TRANSPORT_SCHEMA,
            "metadata": {
                "canonical_news_id": request.canonical_news_id,
                "source_hash": source_hash,
                "prompt_version": PROMPT_VERSION,
            },
        }
        # Deep reasoning can legitimately exceed the gateway's former 45-second
        # budget. Keep this caller above the route timeout so the gateway can
        # return its structured result or its own explicit failure.
        response = await asyncio.to_thread(_post_json, f"{self.config.model_gateway_url}/infer", payload, 135.0)
        result = canonicalize_output(response["result"])
        errors = validate_output(result, [row["sentence_id"] for row in sample["normalized_sentences"]])
        if errors:
            raise ValueError(f"issuer review validation failed: {errors}")
        forecast_tickers = sorted({
            str(row.get("ticker") or "").upper()
            for row in result["issuers"]
            if row.get("ticker") and float(row["forecast_relevance_probability"]) >= 0.5
        })
        sentiments = [_language_sentiment(row) for row in result["issuers"]]
        now = _timestamp()
        persisted = {
            "review_id": idempotency,
            "canonical_news_id": request.canonical_news_id,
            "published_at_utc": _clickhouse_timestamp(source["source_timestamp"]),
            "rendered_text_hash": source_hash,
            "contract_version": REVIEW_CONTRACT,
            "prompt_version": PROMPT_VERSION,
            "schema_version": SCHEMA_VERSION,
            "trigger_mode": work.trigger_mode,
            "requested_by": request.requested_by,
            "status": "complete",
            "issuer_labels_json": json.dumps(result, separators=(",", ":")),
            "forecast_tickers": forecast_tickers,
            "issuer_tickers": [str(row.get("ticker") or "").upper() for row in result["issuers"]],
            "language_sentiments": sentiments,
            "provider": str(response.get("provider") or ""),
            "model": str(response.get("model") or ""),
            "input_tokens": int(response.get("input_tokens") or 0),
            "output_tokens": int(response.get("output_tokens") or 0),
            "cost_usd": float(response.get("cost_usd") or 0),
            "latency_ms": int(response.get("latency_ms") or 0),
            "error": "",
            "updated_at_utc": now,
        }
        latest = {key: value for key, value in persisted.items() if key != "review_id"}
        insert_json_each_row(self.client, self.database, REVIEW_TABLE, list(latest), [latest])
        insert_json_each_row(self.client, self.database, REVIEW_HISTORY_TABLE, list(persisted), [persisted])
        self.metrics["review_completed"] += 1
        self.metrics["last_error"] = ""
        if work.trigger_mode == "automatic":
            try:
                await asyncio.to_thread(
                    self.request_reaction,
                    ReactionRequest(
                        canonical_news_id=request.canonical_news_id,
                        published_at_utc=request.published_at_utc,
                        requested_by="automatic-review",
                    ),
                )
            except Exception as exc:  # reaction is an independent persisted stage
                self.metrics["reaction_failed"] += 1
                self.metrics["hypothesis_enqueue_failed"] += 1
                self.metrics["last_error"] = f"{type(exc).__name__}: {exc}"[:1000]

    def _load_source(self, request: ReviewRequest) -> dict[str, Any]:
        rows = list(self.client.iter_json_each_row(f"""
SELECT e.canonical_news_id source_id,concat(toString(e.published_at_utc),'Z') source_timestamp,
 e.provider,e.title,if(empty(r.rendered_text),e.title,r.rendered_text) text,e.tickers,
 e.channels,e.provider_tags,e.content_quality_flags,
 if(empty(r.rendered_text_hash),hex(SHA256(e.title)),r.rendered_text_hash) rendered_text_hash
FROM `{self.database}`.`benzinga_news_event_v2` AS e FINAL
LEFT JOIN `{self.database}`.`benzinga_news_rendered_v2` AS r FINAL
 ON r.published_date=e.published_date AND r.provider_article_id=e.provider_article_id
 AND r.source_revision_key=e.source_revision_key
WHERE e.canonical_news_id={sql_string(request.canonical_news_id)}
  AND e.published_at_utc=parseDateTime64BestEffort({sql_string(request.published_at_utc)},9,'UTC')
LIMIT 1 FORMAT JSONEachRow
"""))
        if not rows:
            raise LookupError("canonical News source is unavailable")
        return rows[0]

    def _ticker_history(self, source: Mapping[str, Any]) -> dict[str, Any]:
        tickers = sorted({str(value).strip().upper() for value in source.get("tickers") or () if str(value).strip()})
        if not tickers:
            return {}
        published = str(source["source_timestamp"])
        ticker_sql = "[" + ",".join(sql_string(value) for value in tickers) + "]"
        rows = list(self.client.iter_json_each_row(f"""
WITH prior AS (
 SELECT ticker,count() session_count,max(published_at_utc) previous_at
 FROM (
  SELECT arrayJoin(tickers) ticker,published_at_utc
  FROM `{self.database}`.`benzinga_news_event_v2` FINAL
  WHERE published_at_utc < parseDateTime64BestEffort({sql_string(published)},9,'UTC')
    AND published_at_utc >= toStartOfDay(parseDateTime64BestEffort({sql_string(published)},9,'UTC'),'America/New_York')
    AND hasAny(tickers,{ticker_sql})
 ) WHERE has({ticker_sql},ticker) GROUP BY ticker
)
SELECT min(session_count)+1 min_ticker_session_ordinal,max(session_count)+1 max_ticker_session_ordinal,
 min(dateDiff('second',previous_at,parseDateTime64BestEffort({sql_string(published)},9,'UTC'))) min_seconds_since_previous_ticker_news,
 max(dateDiff('second',previous_at,parseDateTime64BestEffort({sql_string(published)},9,'UTC'))) max_seconds_since_previous_ticker_news,
 countIf(session_count=0)>0 any_ticker_first_session,countIf(session_count=0)=length({ticker_sql}) all_tickers_first_session
FROM (SELECT value ticker,ifNull(p.session_count,0) session_count,p.previous_at FROM (SELECT arrayJoin({ticker_sql}) value) t LEFT JOIN prior p ON p.ticker=t.value)
FORMAT JSONEachRow
"""))
        result = rows[0] if rows else {}
        minimum = result.get("min_seconds_since_previous_ticker_news")
        result["any_ticker_news_within_5m"] = minimum is not None and float(minimum) < 300
        result["any_ticker_news_within_30m"] = minimum is not None and float(minimum) < 1800
        return result

    def _market_cap_context(self, source: Mapping[str, Any]) -> dict[str, Any]:
        from src.backend.ticker_facts_service import ticker_facts_payload

        values: list[dict[str, Any]] = []
        for ticker in sorted({str(value).strip().upper() for value in source.get("tickers") or () if str(value).strip()}):
            try:
                payload = ticker_facts_payload(
                    ticker,
                    as_of=_utc_iso(source["source_timestamp"]),
                    database=self.database,
                )
            except ValueError:
                values.append({
                    "ticker": ticker,
                    "market_cap": None,
                    "market_cap_bucket": "missing",
                    "market_cap_source": "invalid_ticker",
                })
                continue
            market = ((payload.get("facts") or {}).get("market") or {})
            cap = market.get("market_cap")
            values.append({"ticker": ticker, "market_cap": cap, "market_cap_bucket": _cap_bucket(cap), "market_cap_source": "ticker_facts_asof" if cap else "missing"})
        known = [float(row["market_cap"]) for row in values if row.get("market_cap")]
        buckets = sorted({str(row["market_cap_bucket"]) for row in values})
        return {
            "market_cap_coverage": "complete" if values and len(known) == len(values) else "partial" if known else "missing",
            "market_cap_known_ticker_count": len(known),
            "market_cap_missing_fraction": 1.0 - len(known) / max(1, len(values)),
            "market_cap_min": min(known) if known else None,
            "market_cap_median": sorted(known)[len(known) // 2] if known else None,
            "market_cap_max": max(known) if known else None,
            "market_cap_min_bucket": _cap_bucket(min(known)) if known else "missing",
            "market_cap_max_bucket": _cap_bucket(max(known)) if known else "missing",
            "market_cap_bucket_set": "|".join(buckets) if buckets else "missing",
            "market_cap_source_set": "ticker_facts_asof" if known else "missing",
            "market_cap_max_age_bucket": "unknown",
            "market_cap_tickers": values,
        }

    def _review_complete(self, canonical_news_id: str) -> bool:
        value = self.client.execute(f"""
SELECT count() FROM `{self.database}`.`{REVIEW_TABLE}` FINAL
WHERE canonical_news_id={sql_string(canonical_news_id)} AND status='complete'
""").strip()
        return int(value or "0") > 0

    def _write_review_status(self, request: ReviewRequest, trigger_mode: str, status: str, *, error: str = "") -> None:
        row = {
            "canonical_news_id": request.canonical_news_id,
            "published_at_utc": _clickhouse_timestamp(request.published_at_utc),
            "rendered_text_hash": "", "contract_version": REVIEW_CONTRACT,
            "prompt_version": PROMPT_VERSION, "schema_version": SCHEMA_VERSION,
            "trigger_mode": trigger_mode, "requested_by": request.requested_by,
            "status": status, "issuer_labels_json": "", "forecast_tickers": [],
            "issuer_tickers": [], "language_sentiments": [], "provider": "", "model": "",
            "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "latency_ms": 0,
            "error": error[:1000], "updated_at_utc": _timestamp(),
        }
        insert_json_each_row(self.client, self.database, REVIEW_TABLE, list(row), [row])

    def _ensure_tables(self) -> None:
        self.client.execute(f"""CREATE TABLE IF NOT EXISTS `{self.database}`.`{FUNNEL_TABLE}` (
canonical_news_id String,published_at_utc DateTime64(9,'UTC'),rendered_text_hash String,
contract_version LowCardinality(String),deterministic_engine_version LowCardinality(String),
stage LowCardinality(String),forecast_eligibility LowCardinality(String),eligible_probability Float64,
threshold Float64,model_release_id String,model_release_hash String,created_at_utc DateTime64(6,'UTC')
) ENGINE=ReplacingMergeTree(created_at_utc) PARTITION BY toYYYYMM(published_at_utc)
ORDER BY (canonical_news_id,contract_version)""")
        self.client.execute(f"""CREATE TABLE IF NOT EXISTS `{self.database}`.`{REVIEW_TABLE}` (
canonical_news_id String,published_at_utc DateTime64(9,'UTC'),rendered_text_hash String,
contract_version LowCardinality(String),prompt_version LowCardinality(String),schema_version LowCardinality(String),
trigger_mode LowCardinality(String),requested_by String,status LowCardinality(String),issuer_labels_json String,
forecast_tickers Array(LowCardinality(String)),issuer_tickers Array(LowCardinality(String)),
language_sentiments Array(LowCardinality(String)),provider LowCardinality(String),model String,
input_tokens UInt32,output_tokens UInt32,cost_usd Float64,latency_ms UInt32,error String,
updated_at_utc DateTime64(6,'UTC')
) ENGINE=ReplacingMergeTree(updated_at_utc) PARTITION BY toYYYYMM(published_at_utc)
ORDER BY (canonical_news_id,contract_version)""")
        self.client.execute(f"""CREATE TABLE IF NOT EXISTS `{self.database}`.`{REVIEW_HISTORY_TABLE}` (
review_id String,canonical_news_id String,published_at_utc DateTime64(9,'UTC'),rendered_text_hash String,
contract_version LowCardinality(String),prompt_version LowCardinality(String),schema_version LowCardinality(String),
trigger_mode LowCardinality(String),requested_by String,status LowCardinality(String),issuer_labels_json String,
forecast_tickers Array(LowCardinality(String)),issuer_tickers Array(LowCardinality(String)),
language_sentiments Array(LowCardinality(String)),provider LowCardinality(String),model String,
input_tokens UInt32,output_tokens UInt32,cost_usd Float64,latency_ms UInt32,error String,
updated_at_utc DateTime64(6,'UTC')
) ENGINE=MergeTree PARTITION BY toYYYYMM(published_at_utc)
ORDER BY (canonical_news_id,contract_version,review_id,updated_at_utc)""")


def _review_sample(source: Mapping[str, Any]) -> dict[str, Any]:
    text = str(source.get("text") or source.get("title") or "").replace("\r", "\n")
    sentences: list[str] = []
    for line in (part.strip() for part in text.split("\n") if part.strip()):
        sentences.extend(part.strip() for part in _SENTENCE_SPLIT.split(line) if part.strip())
    if not sentences:
        raise ValueError("News source has no reviewable sentences")
    return {
        "published_at_utc": str(source["source_timestamp"]),
        "normalized_sentences": [{"sentence_id": index, "text": value} for index, value in enumerate(sentences, 1)],
        "metadata": {
            "title": str(source.get("title") or ""), "provider": str(source.get("provider") or ""),
            "tickers": list(source.get("tickers") or ()), "channels": list(source.get("channels") or ()),
            "provider_tags": list(source.get("provider_tags") or ()),
        },
    }


def _language_sentiment(issuer: Mapping[str, Any]) -> str:
    positive = float(issuer["positive_implication_probability"])
    negative = float(issuer["negative_implication_probability"])
    if positive >= 0.5 and negative >= 0.5:
        return "mixed"
    if positive >= 0.5:
        return "positive"
    if negative >= 0.5:
        return "negative"
    return "neutral"


def _post_json(url: str, payload: Mapping[str, Any], timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:1000]
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc


def _load_system_prompt(prompt_path: Path) -> str:
    metadata_path = prompt_path.with_suffix(".json")
    prompt = prompt_path.read_text(encoding="utf-8")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if metadata.get("contract_version") != "news_issuer_review_prompt_v1":
        raise ValueError("unsupported issuer review prompt contract")
    digest = hashlib.sha256(prompt_path.read_bytes()).hexdigest()
    if digest != metadata.get("prompt_sha256"):
        raise ValueError("issuer review prompt hash mismatch")
    if not prompt.strip():
        raise ValueError("issuer review prompt is empty")
    return prompt


def _timestamp() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")


def _clickhouse_timestamp(value: Any) -> str:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")


def _utc_iso(value: Any) -> str:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat()


def _cap_bucket(value: Any) -> str:
    if value in {None, ""}:
        return "missing"
    number = float(value)
    if number < 50_000_000:
        return "nano_lt_50m"
    if number < 300_000_000:
        return "micro_50m_300m"
    if number < 2_000_000_000:
        return "small_300m_2b"
    if number < 10_000_000_000:
        return "mid_2b_10b"
    if number < 200_000_000_000:
        return "large_10b_200b"
    return "mega_gte_200b"
