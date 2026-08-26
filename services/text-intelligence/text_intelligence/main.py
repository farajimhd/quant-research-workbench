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
from .canonical_live import (
    CanonicalTextRuntime,
    TextDocumentNotice,
    TextDocumentNoticeBatch,
)
from .forecast_review import ForecastReviewRuntime, ReactionRequest, ReviewBatch, ReviewRequest
from .sec_review import SecReviewRequest, SecReviewRuntime

config = IntelligenceConfig.from_env()
engine = IntelligenceEngine(config)
live_runtime = LiveNewsRuntime(enabled=config.enable_live_ai)
shared_client = ClickHouseHttpClient(
        default_clickhouse_url(),
        default_clickhouse_user(),
        default_clickhouse_password(),
        timeout_seconds=30,
    )
forecast_review_runtime = ForecastReviewRuntime(config, shared_client, live_runtime.database)
sec_review_runtime = SecReviewRuntime(config, shared_client, live_runtime.database)
canonical_runtime = CanonicalTextRuntime(
    client=shared_client,
    database=live_runtime.database,
    live_news=live_runtime,
    forecast_review=forecast_review_runtime,
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    terminal_stop = asyncio.Event()
    terminal_task: asyncio.Task[None] | None = None
    if config.terminal_rich_enabled:
        from .terminal import run_terminal_dashboard

        terminal_task = asyncio.create_task(
            run_terminal_dashboard(config, _snapshot_metrics, terminal_stop),
            name="text-intelligence-terminal",
        )
    live_started = False
    canonical_started = False
    try:
        await forecast_review_runtime.start()
        await sec_review_runtime.start()
        await live_runtime.start()
        live_started = True
        await canonical_runtime.start()
        canonical_started = True
        yield
    finally:
        terminal_stop.set()
        if terminal_task is not None:
            await asyncio.gather(terminal_task, return_exceptions=True)
        if canonical_started:
            await canonical_runtime.stop()
        if live_started:
            await live_runtime.stop()
        await sec_review_runtime.stop()
        await forecast_review_runtime.stop()
        canonical_runtime.client.close()


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
        canonical_runtime.enqueue(
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
            canonical_runtime.enqueue(
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
            canonical_runtime.enqueue(document)
    except asyncio.QueueFull as exc:
        raise HTTPException(status_code=503, detail="text intelligence queue is full") from exc
    return {"status": "queued", "count": len(batch.documents)}


@app.post("/news-review", status_code=202)
def request_news_review(request: ReviewRequest) -> dict[str, str]:
    try:
        return forecast_review_runtime.enqueue(request, trigger_mode="manual")
    except asyncio.QueueFull as exc:
        raise HTTPException(status_code=503, detail="news review queue is full") from exc


@app.post("/news-reviews", status_code=202)
def request_news_reviews(batch: ReviewBatch) -> dict[str, object]:
    return {"results": [forecast_review_runtime.enqueue(item, trigger_mode="manual") for item in batch.requests]}


@app.get("/news-review/{canonical_news_id}")
def news_review_status(canonical_news_id: str) -> dict[str, object]:
    return forecast_review_runtime.status(canonical_news_id)


@app.post("/news-reaction", status_code=202)
def request_news_reaction(request: ReactionRequest) -> dict[str, object]:
    try:
        return forecast_review_runtime.request_reaction(request)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/sec-review", status_code=202)
async def request_sec_review(request: SecReviewRequest) -> dict[str, str]:
    try:
        return await sec_review_runtime.enqueue(request)
    except asyncio.QueueFull as exc:
        raise HTTPException(status_code=503, detail="SEC review queue is full") from exc
    except LookupError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/sec-review/{cik}/{accession_number}")
def sec_review_status(cik: str, accession_number: str) -> dict[str, object]:
    return sec_review_runtime.status(cik, accession_number)


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
    deterministic_metrics = canonical_runtime.snapshot_metrics()
    worker_error_active = (
        deterministic_metrics.get("deterministic_worker_error_status") == "active"
    )
    reconcile_error_active = (
        deterministic_metrics.get("deterministic_reconcile_error_status") == "active"
    )
    active_error = bool(worker_error_active or reconcile_error_active or failed)
    queue_size = int(deterministic_metrics.get("deterministic_queue_size") or 0)
    active_workers = int(
        deterministic_metrics.get("deterministic_active_workers") or 0
    )
    runtime_status = str(
        deterministic_metrics.get("deterministic_runtime_status") or "starting"
    )
    current_phase = (
        "degraded"
        if active_error
        else runtime_status
        if runtime_status != "running"
        else "processing"
        if queue_size or active_workers
        else "idle"
    )
    last_error = str(
        deterministic_metrics.get("deterministic_reconcile_last_error")
        if reconcile_error_active
        else deterministic_metrics.get("deterministic_worker_last_error")
        if worker_error_active
        else deterministic_metrics.get("deterministic_last_error")
        or ""
    )
    return {
        "status": "degraded" if active_error else "running",
        "current_phase": current_phase,
        "current_phase_message": (
            last_error
            if active_error
            else f"Deterministic runtime is {runtime_status}."
            if runtime_status != "running"
            else f"{active_workers} workers active; {queue_size} notices queued."
            if queue_size or active_workers
            else "Waiting for canonical News or SEC notices; reconciliation remains active."
        ),
        "started_at_utc": deterministic_metrics.get(
            "deterministic_started_at_utc", ""
        ),
        "last_error": last_error,
        "last_error_status": "active" if active_error else "resolved",
        "bind": config.bind,
        "mode": "execute",
        "model_root": str(config.model_root),
        "enable_models": config.enable_models,
        "enable_llm": config.enable_llm,
        "enable_live_ai": config.enable_live_ai,
        "forecast_review_trigger_mode": forecast_review_runtime.trigger_mode,
        "review_gateway_timeout_seconds": config.review_gateway_timeout_seconds,
        "forecast_funnel_enabled": config.forecast_funnel_enabled,
        "forecast_review_metrics": {
            **forecast_review_runtime.metrics,
            "queue_size": forecast_review_runtime.queue.qsize(),
            "pending": len(forecast_review_runtime.pending),
        },
        "sec_review_trigger_mode": "manual",
        "sec_review_metrics": {
            **sec_review_runtime.metrics,
            "queue_size": sec_review_runtime.queue.qsize(),
            "pending": len(sec_review_runtime.pending),
        },
        "stack_version": config.stack_version,
        "taxonomy_version": config.taxonomy_version,
        "models_loaded": loaded,
        "models_failed": failed,
        "errors": int(active_error),
        "failed_rows": deterministic_metrics.get("deterministic_failed", 0),
        "source_statuses": [
            {
                "name": "canonical_text",
                "status": "degraded" if active_error else "ok",
                "rows": deterministic_metrics.get("deterministic_completed", 0),
                "detail": (
                    last_error
                    if active_error
                    else "Bounded News and SEC reconciliation is available."
                ),
            },
            {
                "name": "model_registry",
                "status": "ok" if failed == 0 else "degraded",
                "rows": len(model_rows),
                "detail": f"loaded={loaded} failed={failed}",
            }
        ],
        "tasks": [
            {
                "name": "reconcile_canonical_text",
                "status": (
                    "failed" if reconcile_error_active else "running"
                ),
                "rows": deterministic_metrics.get(
                    "deterministic_reconcile_notices", 0
                ),
                "message": "Find new or revised canonical News and SEC sources.",
            },
            {
                "name": "persist_news_synthesis_v1_and_sec_synthesis_v1",
                "status": "failed" if worker_error_active else current_phase,
                "rows": deterministic_metrics.get("deterministic_completed", 0),
                "message": "Persist News Synthesis V1, SEC Synthesis V1, and compatible SEC labels.",
            },
        ],
        "live_session": vars(live_runtime.session),
        "live_metrics": live_metrics,
        "deterministic_metrics": deterministic_metrics,
        "enable_live_ai": config.enable_live_ai,
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
