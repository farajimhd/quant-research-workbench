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
from services.gateway_core.dashboard import build_dashboard_snapshot
from services.gateway_core.health import build_health_payload
from services.gateway_core.uvicorn_logging import quiet_uvicorn_log_config, suppress_uvicorn_access_logger
from services.sec_gateway.config import SecGatewayConfig
from services.sec_gateway.gateway import SecGateway
from services.sec_gateway.preflight import PreflightError


REPO_ROOT = Path(__file__).resolve().parents[2]


def create_app(config: SecGatewayConfig | None = None, *, start_background: bool = True) -> FastAPI:
    cfg = config or SecGatewayConfig.from_env()
    gateway = SecGateway(cfg)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        if start_background:
            await gateway.start()
        try:
            yield
        finally:
            await gateway.stop()

    app = FastAPI(title="Quant Research Workbench SEC Gateway", version="0.1.0", lifespan=lifespan)
    app.state.gateway = gateway

    @app.get("/health")
    async def health() -> dict[str, object]:
        return build_health_payload(service_name="sec_gateway", config=cfg, metrics=gateway.snapshot_metrics())

    @app.get("/config")
    async def config_payload() -> dict[str, object]:
        return cfg.public_dict()

    @app.get("/metrics")
    async def metrics() -> dict[str, object]:
        return gateway.snapshot_metrics()

    @app.get("/snapshot/status")
    async def status_snapshot() -> dict[str, object]:
        return build_dashboard_snapshot(
            service_name="sec_gateway",
            config=cfg,
            metrics=gateway.snapshot_metrics(),
            recent_items=gateway.recent_snapshot(25),
        )

    @app.get("/snapshot/sec/recent")
    async def recent(limit: int = 100) -> dict[str, object]:
        return gateway.recent_snapshot(limit)

    @app.websocket("/stream/sec")
    async def sec_stream(websocket: WebSocket) -> None:
        await websocket.accept()
        try:
            while True:
                await websocket.send_json(gateway.recent_snapshot(100))
                await asyncio.sleep(2.0)
        except WebSocketDisconnect:
            return

    return app


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Python SEC gateway.")
    parser.add_argument("--bind", default="", help="Override SEC_GATEWAY_BIND, e.g. 127.0.0.1:8797")
    parser.add_argument("--check-only", action="store_true", help="Run preflight and exit.")
    parser.add_argument("--no-background", action="store_true", help="Start HTTP app without poll loop.")
    return parser.parse_args()


def main() -> None:
    load_env_files(discover_env_files(REPO_ROOT))
    args = parse_args()
    if args.bind:
        import os

        os.environ["SEC_GATEWAY_BIND"] = args.bind
    cfg = SecGatewayConfig.from_env()
    app = create_app(cfg, start_background=not args.no_background and not args.check_only)
    if args.check_only:
        try:
            report = asyncio.run(app.state.gateway.preflight())
        except PreflightError as exc:
            print("SEC gateway preflight FAILED", flush=True)
            print(json.dumps(exc.report.public_dict(), indent=2), flush=True)
            raise SystemExit(1) from None
        print("SEC gateway preflight OK", flush=True)
        print(json.dumps(report.public_dict(), indent=2), flush=True)
        return
    suppress_uvicorn_access_logger()
    uvicorn.run(
        app,
        host=cfg.host,
        port=cfg.port,
        log_level="info",
        access_log=False,
        log_config=quiet_uvicorn_log_config(),
        timeout_graceful_shutdown=int(cfg.graceful_shutdown_seconds),
    )


if __name__ == "__main__":
    main()
