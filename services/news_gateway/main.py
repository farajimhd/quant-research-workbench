from __future__ import annotations

import argparse
import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from research.mlops.env import discover_env_files, load_env_files
from services.news_gateway.config import NewsGatewayConfig
from services.news_gateway.gateway import NewsGateway
from services.news_gateway.preflight import PreflightError


REPO_ROOT = Path(__file__).resolve().parents[2]


def create_app(config: NewsGatewayConfig | None = None, *, start_background: bool = True) -> FastAPI:
    cfg = config or NewsGatewayConfig.from_env()
    gateway = NewsGateway(cfg)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        if start_background:
            await gateway.start()
        try:
            yield
        finally:
            await gateway.stop()

    app = FastAPI(title="Quant Research Workbench News Gateway", version="0.1.0", lifespan=lifespan)
    app.state.gateway = gateway

    @app.get("/health")
    async def health() -> dict[str, object]:
        return {
            "status": "ok",
            "config": {
                "bind": cfg.bind,
                "data_root_win": str(cfg.data_root_win),
                "raw_root_win": str(cfg.raw_root_win),
                "execute": cfg.execute,
                "is_workstation": cfg.is_workstation,
            },
            "metrics": gateway.snapshot_metrics(),
        }

    @app.get("/config")
    async def config_payload() -> dict[str, object]:
        return cfg.public_dict()

    @app.get("/metrics")
    async def metrics() -> dict[str, object]:
        return gateway.snapshot_metrics()

    @app.get("/snapshot/news/recent")
    @app.get("/snapshot/news/scanner")
    async def recent(limit: int = 250) -> dict[str, object]:
        return await gateway.state.recent_snapshot(limit)

    @app.get("/snapshot/news/ticker/{ticker}")
    async def ticker_snapshot(ticker: str, limit: int = 100) -> dict[str, object]:
        return await gateway.state.ticker_snapshot(ticker, limit)

    @app.websocket("/stream/news")
    @app.websocket("/stream/news/scanner")
    async def news_stream(websocket: WebSocket) -> None:
        await websocket.accept()
        try:
            while True:
                await websocket.send_json(await gateway.state.recent_snapshot(250))
                await asyncio.sleep(2.0)
        except WebSocketDisconnect:
            return

    @app.websocket("/stream/news/ticker/{ticker}")
    async def ticker_stream(websocket: WebSocket, ticker: str) -> None:
        await websocket.accept()
        try:
            while True:
                await websocket.send_json(await gateway.state.ticker_snapshot(ticker, 100))
                await asyncio.sleep(2.0)
        except WebSocketDisconnect:
            return

    return app


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Python Benzinga news gateway.")
    parser.add_argument("--bind", default="", help="Override NEWS_GATEWAY_BIND, e.g. 127.0.0.1:8796")
    parser.add_argument("--check-only", action="store_true", help="Load config and construct app, then exit.")
    parser.add_argument("--no-background", action="store_true", help="Start HTTP app without poll loops.")
    return parser.parse_args()


def main() -> None:
    load_env_files(discover_env_files(REPO_ROOT))
    args = parse_args()
    if args.bind:
        import os

        os.environ["NEWS_GATEWAY_BIND"] = args.bind
    cfg = NewsGatewayConfig.from_env()
    app = create_app(cfg, start_background=not args.no_background and not args.check_only)
    if args.check_only:
        try:
            report = asyncio.run(app.state.gateway.preflight())
        except PreflightError as exc:
            print("News gateway preflight FAILED", flush=True)
            print(json.dumps(exc.report.public_dict(), indent=2), flush=True)
            raise SystemExit(1) from None
        print("News gateway preflight OK", flush=True)
        print(cfg.public_dict(), flush=True)
        print(json.dumps(report.public_dict(), indent=2), flush=True)
        return
    uvicorn.run(app, host=cfg.host, port=cfg.port, log_level="info", timeout_graceful_shutdown=10)


if __name__ == "__main__":
    main()
