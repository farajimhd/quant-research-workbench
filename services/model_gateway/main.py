from __future__ import annotations

import uvicorn
from fastapi import FastAPI, HTTPException

from .config import GatewayConfig
from .schemas import InferenceRequest, InferenceResponse
from .service import ModelGateway

config = GatewayConfig.from_env()
gateway = ModelGateway(config)
app = FastAPI(title="Model Gateway", version="1.0")


@app.get("/health")
def health() -> dict[str, object]:
    return {
        "status": "ready",
        "service": "model_gateway",
        "bind": config.bind,
        "routes": sorted(config.routes),
        "providers": sorted(config.providers),
        "max_concurrency": config.max_concurrency,
        "metrics": {
            "route_count": len(config.routes),
            "provider_count": len(config.providers),
            "max_concurrency": config.max_concurrency,
        },
    }


@app.get("/routes")
def routes() -> dict[str, object]:
    return {
        name: {
            "providers": route.providers,
            "timeout_seconds": route.timeout_seconds,
            "max_output_tokens": route.max_output_tokens,
            "daily_budget_usd": route.daily_budget_usd,
            "reasoning_effort": route.reasoning_effort,
        }
        for name, route in config.routes.items()
    }


@app.post("/infer", response_model=InferenceResponse)
async def infer(request: InferenceRequest) -> InferenceResponse:
    try:
        return await gateway.infer(request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def main() -> None:
    host, port = config.bind.rsplit(":", 1)
    uvicorn.run(app, host=host, port=int(port), access_log=False)


if __name__ == "__main__":
    main()
