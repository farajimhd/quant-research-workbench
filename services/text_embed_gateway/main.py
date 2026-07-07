from __future__ import annotations

import argparse
import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

import uvicorn
from fastapi import FastAPI

from research.mlops.env import discover_env_files, load_env_files
from services.gateway_core.dashboard import build_dashboard_snapshot
from services.gateway_core.health import build_health_payload
from services.gateway_core.uvicorn_logging import quiet_uvicorn_log_config, suppress_uvicorn_access_logger
from services.text_embed_gateway.config import TextEmbedGatewayConfig
from services.text_embed_gateway.gateway import TextEmbedGateway


REPO_ROOT = Path(__file__).resolve().parents[2]


def create_app(config: TextEmbedGatewayConfig | None = None, *, start_background: bool = True) -> FastAPI:
    cfg = config or TextEmbedGatewayConfig.from_env()
    gateway = TextEmbedGateway(cfg)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        if start_background:
            await gateway.start()
        try:
            yield
        finally:
            await gateway.stop()

    app = FastAPI(title="Quant Research Workbench Text Embedding Gateway", version="0.1.0", lifespan=lifespan)
    app.state.gateway = gateway

    @app.get("/health")
    async def health() -> dict[str, object]:
        return build_health_payload(service_name="text_embed_gateway", config=cfg, metrics=gateway.snapshot_metrics())

    @app.get("/config")
    async def config_payload() -> dict[str, object]:
        return cfg.public_dict()

    @app.get("/metrics")
    async def metrics() -> dict[str, object]:
        return gateway.snapshot_metrics()

    @app.get("/snapshot/status")
    async def status_snapshot() -> dict[str, object]:
        return build_dashboard_snapshot(
            service_name="text_embed_gateway",
            config=cfg,
            metrics=gateway.snapshot_metrics(),
            recent_items=gateway.recent_snapshot(25),
        )

    @app.get("/snapshot/text-embeddings/recent")
    async def recent(limit: int = 50) -> dict[str, object]:
        return gateway.recent_snapshot(limit)

    return app


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Live Qwen text embedding gateway.")
    parser.add_argument("--bind", default="", help="Override TEXT_EMBED_GATEWAY_BIND, e.g. 127.0.0.1:8798")
    parser.add_argument("--check-only", action="store_true", help="Load config and construct the service, then exit before loading Qwen.")
    parser.add_argument("--load-model-check", action="store_true", help="Load and release Qwen once, then exit.")
    parser.add_argument("--no-background", action="store_true", help="Start HTTP app without model/poll loops.")
    parser.add_argument("--no-local-files-only", action="store_true", help="Allow HuggingFace downloads/lookups for the first Qwen cache warmup.")
    return parser.parse_args()


def main() -> None:
    load_env_files(discover_env_files(REPO_ROOT))
    args = parse_args()
    if args.bind:
        import os

        os.environ["TEXT_EMBED_GATEWAY_BIND"] = args.bind
    if args.no_local_files_only:
        import os

        os.environ["TEXT_EMBED_LOCAL_FILES_ONLY"] = "false"
    cfg = TextEmbedGatewayConfig.from_env()
    if args.check_only:
        print("Text embedding gateway config OK", flush=True)
        print(json.dumps(cfg.public_dict(), indent=2, default=str), flush=True)
        return
    if args.load_model_check:
        gateway = TextEmbedGateway(cfg)
        gateway._load_model()  # noqa: SLF001
        print(json.dumps(gateway.snapshot_metrics(), indent=2, default=str), flush=True)
        gateway._release_model()  # noqa: SLF001
        return
    app = create_app(cfg, start_background=not args.no_background)
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
