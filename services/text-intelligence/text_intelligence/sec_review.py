from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from typing import Any

from pydantic import BaseModel, Field

from research.mlops.clickhouse import ClickHouseHttpClient, insert_json_each_row, sql_string
from research.text_intelligence.sec_issuer_review_v1 import (
    PROMPT_VERSION,
    SCHEMA_VERSION,
    TRANSPORT_SCHEMA,
    build_messages,
    validate_output,
)
from research.text_intelligence.sec_synthesis_v1 import ENGINE_VERSION
from research.text_intelligence.sec_synthesis_v1.storage import SYNTHESIS_TABLE

from .config import IntelligenceConfig
from .forecast_review import _clickhouse_timestamp, _post_json, _timestamp


REVIEW_TABLE = "sec_llm_issuer_review_v1"
REVIEW_HISTORY_TABLE = "sec_llm_issuer_review_history_v1"
REVIEW_CONTRACT = "sec_llm_issuer_review_serving_v1"


class SecReviewRequest(BaseModel):
    cik: str = Field(min_length=1, max_length=32)
    accession_number: str = Field(min_length=1, max_length=64)
    requested_by: str = "operator"
    force: bool = False


class SecReviewWork(BaseModel):
    request: SecReviewRequest


class SecReviewRuntime:
    """Manual-only remote review of a durable SEC Synthesis document."""

    def __init__(self, config: IntelligenceConfig, client: ClickHouseHttpClient, database: str) -> None:
        self.config = config
        self.client = client
        self.database = database
        self.queue: asyncio.Queue[SecReviewWork | None] = asyncio.Queue(maxsize=128)
        self.pending: set[tuple[str, str]] = set()
        self.worker: asyncio.Task[None] | None = None
        self.metrics: dict[str, Any] = {"queued": 0, "completed": 0, "failed": 0, "last_error": ""}

    async def start(self) -> None:
        await asyncio.to_thread(self._ensure_tables)
        self.worker = asyncio.create_task(self._worker(), name="sec-manual-review")

    async def stop(self) -> None:
        if self.worker is None:
            return
        await self.queue.put(None)
        await self.queue.join()
        await self.worker
        self.worker = None

    def enqueue(self, request: SecReviewRequest) -> dict[str, str]:
        key = (request.cik, request.accession_number)
        if key in self.pending:
            return {"status": "already_queued", "cik": key[0], "accession_number": key[1]}
        synthesis = self._load_synthesis(*key)
        if not request.force and self._review_complete(*key, str(synthesis["source_hash"])):
            return {"status": "complete", "cik": key[0], "accession_number": key[1]}
        # There is intentionally no automatic trigger mode or caller-controlled mode.
        self.pending.add(key)
        try:
            self.queue.put_nowait(SecReviewWork(request=request))
        except asyncio.QueueFull:
            self.pending.discard(key)
            raise
        self._write_status(request, synthesis, "queued")
        self.metrics["queued"] += 1
        return {"status": "queued", "cik": key[0], "accession_number": key[1]}

    def status(self, cik: str, accession_number: str) -> dict[str, Any]:
        rows = list(self.client.iter_json_each_row(f"""
SELECT * FROM `{self.database}`.`{REVIEW_TABLE}` FINAL
WHERE cik={sql_string(cik)} AND accession_number={sql_string(accession_number)}
ORDER BY updated_at_utc DESC LIMIT 1 FORMAT JSONEachRow
"""))
        return rows[0] if rows else {"cik": cik, "accession_number": accession_number, "status": "not_reviewed"}

    async def _worker(self) -> None:
        while True:
            work = await self.queue.get()
            try:
                if work is None:
                    return
                await self._process(work.request)
            except Exception as exc:  # noqa: BLE001
                if work is not None:
                    try:
                        synthesis = self._load_synthesis(work.request.cik, work.request.accession_number)
                        self._write_status(work.request, synthesis, "failed", error=f"{type(exc).__name__}: {exc}")
                    except Exception:  # noqa: BLE001
                        pass
                self.metrics["failed"] += 1
                self.metrics["last_error"] = f"{type(exc).__name__}: {exc}"[:1000]
            finally:
                if work is not None:
                    self.pending.discard((work.request.cik, work.request.accession_number))
                self.queue.task_done()

    async def _process(self, request: SecReviewRequest) -> None:
        synthesis = await asyncio.to_thread(self._load_synthesis, request.cik, request.accession_number)
        self._write_status(request, synthesis, "reviewing")
        source_hash = str(synthesis["source_hash"])
        nonce = uuid.uuid4().hex if request.force else ""
        review_id = hashlib.sha256(
            f"{request.cik}|{request.accession_number}|{source_hash}|{PROMPT_VERSION}|{SCHEMA_VERSION}|{nonce}".encode()
        ).hexdigest()
        response = await asyncio.to_thread(
            _post_json,
            f"{self.config.model_gateway_url}/infer",
            {
                "route": "sec.issuer_review.v1",
                "idempotency_key": review_id,
                "messages": build_messages(synthesis),
                "response_schema": TRANSPORT_SCHEMA,
                "metadata": {
                    "cik": request.cik,
                    "accession_number": request.accession_number,
                    "source_hash": source_hash,
                    "prompt_version": PROMPT_VERSION,
                },
            },
            self.config.review_gateway_timeout_seconds,
        )
        result = response.get("result")
        if not isinstance(result, dict):
            raise ValueError("SEC issuer review result must be an object")
        errors = validate_output(result, synthesis)
        if errors:
            raise ValueError(f"SEC issuer review validation failed: {errors}")
        now = _timestamp()
        base = self._base_row(request, synthesis)
        persisted = {
            **base,
            "status": "complete",
            "review_json": json.dumps(result, ensure_ascii=False, separators=(",", ":")),
            "fundamental_direction": str(result["fundamental_direction"]),
            "materiality_probability": float(result["materiality_probability"]),
            "forecast_relevance_probability": float(result["forecast_relevance_probability"]),
            "provider": str(response.get("provider") or ""), "model": str(response.get("model") or ""),
            "input_tokens": int(response.get("input_tokens") or 0), "output_tokens": int(response.get("output_tokens") or 0),
            "cost_usd": float(response.get("cost_usd") or 0), "latency_ms": int(response.get("latency_ms") or 0),
            "error": "", "updated_at_utc": now,
        }
        insert_json_each_row(self.client, self.database, REVIEW_TABLE, list(persisted), [persisted])
        history = {"review_id": review_id, **persisted}
        insert_json_each_row(self.client, self.database, REVIEW_HISTORY_TABLE, list(history), [history])
        self.metrics["completed"] += 1
        self.metrics["last_error"] = ""

    def _load_synthesis(self, cik: str, accession_number: str) -> dict[str, Any]:
        rows = list(self.client.iter_json_each_row(f"""
SELECT synthesis_json FROM `{self.database}`.`{SYNTHESIS_TABLE}` FINAL
WHERE cik={sql_string(cik)} AND accession_number={sql_string(accession_number)}
  AND engine_version={sql_string(ENGINE_VERSION)}
ORDER BY updated_at_utc DESC LIMIT 1 FORMAT JSONEachRow
"""))
        if not rows:
            raise LookupError("SEC Synthesis is unavailable for this accession")
        return json.loads(str(rows[0]["synthesis_json"]))

    def _review_complete(self, cik: str, accession_number: str, source_hash: str) -> bool:
        value = self.client.execute(f"""
SELECT count() FROM `{self.database}`.`{REVIEW_TABLE}` FINAL
WHERE cik={sql_string(cik)} AND accession_number={sql_string(accession_number)}
  AND source_hash={sql_string(source_hash)}
  AND contract_version={sql_string(REVIEW_CONTRACT)}
  AND prompt_version={sql_string(PROMPT_VERSION)}
  AND schema_version={sql_string(SCHEMA_VERSION)}
  AND status='complete'
""").strip()
        return int(value or "0") > 0

    def _base_row(self, request: SecReviewRequest, synthesis: dict[str, Any]) -> dict[str, Any]:
        return {
            "cik": request.cik, "accession_number": request.accession_number,
            "accepted_at_utc": _clickhouse_timestamp(synthesis["accepted_at_utc"]),
            "source_hash": str(synthesis["source_hash"]), "contract_version": REVIEW_CONTRACT,
            "prompt_version": PROMPT_VERSION, "schema_version": SCHEMA_VERSION,
            "trigger_mode": "manual", "requested_by": request.requested_by,
        }

    def _write_status(self, request: SecReviewRequest, synthesis: dict[str, Any], status: str, *, error: str = "") -> None:
        row = {
            **self._base_row(request, synthesis), "status": status, "review_json": "",
            "fundamental_direction": "", "materiality_probability": 0.0, "forecast_relevance_probability": 0.0,
            "provider": "", "model": "", "input_tokens": 0, "output_tokens": 0,
            "cost_usd": 0.0, "latency_ms": 0, "error": error[:1000], "updated_at_utc": _timestamp(),
        }
        insert_json_each_row(self.client, self.database, REVIEW_TABLE, list(row), [row])

    def _ensure_tables(self) -> None:
        columns = """cik String,accession_number String,accepted_at_utc DateTime64(9,'UTC'),source_hash String,
contract_version LowCardinality(String),prompt_version LowCardinality(String),schema_version LowCardinality(String),
trigger_mode LowCardinality(String),requested_by String,status LowCardinality(String),review_json String,
fundamental_direction LowCardinality(String),materiality_probability Float64,forecast_relevance_probability Float64,
provider LowCardinality(String),model String,input_tokens UInt32,output_tokens UInt32,cost_usd Float64,latency_ms UInt32,
error String,updated_at_utc DateTime64(6,'UTC')"""
        self.client.execute(f"""CREATE TABLE IF NOT EXISTS `{self.database}`.`{REVIEW_TABLE}` ({columns})
ENGINE=ReplacingMergeTree(updated_at_utc) PARTITION BY toYYYYMM(accepted_at_utc)
ORDER BY (cik,accession_number,contract_version)""")
        self.client.execute(f"""CREATE TABLE IF NOT EXISTS `{self.database}`.`{REVIEW_HISTORY_TABLE}` (review_id String,{columns})
ENGINE=MergeTree PARTITION BY toYYYYMM(accepted_at_utc)
ORDER BY (cik,accession_number,contract_version,review_id,updated_at_utc)""")
