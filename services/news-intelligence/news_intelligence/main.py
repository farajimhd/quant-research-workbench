from __future__ import annotations

import uvicorn
from fastapi import FastAPI

from .config import IntelligenceConfig
from .schemas import IntelligenceResponse, NewsArticleForClassification
from .tiers import IntelligenceEngine

config = IntelligenceConfig.from_env()
engine = IntelligenceEngine(config)
app = FastAPI(title="News Intelligence Service")


@app.get("/health")
def health() -> dict[str, object]:
    return {
        "status": "running",
        "bind": config.bind,
        "model_root": str(config.model_root),
        "enable_models": config.enable_models,
        "enable_llm": config.enable_llm,
        "stack_version": config.stack_version,
        "taxonomy_version": config.taxonomy_version,
    }


@app.get("/models")
def models() -> dict[str, object]:
    return engine.registry.snapshot()


@app.post("/classify", response_model=IntelligenceResponse)
def classify(article: NewsArticleForClassification) -> IntelligenceResponse:
    return engine.classify(article)


def main() -> None:
    host, port_text = config.bind.rsplit(":", 1)
    uvicorn.run(app, host=host, port=int(port_text), log_level="info")


if __name__ == "__main__":
    main()

