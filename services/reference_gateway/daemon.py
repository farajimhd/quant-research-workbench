from __future__ import annotations

import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime

from services.gateway_policy import active_collection_window
from services.reference_gateway.config import ReferenceGatewayConfig


@dataclass(frozen=True, slots=True)
class DaemonCycle:
    started_at_utc: str
    active_window: bool
    interval_seconds: float
    command: list[str]
    returncode: int
    elapsed_seconds: float


def run_reference_daemon(config: ReferenceGatewayConfig, base_args: list[str]) -> None:
    print(
        "reference_gateway_daemon=started "
        f"execute={config.execute} read_database={config.clickhouse_read_database} "
        f"write_database={config.clickhouse_write_database}",
        flush=True,
    )
    while True:
        cycle = run_daemon_cycle(config, base_args)
        print(
            "reference_gateway_daemon_cycle="
            f"active_window={cycle.active_window} returncode={cycle.returncode} "
            f"elapsed_seconds={cycle.elapsed_seconds:.1f} next_seconds={cycle.interval_seconds:.1f}",
            flush=True,
        )
        time.sleep(max(5.0, cycle.interval_seconds))


def run_daemon_cycle(config: ReferenceGatewayConfig, base_args: list[str]) -> DaemonCycle:
    started = time.perf_counter()
    active = active_collection_window(service_prefix="REFERENCE")
    command = [sys.executable, "-m", "services.reference_gateway.main", "--no-daemon"]
    command.extend(arg for arg in base_args if arg != "--daemon")
    if active and config.after_hours_writes_only and not config.market_hours_write_override:
        if "--execute" not in command and "--no-execute" not in command:
            command.append("--execute")
        if "--active-ticker-check" not in command:
            command.append("--active-ticker-check")
        if "--no-write-discovered-issues" not in command:
            command.append("--write-discovered-issues")
        if "--no-write-canonical-graph" not in command:
            command.append("--no-write-canonical-graph")
        if "--no-rebuild-tradable" not in command:
            command.append("--no-rebuild-tradable")
        if "--no-market-publication-gap-fill" not in command:
            command.append("--no-market-publication-gap-fill")
    returncode = subprocess.run(command, check=False).returncode
    interval = config.daemon_active_interval_seconds if active else config.daemon_after_hours_interval_seconds
    return DaemonCycle(
        started_at_utc=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        active_window=active,
        interval_seconds=interval,
        command=command,
        returncode=returncode,
        elapsed_seconds=time.perf_counter() - started,
    )
