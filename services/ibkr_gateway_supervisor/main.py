from __future__ import annotations

import argparse
import asyncio
import json
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from research.mlops.env import discover_env_files, load_env_files
from services.gateway_core.health import build_health_payload
from services.gateway_core.uvicorn_logging import quiet_uvicorn_log_config, suppress_uvicorn_access_logger
from services.ibkr_gateway_supervisor.config import IbkrGatewayConfig
from services.ibkr_gateway_supervisor.login import run_playwright_login
from services.ibkr_gateway_supervisor.status import build_ibkr_status_snapshot
from services.ibkr_gateway_supervisor.supervisor import IbkrGatewaySupervisor


REPO_ROOT = Path(__file__).resolve().parents[2]


class SupervisorService:
    def __init__(self, supervisor: IbkrGatewaySupervisor) -> None:
        self.supervisor = supervisor
        self.thread: threading.Thread | None = None
        self.last_error = ""

    async def start(self) -> None:
        if self.thread is not None and self.thread.is_alive():
            return
        self.thread = threading.Thread(target=self._run, name="ibkr-gateway-supervisor", daemon=True)
        self.thread.start()

    async def stop(self) -> None:
        self.supervisor.request_stop()
        if self.thread is not None:
            self.thread.join(timeout=15)

    def _run(self) -> None:
        try:
            self.supervisor.run_forever()
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"{type(exc).__name__}: {exc}"
            self.supervisor.terminal_state.last_error = self.last_error

    def metrics(self) -> dict[str, object]:
        state = self.supervisor.terminal_state
        return {
            "status": "failed" if self.last_error else "",
            "last_error": self.last_error or state.last_error,
            "supervisor_thread_alive": bool(self.thread and self.thread.is_alive()),
            "gateway_status": state.gateway_status,
            "auth_status": state.auth_status,
            "keepalive_status": state.keepalive_status,
            "account_status": state.account_status,
            "tickle_count": state.tickle_count,
            "tickle_failures": state.tickle_failures,
            "auth_failures": state.auth_failures,
            "reauth_attempts": state.reauth_attempts,
            "login_attempts": state.login_attempts,
            "event_log_path": state.event_log_path,
            "clickhouse_status": state.clickhouse_status,
            "clickhouse_error": state.clickhouse_error,
        }

    def recent_snapshot(self, limit: int = 25) -> dict[str, object]:
        rows = list(self.supervisor.terminal_state.recent_events)[-max(1, min(limit, 250)) :]
        return {"rows": rows, "event_log_path": self.supervisor.terminal_state.event_log_path}


def create_app(config: IbkrGatewayConfig | None = None, *, start_background: bool = True) -> FastAPI:
    cfg = config or IbkrGatewayConfig.from_env()
    supervisor = IbkrGatewaySupervisor(cfg)
    service = SupervisorService(supervisor)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        if start_background:
            await service.start()
        try:
            yield
        finally:
            await service.stop()

    app = FastAPI(title="Quant Research Workbench IBKR Gateway Supervisor", version="0.1.0", lifespan=lifespan)
    app.state.service = service
    app.state.supervisor = supervisor

    @app.get("/health")
    async def health() -> dict[str, object]:
        metrics = service.metrics()
        if metrics.get("status") == "failed":
            metrics["current_phase"] = "failed"
        return build_health_payload(service_name="ibkr_gateway_supervisor", config=cfg, metrics=metrics)

    @app.get("/config")
    async def config_payload() -> dict[str, object]:
        return cfg.public_dict()

    @app.get("/metrics")
    async def metrics() -> dict[str, object]:
        return service.metrics()

    @app.get("/snapshot/status")
    async def status_snapshot() -> dict[str, object]:
        snapshot = build_ibkr_status_snapshot(cfg, supervisor.terminal_state)
        snapshot["service_specific"]["supervisor_thread_alive"] = bool(service.thread and service.thread.is_alive())
        snapshot["service_specific"]["last_runtime_error"] = service.last_error
        return snapshot

    @app.get("/snapshot/ibkr/recent")
    async def recent(limit: int = 25) -> dict[str, object]:
        return service.recent_snapshot(limit)

    @app.websocket("/stream/ibkr")
    async def ibkr_stream(websocket: WebSocket) -> None:
        await websocket.accept()
        try:
            while True:
                await websocket.send_json(service.recent_snapshot(25))
                await asyncio.sleep(2.0)
        except WebSocketDisconnect:
            return

    return app


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="IBKR Client Portal Gateway supervisor.")
    parser.add_argument("--account", default="paper", help="Configured account key. Starts with paper.")
    parser.add_argument("--check-only", action="store_true", help="Verify config, gateway reachability, auth status, and account access once.")
    parser.add_argument("--login-once", action="store_true", help="Use headed Playwright to log in once, then verify auth/account access.")
    parser.add_argument("--no-launch", action="store_true", help="Do not start bin/run.bat if the gateway is unavailable.")
    parser.add_argument("--headless", action="store_true", help="Run the Playwright login helper headless.")
    parser.add_argument("--no-background", action="store_true", help="Start HTTP app without launching the supervisor loop.")
    return parser.parse_args()


def main() -> None:
    load_env_files(discover_env_files(REPO_ROOT))
    args = parse_args()
    if args.no_launch:
        import os

        os.environ["IBKR_GATEWAY_LAUNCH"] = "false"
    if args.headless:
        import os

        os.environ["IBKR_GATEWAY_LOGIN_HEADLESS"] = "true"
    config = IbkrGatewayConfig.from_env(account_key=args.account)
    try:
        if args.check_only:
            print(json.dumps(config.public_dict(), indent=2, sort_keys=True), flush=True)
            supervisor = IbkrGatewaySupervisor(config)
            code = supervisor.check_once()
            print(json.dumps(build_ibkr_status_snapshot(config, supervisor.terminal_state), indent=2, sort_keys=True, default=str), flush=True)
            raise SystemExit(code)
        if args.login_once:
            raise SystemExit(0 if asyncio.run(run_playwright_login(config)) else 1)
        suppress_uvicorn_access_logger()
        uvicorn.run(
            create_app(config, start_background=not args.no_background),
            host=config.host,
            port=config.port,
            log_level="info",
            access_log=False,
            log_config=quiet_uvicorn_log_config(),
        )
    except RuntimeError as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, sort_keys=True), flush=True)
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
