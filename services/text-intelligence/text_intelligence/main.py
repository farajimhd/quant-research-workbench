from __future__ import annotations

import asyncio
import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException

from research.mlops.clickhouse import (
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
)
from services.gateway_core.dashboard import build_dashboard_snapshot
from services.gateway_core.health import build_health_payload
from services.gateway_core.uvicorn_logging import quiet_uvicorn_log_config, suppress_uvicorn_access_logger

from .config import IntelligenceConfig
from .schemas import IntelligenceResponse, NewsArticleForClassification
from .tiers import IntelligenceEngine
from .live import LiveCandidate, LiveCandidateBatch, LiveNewsRuntime, LiveSessionUpdate
from .scoped_live import (
    ScopedTextRuntime,
    TextDocumentNotice,
    TextDocumentNoticeBatch,
)

config = IntelligenceConfig.from_env()
engine = IntelligenceEngine(config)
live_runtime = LiveNewsRuntime(enabled=config.enable_live_ai)
scoped_runtime = ScopedTextRuntime(
    client=ClickHouseHttpClient(
        default_clickhouse_url(),
        default_clickhouse_user(),
        default_clickhouse_password(),
        timeout_seconds=30,
    ),
    database=live_runtime.database,
    live_news=live_runtime,
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await live_runtime.start()
    await scoped_runtime.start()
    try:
        yield
    finally:
        await scoped_runtime.stop()
        await live_runtime.stop()
        scoped_runtime.client.close()


app = FastAPI(title="Text Intelligence Service", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, object]:
    return build_health_payload(
        service_name="text_intelligence",
        config=config,
        metrics=_snapshot_metrics(),
    )


@app.get("/snapshot/status")
def status_snapshot() -> dict[str, object]:
    return build_dashboard_snapshot(
        service_name="text_intelligence",
        config=config,
        metrics=_snapshot_metrics(),
        service_specific={"model_registry": engine.registry.snapshot()},
    )


@app.get("/models")
def models() -> dict[str, object]:
    return engine.registry.snapshot()


@app.post("/classify", response_model=IntelligenceResponse)
def classify(article: NewsArticleForClassification) -> IntelligenceResponse:
    return engine.classify(article)


@app.get("/live-session")
def live_session() -> dict[str, object]:
    return vars(live_runtime.session)


@app.post("/live-session")
def update_live_session(update: LiveSessionUpdate) -> dict[str, object]:
    return vars(live_runtime.update_session(update))


@app.post("/candidate", status_code=202)
def enqueue_candidate(candidate: LiveCandidate) -> dict[str, object]:
    try:
        scoped_runtime.enqueue(
            TextDocumentNotice(
                corpus="news",
                source_id=candidate.canonical_news_id,
                source_timestamp=candidate.published_at_utc,
            )
        )
    except asyncio.QueueFull as exc:
        raise HTTPException(status_code=503, detail="text intelligence queue is full") from exc
    return {"status": "queued", "canonical_news_id": candidate.canonical_news_id}


@app.post("/candidates", status_code=202)
def enqueue_candidates(batch: LiveCandidateBatch) -> dict[str, object]:
    try:
        for candidate in batch.candidates:
            scoped_runtime.enqueue(
                TextDocumentNotice(
                    corpus="news",
                    source_id=candidate.canonical_news_id,
                    source_timestamp=candidate.published_at_utc,
                )
            )
    except asyncio.QueueFull as exc:
        raise HTTPException(status_code=503, detail="text intelligence queue is full") from exc
    return {"status": "queued", "count": len(batch.candidates)}


@app.post("/documents", status_code=202)
def enqueue_documents(batch: TextDocumentNoticeBatch) -> dict[str, object]:
    try:
        for document in batch.documents:
            scoped_runtime.enqueue(document)
    except asyncio.QueueFull as exc:
        raise HTTPException(status_code=503, detail="text intelligence queue is full") from exc
    return {"status": "queued", "count": len(batch.documents)}


def _snapshot_metrics() -> dict[str, object]:
    registry = engine.registry.snapshot()
    model_rows = registry.get("models") if isinstance(registry.get("models"), list) else []
    loaded = 0
    failed = 0
    for payload in model_rows:
        if not isinstance(payload, dict):
            continue
        if payload.get("downloaded"):
            loaded += 1
        if payload.get("error") or payload.get("load_error"):
            failed += 1
    live_metrics = {**live_runtime.metrics, "queue_size": live_runtime.queue.qsize()}
    deterministic_metrics = {
        **scoped_runtime.metrics,
        "deterministic_queue_size": scoped_runtime.queue.qsize(),
    }
    return {
        "status": "running" if failed == 0 else "degraded",
        "bind": config.bind,
        "mode": "execute",
        "model_root": str(config.model_root),
        "enable_models": config.enable_models,
        "enable_llm": config.enable_llm,
        "enable_live_ai": config.enable_live_ai,
        "stack_version": config.stack_version,
        "taxonomy_version": config.taxonomy_version,
        "models_loaded": loaded,
        "models_failed": failed,
        "errors": failed,
        "source_statuses": [
            {
                "name": "model_registry",
                "status": "ok" if failed == 0 else "degraded",
                "rows": len(model_rows),
                "detail": f"loaded={loaded} failed={failed}",
            }
        ],
        "tasks": [
            {
                "name": "serve_models",
                "status": "running",
                "rows": len(model_rows),
                "message": "OpenAI-compatible model serving helper is ready.",
            }
        ],
        "live_session": vars(live_runtime.session),
        "live_metrics": live_metrics,
        "deterministic_metrics": deterministic_metrics,
        **live_metrics,
        **deterministic_metrics,
    }


def main() -> None:
    host, port_text = config.bind.rsplit(":", 1)
    suppress_uvicorn_access_logger()
    uvicorn.run(
        app,
        host=host,
        port=int(port_text),
        log_level="info",
        access_log=False,
        log_config=quiet_uvicorn_log_config(),
    )


if __name__ == "__main__":
    main()
