from __future__ import annotations

import json
import os
import asyncio
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from services.gateway_policy import active_collection_window
from services.gateway_core.dashboard import build_dashboard_snapshot
from services.gateway_core.health import build_health_payload
from services.reference_gateway.config import ReferenceGatewayConfig
from services.reference_gateway.memory import memory_snapshot
from services.reference_gateway.preflight import run_preflight
from services.reference_gateway.runtime_log import RUNTIME_LOG_ENV, RuntimeLogger, new_runtime_log_path


@dataclass(frozen=True, slots=True)
class DaemonCycle:
    started_at_utc: str
    active_window: bool
    interval_seconds: float
    command: list[str]
    returncode: int
    elapsed_seconds: float


@dataclass(slots=True)
class ReferenceDaemonState:
    config: ReferenceGatewayConfig
    started_at_utc: str = field(default_factory=lambda: utc_now())
    updated_at_utc: str = field(default_factory=lambda: utc_now())
    status: str = "starting"
    current_phase: str = "starting"
    current_phase_message: str = "Reference gateway daemon is starting."
    runtime_log_path: str = ""
    poll_runs: int = 0
    poll_failures: int = 0
    last_error: str = ""
    last_cycle: dict[str, Any] = field(default_factory=dict)
    recent_cycles: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=25))

    def set_log_path(self, path: Path) -> None:
        self.runtime_log_path = str(path)
        self.touch("running", "runtime_log_ready", f"Writing runtime log to {path}.")

    def touch(self, status: str, phase: str, message: str = "") -> None:
        self.status = status
        self.current_phase = phase
        self.current_phase_message = message
        self.updated_at_utc = utc_now()

    def record_cycle(self, cycle: DaemonCycle) -> None:
        payload = asdict(cycle)
        self.poll_runs += 1
        if cycle.returncode != 0:
            self.poll_failures += 1
            self.last_error = f"child_cycle_failed returncode={cycle.returncode}"
            self.touch("failed", "child_cycle_failed", self.last_error)
        else:
            self.last_error = ""
            self.touch("running", "waiting_for_next_cycle", f"Last child cycle completed in {cycle.elapsed_seconds:.1f}s.")
        self.last_cycle = payload
        self.recent_cycles.append(payload)

    def record_error(self, message: str) -> None:
        self.poll_failures += 1
        self.last_error = message
        self.touch("failed", "daemon_error", message)

    def metrics(self) -> dict[str, Any]:
        child_event = latest_runtime_event(self.runtime_log_path)
        current_phase = self.current_phase
        current_message = self.current_phase_message
        current_status = self.status
        updated_at_utc = self.updated_at_utc
        child_tasks: list[dict[str, Any]] = []
        if child_event:
            event_name = str(child_event.get("event") or "")
            operation_name = str(child_event.get("name") or event_name)
            operation_status = str(child_event.get("status") or self.status)
            current_phase = operation_name or current_phase
            current_message = str(child_event.get("detail") or child_event.get("reason") or current_message)
            current_status = operation_status if operation_status else current_status
            updated_at_utc = str(child_event.get("ts_utc") or updated_at_utc)
            child_tasks.append(
                {
                    "name": operation_name,
                    "status": operation_status,
                    "rows": child_event.get("rows", ""),
                    "message": current_message,
                }
            )
        return {
            "status": current_status,
            "current_phase": current_phase,
            "current_phase_message": current_message,
            "started_at_utc": self.started_at_utc,
            "updated_at_utc": updated_at_utc,
            "poll_runs": self.poll_runs,
            "poll_failures": self.poll_failures,
            "last_error": self.last_error,
            "runtime_log_path": self.runtime_log_path,
            "daemon_loop_enabled": self.config.daemon_loop_enabled,
            "last_cycle_returncode": self.last_cycle.get("returncode", ""),
            "last_cycle_elapsed_seconds": self.last_cycle.get("elapsed_seconds", ""),
            "last_cycle_active_window": self.last_cycle.get("active_window", ""),
            "last_cycle_started_at_utc": self.last_cycle.get("started_at_utc", ""),
            "tasks": [
                {"name": "daemon parent", "status": self.status, "message": self.current_phase_message},
                {"name": "child cycles", "status": "ok" if self.poll_failures == 0 else "warning", "rows": self.poll_runs, "message": "reference sync/audit child runs"},
                *child_tasks,
            ],
        }

    def recent_snapshot(self, limit: int = 25) -> dict[str, Any]:
        rows = latest_runtime_events(self.runtime_log_path, limit=max(1, min(limit, 250)))
        if not rows:
            rows = list(self.recent_cycles)[-max(1, min(limit, 250)) :]
        return {"rows": rows, "runtime_log_path": self.runtime_log_path}


def run_reference_daemon(config: ReferenceGatewayConfig, base_args: list[str]) -> None:
    log_path = new_runtime_log_path(config.prepared_root_win)
    logger = RuntimeLogger(log_path)
    state = ReferenceDaemonState(config=config)
    state.set_log_path(log_path)
    start_reference_api_server(config, state)
    print(
        "reference_gateway_daemon=started "
        f"execute={config.execute} read_database={config.clickhouse_read_database} "
        f"write_database={config.clickhouse_write_database} runtime_log={log_path}",
        flush=True,
    )
    logger.event(
        "daemon_started",
        execute=config.execute,
        read_database=config.clickhouse_read_database,
        write_database=config.clickhouse_write_database,
        base_args=base_args,
    )
    if config.preflight_enabled:
        result = run_preflight(config, require_source_sync_dependencies=True, logger=logger)
        print("reference_gateway_preflight=" + json.dumps(result.public_dict(), sort_keys=True), flush=True)
        if result.status != "ok":
            logger.event("daemon_failed", reason="preflight_failed")
            state.record_error("preflight_failed")
            raise SystemExit(2)
    while True:
        cycle = run_daemon_cycle(config, base_args, log_path=log_path)
        state.record_cycle(cycle)
        logger.event(
            "daemon_cycle_completed",
            active_window=cycle.active_window,
            returncode=cycle.returncode,
            elapsed_seconds=cycle.elapsed_seconds,
            next_seconds=cycle.interval_seconds,
            command=cycle.command,
            parent_memory=memory_snapshot("daemon_cycle_completed").public_dict(),
        )
        print(
            "reference_gateway_daemon_cycle="
            f"active_window={cycle.active_window} returncode={cycle.returncode} "
            f"elapsed_seconds={cycle.elapsed_seconds:.1f} next_seconds={cycle.interval_seconds:.1f}",
            flush=True,
        )
        if cycle.returncode != 0:
            logger.event("daemon_stopped", reason="child_cycle_failed", returncode=cycle.returncode)
            raise SystemExit(cycle.returncode)
        time.sleep(max(5.0, cycle.interval_seconds))


def run_daemon_cycle(config: ReferenceGatewayConfig, base_args: list[str], *, log_path) -> DaemonCycle:
    started = time.perf_counter()
    active = active_collection_window(service_prefix="REFERENCE")
    command = [sys.executable, "-m", "services.reference_gateway.main"]
    command.extend(child_cycle_args(base_args))
    env = dict(os.environ)
    env[RUNTIME_LOG_ENV] = str(log_path)
    try:
        completed = subprocess.run(
            command,
            check=False,
            env=env,
            timeout=config.daemon_child_timeout_seconds if config.daemon_child_timeout_seconds > 0 else None,
        )
        returncode = completed.returncode
    except subprocess.TimeoutExpired:
        returncode = 124
    interval = config.daemon_active_interval_seconds if active else config.daemon_after_hours_interval_seconds
    return DaemonCycle(
        started_at_utc=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        active_window=active,
        interval_seconds=interval,
        command=command,
        returncode=returncode,
        elapsed_seconds=time.perf_counter() - started,
    )


def child_cycle_args(base_args: list[str]) -> list[str]:
    result: list[str] = []
    skip_next = False
    for arg in base_args:
        if skip_next:
            skip_next = False
            continue
        if arg == "--run":
            skip_next = True
            continue
        result.append(arg)
    result.extend(["--run", "once"])
    return result


def start_reference_api_server(config: ReferenceGatewayConfig, state: ReferenceDaemonState) -> None:
    try:
        import uvicorn
        from fastapi import FastAPI, WebSocket, WebSocketDisconnect
    except Exception as exc:  # noqa: BLE001
        state.record_error(f"reference_api_import_failed: {type(exc).__name__}: {exc}")
        return

    app = FastAPI(title="Quant Research Workbench Reference Gateway", version="0.1.0")

    @app.get("/health")
    async def health() -> dict[str, object]:
        return build_health_payload(service_name="reference_gateway", config=config, metrics=state.metrics())

    @app.get("/config")
    async def config_payload() -> dict[str, object]:
        return config.public_dict()

    @app.get("/metrics")
    async def metrics() -> dict[str, object]:
        return state.metrics()

    @app.get("/snapshot/status")
    async def status_snapshot() -> dict[str, object]:
        return build_dashboard_snapshot(
            service_name="reference_gateway",
            config=config,
            metrics=state.metrics(),
            recent_items=state.recent_snapshot(25),
            service_specific={"last_cycle": state.last_cycle, "runtime_log_path": state.runtime_log_path},
        )

    @app.get("/snapshot/reference/recent")
    async def recent(limit: int = 25) -> dict[str, object]:
        return state.recent_snapshot(limit)

    @app.websocket("/stream/reference")
    async def reference_stream(websocket: WebSocket) -> None:
        await websocket.accept()
        try:
            while True:
                await websocket.send_json(state.recent_snapshot(25))
                await asyncio.sleep(2.0)
        except WebSocketDisconnect:
            return

    def run() -> None:
        server_config = uvicorn.Config(app, host=config.host, port=config.port, log_level="warning")
        try:
            uvicorn.Server(server_config).run()
        except Exception as exc:  # noqa: BLE001
            state.record_error(f"reference_api_server_failed: {type(exc).__name__}: {exc}")

    thread = threading.Thread(target=run, name="reference-gateway-api", daemon=True)
    thread.start()


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def latest_runtime_event(path: str) -> dict[str, Any]:
    rows = latest_runtime_events(path, limit=1)
    return rows[-1] if rows else {}


def latest_runtime_events(path: str, *, limit: int) -> list[dict[str, Any]]:
    if not path:
        return []
    log_path = Path(path)
    if not log_path.exists():
        return []
    try:
        with log_path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - 262_144), os.SEEK_SET)
            text = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    lines = text.splitlines()
    if lines and not lines[0].startswith("{"):
        lines = lines[1:]
    rows: list[dict[str, Any]] = []
    for line in lines[-max(1, limit * 8) :]:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if payload.get("event") in {"operation", "operation_progress", "daemon_cycle_completed", "audit_completed", "alerts_written"}:
            rows.append(payload)
    return rows[-limit:]
