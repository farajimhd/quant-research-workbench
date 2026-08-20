from __future__ import annotations

import os
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect

from services.gateway_core.dashboard import build_dashboard_snapshot

from .config import ServiceConfig
from .contracts import BarBatchRequest, InferenceRequest, OperationalConfigurationUpdate, ScopeRequest
from .operational import configuration_snapshot, update_configuration
from .runtime import BarGptRuntime


config = ServiceConfig.from_env()
runtime = BarGptRuntime(config)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await runtime.start()
    try:
        yield
    finally:
        await runtime.stop()


app = FastAPI(title="BarGPT Service", version="1.0", lifespan=lifespan)


@app.get("/health")
def health() -> dict:
    return runtime.health()


@app.get("/status")
def status() -> dict:
    return runtime.health()


@app.get("/snapshot/status")
def status_snapshot() -> dict:
    health = runtime.health()
    queue = health["queue"]
    metrics = {
        **health["metrics"],
        "status": health["status"],
        "current_phase": "running" if health["status"] == "ready" else health["status"],
        "current_phase_message": health["message"],
        "started_at_utc": health["started_at"],
        "active_scopes": health["scope_count"],
        "active_tickers": health["active_ticker_count"],
        "queue_size": queue["active"],
        "queue_capacity": queue["capacity"],
    }
    public_config = {
        "bind": config.bind,
        "device": config.device,
        "dtype": config.dtype,
        "maximum_tickers": config.maximum_tickers,
        "maximum_batch_size": config.maximum_batch_size,
        "maximum_batch_delay_ms": config.maximum_batch_delay_ms,
        "prediction_history": config.prediction_history,
        "minimum_warm_1s_bars": config.minimum_warm_1s_bars,
        "queue_capacity": config.queue_capacity,
        "warm_concurrency": config.warm_concurrency,
        "connect_qmd": config.connect_qmd,
    }
    return build_dashboard_snapshot(
        service_name="bar_gpt",
        config=public_config,
        metrics=metrics,
        recent_items=runtime.prediction_snapshot(limit=25),
        service_specific={
            "source": "bar_gpt_runtime",
            "operational": {
                "status": health["status"],
                "scope_count": health["scope_count"],
                "active_ticker_count": health["active_ticker_count"],
            },
            "queues": {
                "depth": queue["active"],
                "capacity": queue["capacity"],
                "drop_total": health["metrics"].get("queue_drops", 0),
            },
            "cache": {"entries": len(health["caches"]), "tickers": health["active_ticker_count"]},
            "checkpoint": {"models": health["models"]},
            "qmd": health["qmd"],
        },
    )


@app.get("/metrics")
def metrics() -> dict:
    health = runtime.health()
    return {
        **health["metrics"],
        "status": health["status"],
        "active_scopes": health["scope_count"],
        "active_tickers": health["active_ticker_count"],
        "queue_size": health["queue"]["active"],
        "queue_capacity": health["queue"]["capacity"],
    }


@app.get("/models")
def models() -> dict:
    return {"models": runtime.health()["models"]}


@app.get("/configuration")
def operational_configuration() -> dict:
    try:
        return configuration_snapshot(runtime)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.put("/configuration", status_code=202)
def replace_operational_configuration(request: OperationalConfigurationUpdate) -> dict:
    try:
        return update_configuration(runtime, request)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/scopes")
def scopes() -> dict:
    rows = []
    for scope_id in runtime.active_scopes():
        try:
            rows.append(runtime.scope_snapshot(scope_id))
        except KeyError:
            continue
    return {"schema_version": 1, "scopes": rows}


@app.put("/scopes/{scope_id}")
async def replace_scope(scope_id: str, request: ScopeRequest) -> dict:
    try:
        return await runtime.replace_scope(scope_id, request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/scopes/{scope_id}/advance")
async def advance_scope(scope_id: str, request: ScopeRequest) -> dict:
    try:
        return await runtime.advance_scope(scope_id, request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.delete("/scopes/{scope_id}")
def remove_scope(scope_id: str) -> dict:
    return {"scope_id": scope_id, "removed": runtime.remove_scope(scope_id)}


@app.post("/bars", status_code=202)
async def ingest_bars(request: BarBatchRequest) -> dict:
    try:
        return await runtime.ingest_bars(request.scope_id, request.bars)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/infer")
async def infer(request: InferenceRequest) -> dict:
    try:
        rows = await runtime.infer(request)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"schema_version": 1, "rows": rows, "row_count": len(rows)}


@app.get("/predictions")
def predictions(ticker: str = "", limit: int = Query(default=100, ge=1, le=10_000)) -> dict:
    return runtime.prediction_snapshot(ticker=ticker, limit=limit)


@app.websocket("/stream/predictions")
async def prediction_stream(socket: WebSocket) -> None:
    await socket.accept()
    queue = runtime.subscribe()
    try:
        while True:
            await socket.send_json(await queue.get())
            queue.task_done()
    except WebSocketDisconnect:
        pass
    finally:
        runtime.unsubscribe(queue)


def main() -> None:
    bind = os.environ.get("BAR_GPT_BIND", config.bind)
    host, port = bind.rsplit(":", 1)
    print(
        f"BarGPT service starting bind={bind} models={len(config.releases)} "
        f"device={config.device} qmd={'enabled' if config.connect_qmd else 'disabled'}"
    )
    uvicorn.run(app, host=host, port=int(port), access_log=False)


if __name__ == "__main__":
    main()
