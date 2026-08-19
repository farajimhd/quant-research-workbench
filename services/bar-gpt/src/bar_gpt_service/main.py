from __future__ import annotations

import os
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect

from .config import ServiceConfig
from .contracts import BarBatchRequest, InferenceRequest, ScopeRequest
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


@app.get("/models")
def models() -> dict:
    return {"models": runtime.health()["models"]}


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
