#!/usr/bin/env python3
"""Run the canonical Rust level-book algorithm in process-sharded workers."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = REPO_ROOT / "services" / "qmd_history_gateway" / "Cargo.toml"
BUILD_BINARY_NAME = "structure_checkpoint_campaign.exe" if os.name == "nt" else "structure_checkpoint_campaign"
RUNTIME_BINARY_NAME = (
    "structure_checkpoint_campaign_v6.exe" if os.name == "nt" else "structure_checkpoint_campaign_v6"
)
MAX_PROCESS_WORKERS = 80
RECOVERY_PRIORITY_TICKERS = ("SUGP", "JUNS")
HOLD_SCORE_REVISION = "beta22-wilson90-v1"
CERTIFICATION_SCHEMA_VERSION = 2
EXECUTION_CLOCK_AUTHORITY = "q_live.historical_event_execution_clock_v1"
EXECUTION_CLOCK_COVERAGE_AUTHORITY = "q_live.historical_event_execution_clock_coverage_v1"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def source_commit(explicit: str | None = None) -> str:
    if explicit:
        value = explicit.strip().lower()
        if not re.fullmatch(r"[0-9a-f]{40}", value):
            raise RuntimeError("--source-commit must be a full 40-character Git commit")
        return value
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(
            "the deployed source mirror has no Git metadata; pass --source-commit with the committed laptop revision"
        ) from exc


def worker_process_creationflags(platform_name: str = os.name) -> int:
    """Keep native shard workers attached to the supervisor without spawning consoles."""
    if platform_name != "nt":
        return 0
    return getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


def binary_candidates(explicit: str | None, environ: dict[str, str]) -> tuple[Path, ...]:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    if configured := environ.get("QMD_STRUCTURE_CAMPAIGN_BINARY"):
        candidates.append(Path(configured).expanduser())
    runtime_root = Path(environ.get("TRADING_RUNTIME_ROOT", r"D:\TradingML\runtimes"))
    candidates += [
        runtime_root / "bin" / RUNTIME_BINARY_NAME,
        runtime_root / "cargo-target" / "quant-research-workbench" / "release" / BUILD_BINARY_NAME,
        MANIFEST.parent / "target" / "release" / BUILD_BINARY_NAME,
    ]
    unique, seen = [], set()
    for candidate in candidates:
        key = str(candidate.resolve(strict=False)).casefold()
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return tuple(unique)


def resolve_cargo(environ: dict[str, str]) -> str | None:
    if cargo := shutil.which("cargo"):
        return cargo
    if profile := environ.get("USERPROFILE"):
        candidate = Path(profile) / ".cargo" / "bin" / ("cargo.exe" if os.name == "nt" else "cargo")
        if candidate.is_file():
            return str(candidate)
    return None


def resolve_binary(
    explicit: str | None,
    build: bool,
    environ: dict[str, str],
    force_rebuild: bool = False,
) -> Path:
    candidates = binary_candidates(explicit, environ)
    if not force_rebuild:
        for candidate in candidates:
            if candidate.is_file():
                return candidate.resolve()
    elif explicit:
        raise RuntimeError("--rebuild cannot be combined with --binary")
    if not build:
        raise RuntimeError("campaign binary was not found; searched:\n  " + "\n  ".join(map(str, candidates)))
    cargo = resolve_cargo(environ)
    if cargo is None:
        raise RuntimeError(
            "Cargo and the campaign binary are missing. Copy the prebuilt binary to "
            r"D:\TradingML\runtimes\bin\structure_checkpoint_campaign_v6.exe."
        )
    subprocess.run(
        [cargo, "build", "--release", "--bin", "structure_checkpoint_campaign", "--manifest-path", str(MANIFEST)],
        cwd=REPO_ROOT,
        env=environ,
        check=True,
    )
    target_dir = Path(environ["CARGO_TARGET_DIR"])
    built = target_dir / "release" / BUILD_BINARY_NAME
    if built.is_file():
        return built.resolve()
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise RuntimeError("Cargo completed, but the campaign binary was not found")


def parse_launcher_args(argv: list[str]) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--binary")
    parser.add_argument("--no-build", action="store_true")
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--launcher-help", action="store_true")
    parser.add_argument("--monitor-existing", action="store_true")
    parser.add_argument("--stop-existing", choices=("graceful", "fast"))
    parser.add_argument("--supervisor-child", action="store_true")
    parser.add_argument("--foreground-supervisor", action="store_true")
    parser.add_argument("--process-workers", type=int)
    parser.add_argument("--source-commit")
    parser.add_argument("--resume-from-runtime", "--recover-from-runtime", dest="resume_from_runtime")
    return parser.parse_known_args(argv)


def option_value(args: list[str], name: str) -> str | None:
    try:
        return args[args.index(name) + 1]
    except (ValueError, IndexError):
        return None


def replace_option(args: list[str], name: str, value: str) -> list[str]:
    result, index = [], 0
    while index < len(args):
        if args[index] == name:
            index += 2
        else:
            result.append(args[index])
            index += 1
    return [*result, name, value]


def remove_options(args: list[str], valued: set[str], flags: set[str]) -> list[str]:
    result, index = [], 0
    while index < len(args):
        if args[index] in valued:
            index += 2
        elif args[index] in flags:
            index += 1
        else:
            result.append(args[index])
            index += 1
    return result


def _required_manifest(path: Path) -> dict[str, Any]:
    manifest = read_status(path)
    if manifest is None:
        raise RuntimeError(f"required campaign manifest is unavailable: {path}")
    return manifest


def prepare_recovery_resume(
    campaign_args: list[str], source_runtime_value: str
) -> tuple[list[str], Path, dict[str, Any]]:
    """Bind a successor run to the exact immutable plan of an interrupted run."""
    source_runtime = Path(source_runtime_value).expanduser().resolve()
    target_runtime_value = option_value(campaign_args, "--runtime-dir")
    target_set_id = option_value(campaign_args, "--checkpoint-set-id")
    if not target_runtime_value or not target_set_id:
        raise RuntimeError(
            "recovery resume requires a new --runtime-dir and --checkpoint-set-id"
        )
    target_runtime = Path(target_runtime_value).expanduser().resolve()
    if target_runtime == source_runtime:
        raise RuntimeError(
            "recovery resume must use a new runtime directory; the source campaign is immutable"
        )
    source_manifest_path = source_runtime / "campaign-manifest.json"
    source_plan_path = source_runtime / "planner" / "campaign-plan.json"
    source_manifest = _required_manifest(source_manifest_path)
    if not source_plan_path.is_file():
        raise RuntimeError(f"source campaign plan is unavailable: {source_plan_path}")
    source_status = read_status(source_runtime / "campaign-status.json")
    if source_status is not None and source_status.get("status") == "running":
        raise RuntimeError("source campaign is still running; stop it before recovery")
    source_set_id = str(source_manifest.get("checkpoint_set_id") or "").strip()
    source_start = str(source_manifest.get("start_date") or "").strip()
    source_end = str(source_manifest.get("end_date") or "").strip()
    if not source_set_id or not source_start or not source_end:
        raise RuntimeError("source campaign manifest lacks its set or date identity")
    if source_set_id == target_set_id:
        raise RuntimeError("recovery source and target checkpoint sets must differ")
    result = list(campaign_args)
    for name, source_value in (
        ("--start-date", source_start),
        ("--end-date", source_end),
        ("--recovery-source-checkpoint-set-id", source_set_id),
    ):
        requested = option_value(result, name)
        if requested is not None and requested != source_value:
            raise RuntimeError(
                f"{name}={requested!r} does not match source campaign value {source_value!r}"
            )
        result = replace_option(result, name, source_value)
    existing_priorities = []
    index = 0
    while index < len(result):
        if result[index] == "--priority-ticker" and index + 1 < len(result):
            existing_priorities.append(result[index + 1].strip().upper())
            index += 2
        else:
            index += 1
    result = remove_options(result, {"--priority-ticker"}, set())
    priorities = [
        *RECOVERY_PRIORITY_TICKERS,
        *(ticker for ticker in existing_priorities if ticker not in RECOVERY_PRIORITY_TICKERS),
    ]
    # Native planning is bypassed for recovery, but retain the priority identity
    # in worker commands and durable supervisor evidence.
    for ticker in reversed(priorities):
        result = ["--priority-ticker", ticker, *result]
    return result, source_runtime, source_manifest


def recovery_plan(
    source_runtime: Path, source_manifest: dict[str, Any]
) -> list[dict[str, Any]]:
    path = source_runtime / "planner" / "campaign-plan.json"
    try:
        plans = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"invalid source campaign plan {path}: {exc}") from exc
    if not isinstance(plans, list) or not plans:
        raise RuntimeError("source campaign plan must be a non-empty list")
    tickers = [str(plan.get("ticker") or "").strip().upper() for plan in plans]
    if any(not ticker for ticker in tickers) or len(set(tickers)) != len(tickers):
        raise RuntimeError("source campaign plan has missing or duplicate tickers")
    source_hash = hashlib.sha256(
        "".join(f"{ticker}\n" for ticker in tickers).encode("utf-8")
    ).hexdigest()
    if source_hash != source_manifest.get("universe_hash"):
        raise RuntimeError("source campaign plan does not match its immutable universe hash")
    priorities = {ticker: index for index, ticker in enumerate(RECOVERY_PRIORITY_TICKERS)}
    indexed = list(enumerate(plans))
    indexed.sort(
        key=lambda item: (
            priorities.get(str(item[1]["ticker"]).upper(), len(priorities)),
            item[0],
        )
    )
    return [plan for _, plan in indexed]


def run_execution_clock_preflight(
    binary: Path,
    campaign_args: list[str],
    plans: list[dict[str, Any]],
    planner_dir: Path,
    environ: dict[str, str],
) -> int:
    """Validate the whole immutable universe before any shard worker starts."""
    planner_dir.mkdir(parents=True, exist_ok=True)
    ticker_file = planner_dir / "execution-clock-preflight-tickers.txt"
    ticker_file.write_text(
        "".join(f"{str(plan['ticker']).strip().upper()}\n" for plan in plans),
        encoding="utf-8",
    )
    args = remove_options(
        campaign_args,
        {
            "--workers",
            "--runtime-dir",
            "--ticker-file",
            "--priority-ticker",
            "--core-index",
            "--campaign-control-path",
        },
        {
            "--purge-existing-checkpoints",
            "--plan-only",
            "--explicit-universe-only",
            "--shard-worker",
            "--validate-execution-clock-only",
        },
    )
    preflight_dir = planner_dir / "execution-clock-preflight"
    preflight_dir.mkdir(parents=True, exist_ok=True)
    command = [
        str(binary),
        *args,
        "--ticker-file",
        str(ticker_file),
        "--explicit-universe-only",
        "--runtime-dir",
        str(preflight_dir),
        "--workers",
        "1",
        "--validate-execution-clock-only",
    ]
    print(
        "Validating execution-clock coverage for the complete immutable universe...",
        flush=True,
    )
    return subprocess.run(command, env=environ, check=False).returncode


def prepare_shards(plans: list[dict[str, Any]], worker_count: int) -> list[list[dict[str, Any]]]:
    worker_count = min(worker_count, len(plans))
    shards: list[list[dict[str, Any]]] = [[] for _ in range(worker_count)]
    loads = [0] * worker_count
    # Plan order is liquidity priority. The first N tickers start immediately;
    # later tickers go to the least-loaded shard without splitting a ticker.
    for index, plan in enumerate(plans):
        worker = index if index < worker_count else min(range(worker_count), key=loads.__getitem__)
        shards[worker].append(plan)
        loads[worker] += int(plan.get("estimated_events", 0))
    return shards


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
        deadline = time.monotonic() + 5.0
        delay = 0.02
        while True:
            try:
                os.replace(temporary, path)
                return
            except OSError as exc:
                if time.monotonic() >= deadline or getattr(exc, "winerror", None) not in (5, 32):
                    raise
                time.sleep(delay)
                delay = min(delay * 2, 0.5)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def campaign_control_path(runtime_dir: Path) -> Path:
    return runtime_dir / "campaign-control.json"


def request_campaign_stop(runtime_dir: Path, set_id: str, mode: str) -> Path:
    manifest = read_status(runtime_dir / "campaign-manifest.json")
    if manifest is None or manifest.get("checkpoint_set_id") != set_id:
        raise RuntimeError("stop request does not match an existing campaign manifest")
    path = campaign_control_path(runtime_dir)
    atomic_json(
        path,
        {
            "schema_version": 1,
            "checkpoint_set_id": set_id,
            "action": f"stop_{mode}",
            "request_id": uuid.uuid4().hex,
            "requested_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    return path


def process_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def launch_detached_supervisor(
    binary: Path,
    binary_sha256: str,
    campaign_args: list[str],
    workers: int,
    environ: dict[str, str],
    recovery_source_runtime: Path | None = None,
    campaign_source_commit: str | None = None,
) -> int:
    runtime_value = option_value(campaign_args, "--runtime-dir")
    set_id = option_value(campaign_args, "--checkpoint-set-id")
    if not runtime_value or not set_id:
        raise RuntimeError("process mode requires --runtime-dir and --checkpoint-set-id")
    runtime_dir = Path(runtime_value)
    supervisor_dir = runtime_dir / "supervisor"
    supervisor_dir.mkdir(parents=True, exist_ok=True)
    identity_path = supervisor_dir / "supervisor.json"
    existing = read_status(identity_path)
    if existing is not None and process_is_running(int(existing.get("pid", 0))):
        raise RuntimeError(
            f"campaign supervisor PID {existing['pid']} is already running; use --monitor-existing or --stop-existing"
        )
    log_path = supervisor_dir / "supervisor.log"
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--binary",
        str(binary),
        "--no-build",
        "--supervisor-child",
        "--process-workers",
        str(workers),
        *(
            ["--source-commit", campaign_source_commit]
            if campaign_source_commit is not None
            else []
        ),
        *(
            ["--resume-from-runtime", str(recovery_source_runtime)]
            if recovery_source_runtime is not None
            else []
        ),
        *campaign_args,
    ]
    creationflags = 0
    popen_kwargs: dict[str, Any] = {}
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    else:
        popen_kwargs["start_new_session"] = True
    with log_path.open("a", encoding="utf-8") as log:
        process = subprocess.Popen(
            command,
            cwd=REPO_ROOT,
            env=environ,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            creationflags=creationflags,
            **popen_kwargs,
        )
    launched_at = time.time()
    atomic_json(
        identity_path,
        {
            "schema_version": 1,
            "status": "running",
            "checkpoint_set_id": set_id,
            "pid": process.pid,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "executable_path": str(binary),
            "executable_sha256": binary_sha256,
            "worker_processes": workers,
            "log_path": str(log_path),
        },
    )
    print(
        f"Detached campaign supervisor PID {process.pid}; closing this terminal will not stop workers.",
        flush=True,
    )
    deadline = time.monotonic() + 300
    plan_path = runtime_dir / "planner" / "campaign-plan.json"
    manifest_path = runtime_dir / "campaign-manifest.json"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                f"detached supervisor exited with code {process.returncode}; inspect {log_path}"
            )
        fresh_worker_status = any(
            path.stat().st_mtime >= launched_at - 1
            for path in (runtime_dir / "workers").glob("worker-*/campaign-status.json")
        )
        if (
            plan_path.is_file()
            and manifest_path.is_file()
            and fresh_worker_status
        ):
            break
        time.sleep(1)
    else:
        raise RuntimeError(f"detached supervisor did not publish its plan within five minutes; inspect {log_path}")
    return monitor_existing_campaign(binary, binary_sha256, campaign_args, environ)


def read_status(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def status_is_fully_certified(status: dict[str, Any]) -> bool:
    total_units = int(status.get("total_units", 0))
    certified = int(status.get("counts", {}).get("certified", 0))
    return status.get("status") == "completed" and total_units > 0 and certified == total_units


def validate_process_worker_count(workers: int) -> None:
    if not 1 <= workers <= MAX_PROCESS_WORKERS:
        raise RuntimeError(f"worker process count must be between 1 and {MAX_PROCESS_WORKERS}")


def archive_previous_attempt_statuses(runtime_dir: Path, worker_dirs: list[Path]) -> Path | None:
    """Move stale dashboard snapshots aside before a resumed worker launch."""
    status_paths = [runtime_dir / "campaign-status.json"] + [
        worker_dir / "campaign-status.json" for worker_dir in worker_dirs
    ]
    existing = [path for path in status_paths if path.is_file()]
    if not existing:
        return None
    attempt_id = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%S.%fZ}-{uuid.uuid4().hex[:8]}"
    archive_dir = runtime_dir / "attempts" / attempt_id
    for source in existing:
        destination = archive_dir / source.relative_to(runtime_dir)
        destination.parent.mkdir(parents=True, exist_ok=True)
        source.replace(destination)
    return archive_dir


def fmt_count(value: int | float) -> str:
    return f"{int(value):,}"


def fmt_duration(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return "warming up"
    seconds = int(seconds)
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def aggregate_status(paths, plans, started, rates, processes) -> dict[str, Any]:
    worker_statuses = [read_status(path) for path in paths]
    statuses = [status for status in worker_statuses if status is not None]
    keys = ("active", "blocked", "certified", "completed", "failed", "finished", "queued", "retried", "skipped", "unavailable")
    counts = {key: sum(int(row.get("counts", {}).get(key, 0)) for row in statuses) for key in keys}
    # A force-terminated child cannot publish its final snapshot. Never retain
    # that stale file's active count after the process has actually exited.
    counts["active"] = sum(
        int((status or {}).get("counts", {}).get("active", 0))
        for status, process in zip(worker_statuses, processes, strict=True)
        if process.poll() is None
    )
    events = sum(int(row.get("events_processed", 0)) for row in statuses)
    total_events = sum(int(plan.get("estimated_events", 0)) for plan in plans)
    total_units = sum(len(plan.get("sessions", [])) for plan in plans)
    # A worker that has not published its new status snapshot is still queued.
    # Deriving this value from the immutable plan keeps startup progress truthful.
    counts["queued"] = max(total_units - counts["finished"] - counts["active"], 0)
    now = time.monotonic()
    rates.append((now, events))
    while len(rates) > 1 and rates[1][0] <= now - 300:
        rates.popleft()
    rate = 0.0
    if len(rates) > 1 and now - rates[0][0] >= 15 and events >= rates[0][1]:
        rate = (events - rates[0][1]) / (now - rates[0][0])
    active, issues = [], []
    for worker, (row, process) in enumerate(zip(worker_statuses, processes, strict=True)):
        if row is None:
            continue
        if process.poll() is None:
            active += [f"W{worker + 1:02d} {ticker}@{date}" for ticker, date in row.get("active", {}).items()]
        issues += row.get("issues", [])[-2:]
    exited = sum(process.poll() is not None for process in processes)
    failed_processes = sum(process.poll() not in (None, 0) for process in processes)
    return {
        "schema_version": 1,
        "status": "running" if exited < len(processes) else ("failed" if failed_processes else "completed"),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "worker_processes": len(processes),
        "worker_processes_exited": exited,
        "ticker_count": len(plans),
        "total_units": total_units,
        "total_estimated_events": total_events,
        "events_processed": events,
        "event_rate_5m": rate,
        "eta_seconds": max(total_events - events, 0) / rate if rate > 0 else None,
        "elapsed_seconds": now - started,
        "counts": counts,
        "active": active,
        "issues": issues[-10:],
    }


def render_plain(status: dict[str, Any]) -> str:
    counts = status["counts"]
    return (
        f"{status['updated_at']} status={status['status']} processes={status['worker_processes_exited']}/"
        f"{status['worker_processes']} exited units={counts['finished']}/{status['total_units']} "
        f"queued={counts['queued']} failed={counts['failed']} blocked={counts['blocked']} "
        f"retries={counts['retried']} events={fmt_count(status['events_processed'])}/"
        f"{fmt_count(status['total_estimated_events'])} rate={fmt_count(status['event_rate_5m'])}/s "
        f"elapsed={fmt_duration(status['elapsed_seconds'])} eta={fmt_duration(status['eta_seconds'])}"
    )


def render_rich(status: dict[str, Any], set_id: str):
    from rich.console import Group
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    counts, total = status["counts"], status["total_estimated_events"]
    pct = status["events_processed"] * 100 / total if total else 100.0
    summary = Table.grid(expand=True)
    summary.add_column()
    summary.add_column(justify="right")
    summary.add_row("Checkpoint set", set_id)
    summary.add_row(
        "Hold evidence",
        f"{HOLD_SCORE_REVISION} | repaired from raw counts on load",
    )
    summary.add_row(
        "Executable",
        f"{Path(status.get('executable_path', '?')).name} | "
        f"{status.get('executable_sha256', 'unknown')[:16]}",
    )
    summary.add_row("Processes", f"{status['worker_processes'] - status['worker_processes_exited']} active | {status['worker_processes_exited']} exited")
    summary.add_row(
        "Durable units",
        f"{fmt_count(counts['finished'])} / {fmt_count(status['total_units'])} | {fmt_count(counts['certified'])} certified",
    )
    summary.add_row("Events", f"{fmt_count(status['events_processed'])} / {fmt_count(total)}  {pct:.1f}%")
    summary.add_row("Throughput", f"{fmt_count(status['event_rate_5m'])}/s aggregate (5m)")
    summary.add_row("Elapsed | ETA", f"{fmt_duration(status['elapsed_seconds'])} | {fmt_duration(status['eta_seconds'])}")
    if status.get("monitor_mode") == "reattached":
        oldest = status.get("oldest_worker_status_age_seconds")
        summary.add_row(
            "Worker status",
            f"{status.get('stale_workers', 0)} stale | oldest {oldest:.0f}s"
            if oldest is not None
            else "waiting for first worker status",
        )
    summary.add_row("Queue", f"{fmt_count(counts['queued'])} queued | {counts['retried']} retries | {counts['failed']} failed | {counts['blocked']} blocked")
    active = "  ".join(status["active"][:8]) or "Workers starting or between tickers"
    issue = status["issues"][-1] if status["issues"] else None
    issue_text = "None" if issue is None else f"{issue.get('ticker', '?')} {issue.get('session_date') or ''}: {issue.get('error', '')}"
    if status.get("monitor_mode") == "reattached":
        resume_text = "Reattached read-only process view; progress comes from durable worker snapshots."
    elif status.get("previous_attempt_archive"):
        resume_text = "Previous attempt status archived; this view contains only the current attempt."
    else:
        resume_text = "Fresh attempt; no prior dashboard status was present."
    control_text = (
        "Ctrl+C closes this reattached monitor; campaign workers continue."
        if status.get("monitor_mode") == "reattached"
        else "Ctrl+C stops children; rerun the identical command to resume."
    )
    return Panel(
        Group(
            Text(
                f"Structural Checkpoint Campaign v6  {status['status'].upper()}"
                + ("  REATTACHED" if status.get("monitor_mode") == "reattached" else ""),
                style="bold cyan",
            ),
            summary,
            Text(f"Active  {active}"),
            Text(f"Latest issue  {issue_text}", style="red" if issue else "dim"),
            Text(resume_text, style="dim"),
            Text(control_text, style="dim"),
        ),
        border_style="cyan",
    )


class _DetachedProcessView:
    def __init__(self, status: dict[str, Any] | None):
        self.status = status

    def poll(self) -> int | None:
        if self.status is None or self.status.get("status") == "running":
            return None
        return 0 if self.status.get("status") == "completed" else 1


def _status_timestamp(value: dict[str, Any] | None, key: str) -> datetime | None:
    if value is None or not value.get(key):
        return None
    try:
        return datetime.fromisoformat(str(value[key]).replace("Z", "+00:00"))
    except ValueError:
        return None


def monitor_existing_campaign(
    binary: Path,
    binary_sha256: str,
    campaign_args: list[str],
    environ: dict[str, str],
) -> int:
    runtime_value = option_value(campaign_args, "--runtime-dir")
    set_id = option_value(campaign_args, "--checkpoint-set-id")
    if not runtime_value or not set_id:
        raise RuntimeError("monitor mode requires --runtime-dir and --checkpoint-set-id")
    runtime_dir = Path(runtime_value)
    manifest = read_status(runtime_dir / "campaign-manifest.json")
    plan_path = runtime_dir / "planner" / "campaign-plan.json"
    if manifest is None or not plan_path.is_file():
        raise RuntimeError("monitor mode requires an existing immutable campaign manifest and plan")
    if manifest.get("checkpoint_set_id") != set_id:
        raise RuntimeError("monitor checkpoint set does not match the runtime manifest")
    plans = json.loads(plan_path.read_text(encoding="utf-8"))
    worker_dirs = sorted((runtime_dir / "workers").glob("worker-*"))
    status_paths = [worker_dir / "campaign-status.json" for worker_dir in worker_dirs]
    if not status_paths:
        raise RuntimeError("monitor mode found no campaign workers")
    initial_rows = [read_status(path) for path in status_paths]
    starts = [value for row in initial_rows if (value := _status_timestamp(row, "started_at"))]
    elapsed = max((datetime.now(timezone.utc) - min(starts)).total_seconds(), 0.0) if starts else 0.0
    started = time.monotonic() - elapsed
    rates: deque[tuple[float, int]] = deque()
    interactive = sys.stdout.isatty()
    live, last_plain = None, 0.0
    if interactive:
        try:
            from rich.live import Live
        except ImportError as exc:
            raise RuntimeError("Rich is required for the interactive campaign monitor") from exc
        live = Live(refresh_per_second=1, transient=False)
        live.start()
    try:
        while True:
            rows = [read_status(path) for path in status_paths]
            views = [_DetachedProcessView(row) for row in rows]
            status = aggregate_status(status_paths, plans, started, rates, views)
            now = datetime.now(timezone.utc)
            ages = [
                max((now - updated).total_seconds(), 0.0)
                for row in rows
                if (updated := _status_timestamp(row, "updated_at"))
            ]
            status.update(
                {
                    "checkpoint_set_id": set_id,
                    "universe_hash": manifest.get("universe_hash"),
                    "executable_path": str(binary),
                    "executable_sha256": binary_sha256,
                    "monitor_mode": "reattached",
                    "stale_workers": sum(age > 120 for age in ages) + sum(row is None for row in rows),
                    "oldest_worker_status_age_seconds": max(ages, default=None),
                }
            )
            if status["stale_workers"] and status["status"] == "running":
                status["status"] = "stale"
            if live:
                live.update(render_rich(status, set_id), refresh=True)
            elif time.monotonic() - last_plain >= 15:
                print(render_plain(status), flush=True)
                last_plain = time.monotonic()
            if all(row is not None and row.get("status") != "running" for row in rows):
                all_certified = all(status_is_fully_certified(row) for row in rows if row is not None)
                return 0 if all_certified else 1
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nMonitor closed; campaign workers continue independently.", file=sys.stderr, flush=True)
        return 0
    finally:
        if live:
            live.stop()


def run_process_campaign(
    binary: Path,
    binary_sha256: str,
    campaign_args: list[str],
    workers: int,
    environ: dict[str, str],
    recovery_source_runtime: Path | None = None,
    recovery_source_manifest: dict[str, Any] | None = None,
    campaign_source_commit: str | None = None,
) -> int:
    validate_process_worker_count(workers)
    runtime_value = option_value(campaign_args, "--runtime-dir")
    set_id = option_value(campaign_args, "--checkpoint-set-id")
    if not runtime_value or not set_id:
        raise RuntimeError("process mode requires --runtime-dir and --checkpoint-set-id")
    interactive = sys.stdout.isatty()
    if interactive:
        try:
            import rich  # noqa: F401
        except ImportError as exc:
            raise RuntimeError(
                "Rich is required for the interactive campaign dashboard; install it in this environment"
            ) from exc
    runtime_dir = Path(runtime_value)
    control_path = campaign_control_path(runtime_dir)
    control_path.unlink(missing_ok=True)
    planner_dir = runtime_dir / "planner"
    planner_dir.mkdir(parents=True, exist_ok=True)
    plan_path = planner_dir / "campaign-plan.json"
    manifest_path = runtime_dir / "campaign-manifest.json"
    start_date = option_value(campaign_args, "--start-date")
    end_date = option_value(campaign_args, "--end-date")
    if not start_date or not end_date:
        raise RuntimeError("process mode requires --start-date and --end-date")
    existing_manifest = read_status(manifest_path)
    requested_identity = {
        "schema_version": 2,
        "checkpoint_set_id": set_id,
        "start_date": start_date,
        "end_date": end_date,
        "recovery_source_checkpoint_set_id": option_value(
            campaign_args, "--recovery-source-checkpoint-set-id"
        ),
        # The executable hash is the runnable authority. Preserve the commit
        # already bound to an interrupted target when only the launcher source
        # has moved forward between attempts.
        "source_commit": (
            existing_manifest.get("source_commit")
            if existing_manifest is not None
            else source_commit(campaign_source_commit)
        ),
        "executable_sha256": binary_sha256,
        "certification_schema_version": CERTIFICATION_SCHEMA_VERSION,
        "execution_clock_authority": EXECUTION_CLOCK_AUTHORITY,
        "execution_clock_coverage_authority": EXECUTION_CLOCK_COVERAGE_AUTHORITY,
    }
    if recovery_source_runtime is not None:
        if recovery_source_manifest is None:
            raise RuntimeError("recovery source manifest was not loaded")
        requested_identity.update(
            {
                "recovery_source_manifest_sha256": sha256_file(
                    recovery_source_runtime / "campaign-manifest.json"
                ),
                "recovery_source_universe_hash": recovery_source_manifest.get(
                    "universe_hash"
                ),
            }
        )
    if existing_manifest is not None and any(
        existing_manifest.get(key) != value for key, value in requested_identity.items()
    ):
        raise RuntimeError(
            "runtime directory belongs to a different immutable campaign; choose a new runtime directory"
        )
    purge_marker = runtime_dir / "checkpoint-set-purge-completed.json"
    planner_source_args = campaign_args
    if purge_marker.exists():
        planner_source_args = remove_options(
            planner_source_args, set(), {"--purge-existing-checkpoints"}
        )
    planner_args = replace_option(replace_option(planner_source_args, "--runtime-dir", str(planner_dir)), "--workers", "1")
    if "--plan-only" not in planner_args:
        planner_args.append("--plan-only")
    if existing_manifest is None:
        if recovery_source_runtime is not None:
            print(
                "Loading the immutable source campaign plan for successor recovery...",
                flush=True,
            )
            plans = recovery_plan(recovery_source_runtime, recovery_source_manifest or {})
            atomic_json(plan_path, plans)
        else:
            print("Planning immutable ticker universe and exact event workload...", flush=True)
            planning = subprocess.run([str(binary), *planner_args], env=environ, check=False)
            if planning.returncode:
                return planning.returncode
            if "--purge-existing-checkpoints" in planner_args:
                atomic_json(
                    purge_marker,
                    {
                        "checkpoint_set_id": set_id,
                        "purged_at": datetime.now(timezone.utc).isoformat(),
                    },
                )
    elif not plan_path.is_file():
        raise RuntimeError("immutable campaign manifest exists but its campaign plan is missing")
    plans = json.loads(plan_path.read_text(encoding="utf-8"))
    if not plans:
        raise RuntimeError("campaign planner returned an empty universe")
    if run_execution_clock_preflight(binary, campaign_args, plans, planner_dir, environ):
        print(
            "Execution-clock preflight failed; no campaign workers were started and the source campaign remains unchanged.",
            file=sys.stderr,
            flush=True,
        )
        return 2
    shards = prepare_shards(plans, workers)
    universe_hash = hashlib.sha256(
        "".join(f"{plan['ticker']}\n" for plan in plans).encode("utf-8")
    ).hexdigest()
    if existing_manifest is None:
        atomic_json(
            manifest_path,
            {
                **requested_identity,
                "universe_hash": universe_hash,
                "ticker_count": len(plans),
                "estimated_events": sum(int(plan.get("estimated_events", 0)) for plan in plans),
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )
    elif existing_manifest.get("universe_hash") != universe_hash:
        raise RuntimeError("immutable campaign plan no longer matches its persisted universe hash")
    existing_status = read_status(runtime_dir / "campaign-status.json")
    if (
        existing_manifest is not None
        and existing_status is not None
        and status_is_fully_certified(existing_status)
    ):
        print(
            f"Checkpoint set {set_id} is already complete and immutable; use a new set id for a rebuild.",
            flush=True,
        )
        return 0

    def register_set(state: str, event_count: int) -> int:
        return subprocess.run(
            [
                str(binary),
                "--start-date", start_date,
                "--end-date", end_date,
                "--checkpoint-set-id", set_id,
                "--runtime-dir", str(runtime_dir),
                "--register-set-state", state,
                "--set-universe-hash", universe_hash,
                "--set-ticker-count", str(len(plans)),
                "--set-event-count", str(event_count),
            ],
            env=environ,
            check=False,
        ).returncode

    if register_set("building", 0):
        raise RuntimeError("failed to register the checkpoint set before worker launch")
    worker_root = runtime_dir / "workers"
    worker_root.mkdir(parents=True, exist_ok=True)
    worker_dirs = [worker_root / f"worker-{index + 1:02d}" for index in range(len(shards))]
    previous_attempt_archive = archive_previous_attempt_statuses(runtime_dir, worker_dirs)
    base_args = remove_options(
        campaign_args,
        {"--workers", "--runtime-dir", "--ticker-file", "--priority-ticker", "--core-index", "--campaign-control-path"},
        {"--purge-existing-checkpoints", "--plan-only", "--explicit-universe-only"},
    )
    processes, logs, status_paths = [], [], []
    try:
        for index, shard in enumerate(shards):
            worker_dir = worker_dirs[index]
            worker_dir.mkdir(parents=True, exist_ok=True)
            ticker_file = worker_dir / "tickers.txt"
            ticker_file.write_text("\n".join(str(plan["ticker"]) for plan in shard) + "\n", encoding="utf-8")
            log = (worker_dir / "worker.log").open("a", encoding="utf-8")
            logs.append(log)
            child_args = [
                *base_args,
                "--ticker-file", str(ticker_file),
                "--explicit-universe-only",
                "--runtime-dir", str(worker_dir),
                "--workers", "1",
                "--core-index", str(index),
                "--campaign-control-path", str(control_path),
                "--shard-worker",
            ]
            processes.append(
                subprocess.Popen(
                    [str(binary), *child_args],
                    env=environ,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    text=True,
                    creationflags=worker_process_creationflags(),
                )
            )
            status_paths.append(worker_dir / "campaign-status.json")
    except OSError:
        for process in processes:
            if process.poll() is None:
                process.terminate()
        for process in processes:
            process.wait()
        for log in logs:
            log.close()
        register_set("failed", 0)
        raise

    aggregate_path = runtime_dir / "campaign-status.json"
    started, rates, interrupted, registry_failed = time.monotonic(), deque(), False, False
    live, last_plain = None, 0.0
    try:
        if interactive:
            from rich.live import Live

            live = Live(refresh_per_second=1, transient=False)
            live.start()
        while any(process.poll() is None for process in processes):
            status = aggregate_status(status_paths, plans, started, rates, processes)
            status["checkpoint_set_id"] = set_id
            status["universe_hash"] = universe_hash
            status["executable_path"] = str(binary)
            status["executable_sha256"] = binary_sha256
            if previous_attempt_archive is not None:
                status["previous_attempt_archive"] = str(previous_attempt_archive)
            atomic_json(aggregate_path, status)
            if live:
                live.update(render_rich(status, set_id), refresh=True)
            elif time.monotonic() - last_plain >= 15:
                print(render_plain(status), flush=True)
                last_plain = time.monotonic()
            time.sleep(1)
    except KeyboardInterrupt:
        interrupted = True
        request_campaign_stop(runtime_dir, set_id, "fast")
        print("\nFast stop requested; workers will stop at ordinal-chunk boundaries...", file=sys.stderr, flush=True)
    finally:
        control = read_status(control_path)
        interrupted = interrupted or bool(
            control
            and control.get("checkpoint_set_id") == set_id
            and control.get("action") in {"stop_fast", "stop_graceful"}
        )
        if interrupted:
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline and any(p.poll() is None for p in processes):
                time.sleep(0.25)
            for process in processes:
                if process.poll() is None:
                    process.terminate()
        for process in processes:
            process.wait()
        final = aggregate_status(status_paths, plans, started, rates, processes)
        final["checkpoint_set_id"] = set_id
        final["universe_hash"] = universe_hash
        final["executable_path"] = str(binary)
        final["executable_sha256"] = binary_sha256
        if previous_attempt_archive is not None:
            final["previous_attempt_archive"] = str(previous_attempt_archive)
        if interrupted:
            final["status"] = "interrupted"
        set_state = "interrupted" if interrupted else (
            "failed" if any((process.returncode or 0) != 0 for process in processes) else "sealed"
        )
        set_event_count = (
            int(final["total_estimated_events"])
            if set_state == "sealed"
            else int(final["events_processed"])
        )
        register_code = register_set(set_state, set_event_count)
        if register_code:
            registry_failed = True
            final["status"] = "failed"
            final.setdefault("issues", []).append({"ticker": "checkpoint-set", "error": "registry finalization failed"})
        atomic_json(aggregate_path, final)
        if live:
            live.update(render_rich(final, set_id), refresh=True)
            live.stop()
        else:
            print(render_plain(final), flush=True)
        for log in logs:
            log.close()
    if interrupted:
        return 130
    return 1 if registry_failed or any((process.returncode or 0) != 0 for process in processes) else 0


def main(argv: list[str] | None = None) -> int:
    launcher, campaign_args = parse_launcher_args(list(sys.argv[1:] if argv is None else argv))
    if launcher.launcher_help:
        print(
            "Launcher options: --binary PATH, --no-build, --monitor-existing, "
            "--rebuild, --stop-existing {graceful,fast}, --foreground-supervisor, "
            "--resume-from-runtime PATH, --source-commit COMMIT, "
            f"--process-workers 1..{MAX_PROCESS_WORKERS}"
        )
        print("All other options are forwarded to structure-checkpoint-campaign v6.")
        return 0
    environ = dict(os.environ)
    environ["PYTHONDONTWRITEBYTECODE"] = "1"
    runtime_root = Path(environ.get("TRADING_RUNTIME_ROOT", r"D:\TradingML\runtimes"))
    environ.setdefault(
        "CARGO_TARGET_DIR",
        str(runtime_root / "cargo-target" / "quant-research-workbench"),
    )
    try:
        recovery_source_runtime = None
        recovery_source_manifest = None
        if launcher.resume_from_runtime:
            campaign_args, recovery_source_runtime, recovery_source_manifest = (
                prepare_recovery_resume(campaign_args, launcher.resume_from_runtime)
            )
        if launcher.rebuild and launcher.no_build:
            raise RuntimeError("--rebuild and --no-build are mutually exclusive")
        campaign_source_commit = (
            source_commit(launcher.source_commit)
            if not launcher.monitor_existing and not launcher.stop_existing
            else launcher.source_commit
        )
        binary = resolve_binary(
            launcher.binary,
            not launcher.no_build,
            environ,
            force_rebuild=launcher.rebuild,
        )
        binary_sha256 = sha256_file(binary)
        print(f"Campaign executable: {binary} (SHA-256 {binary_sha256})", flush=True)
        print(
            f"Checkpoint migration: {HOLD_SCORE_REVISION} derived evidence is "
            "repaired from raw counts on load; no purge required.",
            flush=True,
        )
        if launcher.stop_existing:
            runtime_value = option_value(campaign_args, "--runtime-dir")
            set_id = option_value(campaign_args, "--checkpoint-set-id")
            if not runtime_value or not set_id:
                raise RuntimeError("stop mode requires --runtime-dir and --checkpoint-set-id")
            path = request_campaign_stop(Path(runtime_value), set_id, launcher.stop_existing)
            print(f"Published {launcher.stop_existing} stop request: {path}", flush=True)
            return 0
        if launcher.monitor_existing:
            return monitor_existing_campaign(binary, binary_sha256, campaign_args, environ)
        workers = (
            launcher.process_workers
            if launcher.process_workers is not None
            else int(option_value(campaign_args, "--workers") or "1")
        )
        validate_process_worker_count(workers)
        if workers > 1 and "--plan-only" not in campaign_args:
            if not launcher.supervisor_child and not launcher.foreground_supervisor:
                return launch_detached_supervisor(
                    binary,
                    binary_sha256,
                    campaign_args,
                    workers,
                    environ,
                    recovery_source_runtime,
                    campaign_source_commit,
                )
            result = run_process_campaign(
                binary,
                binary_sha256,
                campaign_args,
                workers,
                environ,
                recovery_source_runtime,
                recovery_source_manifest,
                campaign_source_commit,
            )
            if launcher.supervisor_child:
                runtime_value = option_value(campaign_args, "--runtime-dir")
                identity_path = Path(runtime_value) / "supervisor" / "supervisor.json"
                identity = read_status(identity_path) or {}
                atomic_json(
                    identity_path,
                    {
                        **identity,
                        "status": (
                            "completed"
                            if result == 0
                            else "interrupted"
                            if result == 130
                            else "failed"
                        ),
                        "finished_at": datetime.now(timezone.utc).isoformat(),
                        "exit_code": result,
                    },
                )
            return result
        return subprocess.run([str(binary), *campaign_args], env=environ, check=False).returncode
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"Unable to launch structure checkpoint campaign: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
