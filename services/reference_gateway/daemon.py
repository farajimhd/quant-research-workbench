from __future__ import annotations

import subprocess
import sys
import time
import os
import json
from dataclasses import dataclass
from datetime import UTC, datetime

from services.gateway_policy import active_collection_window
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


def run_reference_daemon(config: ReferenceGatewayConfig, base_args: list[str]) -> None:
    log_path = new_runtime_log_path(config.prepared_root_win)
    logger = RuntimeLogger(log_path)
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
            raise SystemExit(2)
    while True:
        cycle = run_daemon_cycle(config, base_args, log_path=log_path)
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
