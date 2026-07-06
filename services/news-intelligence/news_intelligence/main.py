from __future__ import annotations

import uvicorn
from fastapi import FastAPI

from services.gateway_core.dashboard import build_dashboard_snapshot
from services.gateway_core.health import build_health_payload

from .config import IntelligenceConfig
from .schemas import IntelligenceResponse, NewsArticleForClassification
from .tiers import IntelligenceEngine

config = IntelligenceConfig.from_env()
engine = IntelligenceEngine(config)
app = FastAPI(title="News Intelligence Service")


@app.get("/health")
def health() -> dict[str, object]:
    return build_health_payload(
        service_name="news_intelligence",
        config=config,
        metrics=_snapshot_metrics(),
    )


@app.get("/snapshot/status")
def status_snapshot() -> dict[str, object]:
    return build_dashboard_snapshot(
        service_name="news_intelligence",
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
    return {
        "status": "running" if failed == 0 else "degraded",
        "bind": config.bind,
        "mode": "execute",
        "model_root": str(config.model_root),
        "enable_models": config.enable_models,
        "enable_llm": config.enable_llm,
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
    }


def main() -> None:
    host, port_text = config.bind.rsplit(":", 1)
    uvicorn.run(app, host=host, port=int(port_text), log_level="info")


if __name__ == "__main__":
    main()
