from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, HTTPException

from .contextual import HypothesisRequest, NewsHypothesisRuntime

runtime = NewsHypothesisRuntime()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await runtime.start()
    try:
        yield
    finally:
        await runtime.stop()


app = FastAPI(title="News Hypothesis Service", version="1.0", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, object]:
    return {
        "status": "ready",
        "service": "news_hypothesis",
        "queue_size": runtime.queue.qsize(),
        "metrics": runtime.metrics,
        "responsibility": "contextual news hypotheses only; no market-model or order authority",
    }


@app.post("/hypothesize", status_code=202)
def hypothesize(request: HypothesisRequest) -> dict[str, str]:
    try:
        runtime.enqueue(request)
    except asyncio.QueueFull as exc:
        raise HTTPException(status_code=503, detail="news hypothesis queue is full") from exc
    return {"status": "queued", "canonical_news_id": request.canonical_news_id, "ticker": request.ticker}


def main() -> None:
    bind = os.environ.get("NEWS_HYPOTHESIS_BIND", "127.0.0.1:8803")
    host, port = bind.rsplit(":", 1)
    uvicorn.run(app, host=host, port=int(port), access_log=False)


if __name__ == "__main__":
    main()
