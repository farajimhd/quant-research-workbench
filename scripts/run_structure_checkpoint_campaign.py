#!/usr/bin/env python3
"""Run the immutable algorithm-18 campaign with a shared process work queue.

The explicit structural-prominence-v18 build leaves old campaign executables
and default live/history service algorithms unchanged.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

sys.dont_write_bytecode = True
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = REPO_ROOT / "services" / "qmd_history_gateway" / "Cargo.toml"
BUILD_BINARY_NAME = "structure_checkpoint_campaign_v18.exe" if os.name == "nt" else "structure_checkpoint_campaign_v18"
RUNTIME_BINARY_NAME = (
    "structure_checkpoint_campaign_v18.exe" if os.name == "nt" else "structure_checkpoint_campaign_v18"
)
MAX_PROCESS_WORKERS = 96
ALGORITHM_VERSION = 18
CAMPAIGN_VERSION = 11
HOLD_SCORE_REVISION = "beta22-wilson90-v1"
RELATIVE_SCORE_REVISION = "frozen-prior-session-role-ecdf-midrank-v1"
CERTIFICATION_SCHEMA_VERSION = 3
STRUCTURE_INPUT_POLICY = "historical-sip-condition-v1"


def apply_priority_ranking(args: list[str], path: Path) -> list[str]:
    report = json.loads(path.read_text(encoding="utf-8"))
    tickers = report.get("priority_tickers", [])
    if (report.get("schema_version") != 1
        or report.get("metric") != "canonical_reported_trade_dollar_volume"
        or report.get("session_start") != "04:00"
        or report.get("session_end_exclusive") != "20:00"
        or report.get("timezone") != "America/New_York"
        or not isinstance(tickers, list) or len(tickers) != 10
        or any(not isinstance(t, str) or not re.fullmatch(r"[A-Z0-9._-]{1,32}", t) for t in tickers)
        or len(set(tickers)) != 10):
        raise RuntimeError("Invalid canonical ten-ticker full-session priority report")
    if "--priority-ticker" in args:
        raise RuntimeError("Use either --priority-ranking or explicit --priority-ticker options")
    if runtime := option_value(args, "--runtime-dir"):
        target = Path(runtime) / "priority-ranking.json"
        if target.exists() and json.loads(target.read_text(encoding="utf-8")) != report:
            raise RuntimeError("Runtime already pins a different priority report")
        atomic_json(target, report)
    result = list(args)
    # The report defines the requested ranking session. Do not silently scan
    # the default entire month merely to order the remaining tickers.
    if option_value(result, "--liquidity-start-date") is None and option_value(result, "--liquidity-end-date") is None:
        from datetime import date
        day = date.fromisoformat(report["session_date"]).isoformat()
        result += ["--liquidity-start-date", day, "--liquidity-end-date", day]
    for ticker in tickers:
        result += ["--priority-ticker", ticker]
    print(f"Priority session {report['session_date']} (04:00-20:00 ET): {', '.join(tickers)}", flush=True)
    return result


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
    # Default launches ask Cargo to validate/rebuild the current source instead
    # of silently preferring an older executable in runtimes/bin.
    if not force_rebuild and (not build or explicit):
        for candidate in candidates:
            if candidate.is_file():
                return candidate.resolve()
    elif force_rebuild and explicit:
        raise RuntimeError("--rebuild cannot be combined with --binary")
    if not build:
        raise RuntimeError("campaign binary was not found; searched:\n  " + "\n  ".join(map(str, candidates)))
    cargo = resolve_cargo(environ)
    if cargo is None:
        raise RuntimeError(
            "Cargo and the campaign binary are missing. Copy the prebuilt binary to "
            r"D:\TradingML\runtimes\bin\structure_checkpoint_campaign_v18.exe."
        )
    subprocess.run(
        [cargo, "build", "--release", "--features", "structural-prominence-v18", "--bin", "structure_checkpoint_campaign_v18", "--manifest-path", str(MANIFEST)],
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
    parser.add_argument("--priority-ranking", help="Frozen canonical full-session liquidity report")
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
    if source_manifest.get("algorithm_version") != ALGORITHM_VERSION:
        raise RuntimeError("Cannot recover a different structural algorithm; reconstruct in a new set from canonical events")
    if not source_plan_path.is_file():
        raise RuntimeError(f"source campaign plan is unavailable: {source_plan_path}")
    source_status = read_status(source_runtime / "campaign-status.json")
    if source_status is not None and source_status.get("status") in {"running", "degraded", "stale"}:
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
        *source_manifest.get("priority_tickers", []),
        *(ticker for ticker in existing_priorities if ticker not in source_manifest.get("priority_tickers", [])),
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
    priorities = {ticker: index for index, ticker in enumerate(source_manifest.get("priority_tickers", []))}
    indexed = list(enumerate(plans))
    indexed.sort(
        key=lambda item: (
            priorities.get(str(item[1]["ticker"]).upper(), len(priorities)),
            item[0],
        )
    )
    return [plan for _, plan in indexed]


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
    if os.name == "nt":
        # os.kill(pid, 0) terminates processes on Windows; it is not a probe.
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if not handle:
            error = ctypes.get_last_error()
            if error == 87:  # ERROR_INVALID_PARAMETER: PID no longer exists.
                return False
            raise OSError(error, "Cannot verify campaign supervisor liveness")
        try:
            result = kernel.WaitForSingleObject(handle, 0)
            if result not in (0, 258):
                raise OSError(ctypes.get_last_error(), "Cannot query campaign supervisor state")
            return result == 258  # WAIT_TIMEOUT means still running.
        finally:
            kernel.CloseHandle(handle)
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
        return "not estimated"
    seconds = int(seconds)
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def worker_log_tail(path: Path) -> str:
    try:
        with path.open("rb") as source:
            source.seek(0, 2)
            source.seek(max(source.tell() - 8192, 0))
            return source.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def retryable_worker_exit(log: str) -> bool:
    failures = [line for line in log.splitlines() if line.startswith(("Error:", "worker failure:"))]
    text = "\n".join(failures).lower() if failures else log.lower()
    if any(marker in text for marker in ("hash drifted", "hash mismatch", "invalid", "panicked", "authority mismatch", "syntax_error", "unknown_identifier")):
        return False
    return any(marker in text for marker in ("deadlock_avoided", "timed out", "timeout", "error sending request", "connection reset", "connection closed", "temporarily unavailable", "too many simultaneous queries", "memory limit"))


def aggregate_status(paths, plans, started, rates, processes, stopping=False) -> dict[str, Any]:
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
    # Recovery credits saved events without replaying them. Start a new rate
    # window after recovery or counter rollback rather than forecasting that
    # cheap coverage as the cost of replaying the remaining market history.
    recovering = any("recovery" in stage for row in statuses for stage in row.get("stages", {}).values())
    if rates and (events < rates[-1][1] or len(rates[-1]) < 3 or counts["skipped"] != rates[-1][2] or recovering):
        rates.clear()
    rates.append((now, events, counts["skipped"]))
    while len(rates) > 1 and rates[1][0] <= now - 300:
        rates.popleft()
    rate = 0.0
    if len(rates) > 1 and now - rates[0][0] >= 15 and events >= rates[0][1]:
        rate = (events - rates[0][1]) / (now - rates[0][0])
    active, issues = [], []
    stages: dict[str, int] = {}
    worker_details = []
    failed_workers, startup_failures, busy_workers = 0, 0, 0
    for worker, (row, process) in enumerate(zip(worker_statuses, processes, strict=True)):
        exit_code = process.poll()
        completed = row is not None and (status_is_fully_certified(row) or (row.get("status") == "completed" and row.get("total_units") == 0))
        if not stopping and (exit_code not in (None, 0) or (exit_code == 0 and not completed)):
            failed_workers += 1
            startup_failures += int(row is None)
            log_path = paths[worker].parent / "worker.log"
            detail = worker_log_tail(log_path).splitlines()
            issues.append({"ticker": f"W{worker + 1:02d}", "error": detail[-1] if detail else f"Worker exited ({process.poll()}) without complete certification; inspect {log_path}"})
        if row is None:
            continue
        if process.poll() is None:
            busy_workers += int(bool(row.get("active")))
            active += [f"W{worker + 1:02d} {ticker}@{date}" for ticker, date in row.get("active", {}).items()]
            worker_stages = row.get("stages", {})
            for ticker, stage in worker_stages.items():
                stages[stage] = stages.get(stage, 0) + 1
                worker_details.append(f"W{worker + 1:02d} {ticker}: {stage}")
        issues += row.get("issues", [])[-2:]
    exited = sum(process.poll() is not None for process in processes)
    return {
        "schema_version": 1,
        "status": ("degraded" if failed_workers else "running") if exited < len(processes) else ("failed" if failed_workers else "completed"),
        "worker_processes_failed": failed_workers,
        "worker_startup_failures": startup_failures,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "worker_processes": len(processes),
        "worker_processes_exited": exited,
        "ticker_count": len(plans),
        "total_units": total_units,
        "total_estimated_events": total_events,
        "events_processed": events,
        "event_rate_5m": rate,
        "eta_seconds": (max(total_events - events, 0) / rate
                        if rate > 0 and not recovering and not failed_workers and not counts["failed"] and events < total_events else None),
        "eta_basis": "recent replay coverage rate; approximate",
        "worker_processes_busy": busy_workers,
        "worker_processes_waiting": max(len(processes) - exited - busy_workers, 0),
        "elapsed_seconds": now - started,
        "counts": counts,
        "active": active,
        "stages": stages,
        "worker_details": worker_details,
        "replayed_events": sum(int(row.get("events_advanced", 0)) for row in statuses),
        "issues": issues[-10:],
    }


def eta_label(status: dict[str, Any]) -> str:
    if status.get("worker_processes_failed", 0) or status["counts"].get("failed", 0):
        return "blocked by failed work"
    if status["counts"].get("certified", 0) == status["total_units"]:
        return "complete"
    if status.get("eta_seconds") is not None:
        return f"~{fmt_duration(status['eta_seconds'])} (recent rate)"
    if status.get("events_processed", 0) >= status.get("total_estimated_events", 0) > 0:
        return "finishing certification"
    if any("recovery" in stage for stage in status.get("stages", {})):
        return "calibrating after recovery"
    return "measuring throughput"


def record_priority_completions(status, work_dir, priorities, start_date, end_date):
    status["priority_tickers"] = list(priorities)
    status["priority_completed"] = [ticker for ticker in priorities
                                    if (work_dir / "completed" / f"{ticker}.done").is_file()]
    start = datetime.fromisoformat(start_date).strftime("%b %d, %Y")
    end = datetime.fromisoformat(end_date).strftime("%b %d, %Y")
    status["priority_completion_messages"] = [
        f"{ticker} completed from {start} through {end}"
        for ticker in status["priority_completed"]
    ]


def print_new_priority_completions(status, announced, live=None):
    for message in status.get("priority_completion_messages", []):
        if message in announced:
            continue
        if live:
            live.console.print(message, style="green", markup=False)
        else:
            print(message, flush=True)
        announced.add(message)


def render_plain(status: dict[str, Any]) -> str:
    counts = status["counts"]
    return (
        f"{status['updated_at']} status={status['status']} processes={status['worker_processes_exited']}/"
        f"{status['worker_processes']} exited units={counts['finished']}/{status['total_units']} "
        f"busy={status.get('worker_processes_busy', 0)} waiting={status.get('worker_processes_waiting', 0)} "
        f"queued={counts['queued']} failed={counts['failed']} blocked={counts['blocked']} "
        f"worker_failures={status.get('worker_processes_failed', 0)} startup_failures={status.get('worker_startup_failures', 0)} restarts={status.get('worker_restarts', 0)} "
        f"retries={counts['retried']} events={fmt_count(status['events_processed'])}/"
        f"{fmt_count(status['total_estimated_events'])} rate={fmt_count(status['event_rate_5m'])}/s "
        f"elapsed={fmt_duration(status['elapsed_seconds'])} eta={eta_label(status)}"
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
    summary.add_row("Build SHA-256", str(status.get("executable_sha256", "unknown"))[:16])
    summary.add_row("Processes", f"{status['worker_processes'] - status['worker_processes_exited']} alive | {status['worker_processes_exited']} exited | {status.get('worker_processes_failed', 0)} failed")
    summary.add_row("Worker activity", f"{status.get('worker_processes_busy', 0)} assigned work | {status.get('worker_processes_waiting', 0)} waiting / starting")
    if status.get("priority_tickers"):
        completed = status.get("priority_completed", [])
        summary.add_row("Priority completed", f"{len(completed)}/{len(status['priority_tickers'])}: {', '.join(completed) or 'none yet'}")
    summary.add_row("Startup / restarts", f"{status.get('worker_startup_failures', 0)} | {status.get('worker_restarts', 0)}")
    summary.add_row(
        "Certified days",
        f"{fmt_count(counts['certified'])} / {fmt_count(status['total_units'])}",
    )
    summary.add_row("Recovered days", fmt_count(counts["skipped"]))
    summary.add_row("Events covered", f"{fmt_count(status['events_processed'])} / {fmt_count(total)}  {pct:.1f}%")
    summary.add_row("Coverage rate", f"{fmt_count(status['event_rate_5m'])}/s aggregate (5m)")
    summary.add_row("Elapsed | ETA", f"{fmt_duration(status['elapsed_seconds'])} | {eta_label(status)}")
    if status.get("monitor_mode") == "reattached":
        oldest = status.get("oldest_worker_status_age_seconds")
        summary.add_row(
            "Worker status",
            f"{status.get('stale_workers', 0)} stale | oldest {oldest:.0f}s"
            if oldest is not None
            else "waiting for first worker status",
        )
    summary.add_row("Queue", f"{fmt_count(counts['queued'])} queued | {counts['retried']} retries | {counts['failed']} failed | {counts['blocked']} blocked")
    stages = status.get("stages", {})
    summary.add_row("Worker stages", " | ".join(f"{name}: {count}" for name, count in sorted(stages.items())) or "Starting / between tickers")
    summary.add_row("Replay events saved", fmt_count(status.get("replayed_events", 0)))
    details = status.get("worker_details", status["active"])
    active = "  ".join(details[:2]) or "Workers starting or between tickers"
    summary.add_row("Worker detail file", "campaign-status.json")
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
                f"Structural Checkpoint Campaign v{CAMPAIGN_VERSION}  {status['status'].upper()}"
                + ("  REATTACHED" if status.get("monitor_mode") == "reattached" else ""),
                style="bold cyan",
            ),
            summary,
            Text(f"Examples ({min(2, len(details))}/{len(details)})  {active}", overflow="ellipsis", no_wrap=True),
            Text(f"Latest issue  {issue_text}", style="red" if issue else "dim", overflow="ellipsis", no_wrap=True),
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
    rates: deque[tuple[float, int, int]] = deque()
    announced: set[str] = set()
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
            supervisor_status = read_status(runtime_dir / "campaign-status.json") or {}
            if supervisor_status.get("checkpoint_set_id") == set_id:
                for key in ("priority_tickers", "priority_completed", "priority_completion_messages"):
                    status[key] = supervisor_status.get(key, [])
            print_new_priority_completions(status, announced, live)
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
        "schema_version": 3,
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
        "structure_input_policy": STRUCTURE_INPUT_POLICY,
        "algorithm_version": ALGORITHM_VERSION,
        "campaign_version": CAMPAIGN_VERSION,
        "priority_tickers": [campaign_args[i + 1] for i, arg in enumerate(campaign_args[:-1]) if arg == "--priority-ticker"],
        "priority_report_sha256": sha256_file(runtime_dir / "priority-ranking.json") if (runtime_dir / "priority-ranking.json").is_file() else None,
        "recovery_fallback_checkpoint_set_ids": [campaign_args[i + 1] for i, arg in enumerate(campaign_args[:-1]) if arg == "--recovery-fallback-checkpoint-set-id"],
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
    print(
        "Historical structure input: SIP availability order plus canonical trade-condition eligibility; "
        "archive execution-clock coverage is not required.",
        flush=True,
    )
    planned_tickers = [str(p["ticker"]) for p in plans]
    priorities = requested_identity["priority_tickers"]
    if planned_tickers[:len(priorities)] != priorities:
        raise RuntimeError("Campaign plan must start with every requested priority ticker in ranking order")
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
        {"--workers", "--runtime-dir", "--ticker-file", "--priority-ticker", "--core-index", "--campaign-control-path", "--plan-file"},
        {"--purge-existing-checkpoints", "--plan-only", "--explicit-universe-only"},
    )
    # A durable shared queue prevents short shards from idling behind long ones.
    work_dir = runtime_dir / "work-queues" / uuid.uuid4().hex
    pending = work_dir / "pending"
    pending.mkdir(parents=True)
    atomic_json(work_dir / "priority-tickers.json", requested_identity["priority_tickers"])
    for index, plan in enumerate(plans):
        atomic_json(pending / f"{index:08d}.json", plan)
    processes, logs, status_paths, commands = [], [], [], []
    restarts = [0] * len(shards)
    try:
        for index, shard in enumerate(shards):
            worker_dir = worker_dirs[index]
            worker_dir.mkdir(parents=True, exist_ok=True)
            ticker_file = worker_dir / "tickers.txt"
            ticker_file.write_text("\n".join(str(plan["ticker"]) for plan in shard) + "\n", encoding="utf-8")
            assigned_plan = worker_dir / "assigned-plan.json"
            atomic_json(assigned_plan, shard)
            log = (worker_dir / "worker.log").open("a", encoding="utf-8")
            logs.append(log)
            child_args = [
                *base_args,
                "--ticker-file", str(ticker_file),
                "--explicit-universe-only",
                "--runtime-dir", str(worker_dir),
                "--workers", "1",
                "--shared-work-dir", str(work_dir),
                "--campaign-control-path", str(control_path),
                "--shard-worker",
                "--plan-file", str(assigned_plan),
            ]
            commands.append([str(binary), *child_args])
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
    announced: set[str] = set()
    live, last_plain = None, 0.0
    try:
        if interactive:
            from rich.live import Live

            live = Live(refresh_per_second=1, transient=False)
            live.start()
        while True:
            control = read_status(control_path)
            stopping = bool(control and control.get("checkpoint_set_id") == set_id and control.get("action") in {"stop_fast", "stop_graceful"})
            if stopping and control["action"] == "stop_fast":
                # Enter bounded shutdown now. Waiting for every child before
                # reaching finally made the forced-stop deadline unreachable.
                interrupted = True
                break
            if (work_dir / "failure.txt").exists() and not stopping:
                request_campaign_stop(runtime_dir, set_id, "fast")
                stopping = True
            for index, process in enumerate(processes):
                if not stopping and process.poll() not in (None, 0) and restarts[index] < 2 and retryable_worker_exit(worker_log_tail(worker_dirs[index] / "worker.log")):
                    restarts[index] += 1
                    archive = worker_dirs[index] / f"attempt-{restarts[index]}-{uuid.uuid4().hex[:8]}"
                    archive.mkdir(exist_ok=False)
                    if status_paths[index].exists():
                        shutil.move(str(status_paths[index]), str(archive / "campaign-status.json"))
                    logs[index].close()
                    shutil.move(str(worker_dirs[index] / "worker.log"), str(archive / "worker.log"))
                    logs[index] = (worker_dirs[index] / "worker.log").open("a", encoding="utf-8")
                    processes[index] = subprocess.Popen(commands[index], env=environ, stdout=logs[index], stderr=subprocess.STDOUT, text=True, creationflags=worker_process_creationflags())
                elif not stopping and process.poll() not in (None, 0):
                    (work_dir / "failure.txt").write_text(
                        f"Worker {index + 1} exited with code {process.returncode}; inspect its worker.log", encoding="utf-8"
                    )
                    request_campaign_stop(runtime_dir, set_id, "fast")
                    stopping = True
            if not any(process.poll() is None for process in processes):
                break
            status = aggregate_status(status_paths, plans, started, rates, processes, stopping=stopping)
            if stopping:
                status["status"] = "stopping"
            record_priority_completions(status, work_dir, requested_identity["priority_tickers"], start_date, end_date)
            print_new_priority_completions(status, announced, live)
            status["worker_restarts"] = sum(restarts)
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
    except BaseException:
        interrupted = True
        request_campaign_stop(runtime_dir, set_id, "fast")
        raise
    finally:
        control = read_status(control_path)
        interrupted = interrupted or bool(
            control
            and control.get("checkpoint_set_id") == set_id
            and control.get("action") in {"stop_fast", "stop_graceful"}
        )
        if interrupted:
            deadline = time.monotonic() + 60
            print("Stopping campaign: allowing up to 60 seconds for workers, then terminating remaining owned children.", flush=True)
            while time.monotonic() < deadline and any(p.poll() is None for p in processes):
                time.sleep(0.25)
            for process in processes:
                if process.poll() is None:
                    process.terminate()
        for process in processes:
            process.wait()
        final = aggregate_status(status_paths, plans, started, rates, processes, stopping=interrupted and not (work_dir / "failure.txt").exists())
        record_priority_completions(final, work_dir, requested_identity["priority_tickers"], start_date, end_date)
        print_new_priority_completions(final, announced, live)
        final["worker_restarts"] = sum(restarts)
        final["checkpoint_set_id"] = set_id
        final["universe_hash"] = universe_hash
        final["executable_path"] = str(binary)
        final["executable_sha256"] = binary_sha256
        if previous_attempt_archive is not None:
            final["previous_attempt_archive"] = str(previous_attempt_archive)
        if interrupted:
            final["status"] = "interrupted"
        set_state = "interrupted" if interrupted else (
            "sealed" if status_is_fully_certified(final) and not final["worker_processes_failed"] else "failed"
        )
        if (work_dir / "failure.txt").exists():
            set_state = "failed"
            final["status"] = "failed"
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
    if interrupted and set_state != "failed":
        return 130
    return 1 if registry_failed or set_state != "sealed" else 0


def main(argv: list[str] | None = None) -> int:
    launcher, campaign_args = parse_launcher_args(list(sys.argv[1:] if argv is None else argv))
    if launcher.launcher_help:
        print(
            "Launcher options: --binary PATH, --no-build, --monitor-existing, "
            "--rebuild, --stop-existing {graceful,fast}, --foreground-supervisor, "
            "--resume-from-runtime PATH, --source-commit COMMIT, --priority-ranking JSON, "
            f"--process-workers 1..{MAX_PROCESS_WORKERS}"
        )
        print("All other options are forwarded to structure-checkpoint-campaign v10 (algorithm 18).")
        return 0
    if launcher.stop_existing:
        runtime_value = option_value(campaign_args, "--runtime-dir")
        set_id = option_value(campaign_args, "--checkpoint-set-id")
        if not runtime_value or not set_id:
            print("stop mode requires --runtime-dir and --checkpoint-set-id", file=sys.stderr)
            return 1
        path = request_campaign_stop(Path(runtime_value), set_id, launcher.stop_existing)
        print(f"Published {launcher.stop_existing} stop request: {path}", flush=True)
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
        if launcher.priority_ranking:
            campaign_args = apply_priority_ranking(campaign_args, Path(launcher.priority_ranking))
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
        build = subprocess.run([str(binary), "--campaign-build-info"], check=True, capture_output=True, text=True)
        build_info = json.loads(build.stdout)
        if build_info.get("algorithm_version") != ALGORITHM_VERSION or build_info.get("campaign_version") != CAMPAIGN_VERSION:
            raise RuntimeError("This campaign requires the version-10 algorithm-18 executable; refusing a different engine")
        binary_sha256 = sha256_file(binary)
        print(f"Campaign executable: {binary} (SHA-256 {binary_sha256})", flush=True)
        print(
            "Algorithm 18: fresh canonical construction required; only compatible "
            "v18 checkpoints may resume. Old sets remain immutable.",
            flush=True,
        )
        if launcher.monitor_existing:
            return monitor_existing_campaign(binary, binary_sha256, campaign_args, environ)
        workers = (
            launcher.process_workers
            if launcher.process_workers is not None
            else int(option_value(campaign_args, "--workers") or "1")
        )
        validate_process_worker_count(workers)
        if "--plan-only" not in campaign_args and "--preflight-only" not in campaign_args:
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
