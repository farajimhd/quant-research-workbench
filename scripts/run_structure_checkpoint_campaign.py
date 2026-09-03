#!/usr/bin/env python3
"""Run the canonical Rust level-book algorithm in process-sharded workers."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import signal
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
    "structure_checkpoint_campaign_v5.exe" if os.name == "nt" else "structure_checkpoint_campaign_v5"
)
MAX_PROCESS_WORKERS = 80


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


def resolve_binary(explicit: str | None, build: bool, environ: dict[str, str]) -> Path:
    candidates = binary_candidates(explicit, environ)
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    if not build:
        raise RuntimeError("campaign binary was not found; searched:\n  " + "\n  ".join(map(str, candidates)))
    cargo = resolve_cargo(environ)
    if cargo is None:
        raise RuntimeError(
            "Cargo and the campaign binary are missing. Copy the prebuilt binary to "
            r"D:\TradingML\runtimes\bin\structure_checkpoint_campaign_v5.exe."
        )
    subprocess.run(
        [cargo, "build", "--release", "--bin", "structure_checkpoint_campaign", "--manifest-path", str(MANIFEST)],
        cwd=REPO_ROOT,
        env=environ,
        check=True,
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise RuntimeError("Cargo completed, but the campaign binary was not found")


def parse_launcher_args(argv: list[str]) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--binary")
    parser.add_argument("--no-build", action="store_true")
    parser.add_argument("--launcher-help", action="store_true")
    parser.add_argument("--process-workers", type=int)
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


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
    temporary.replace(path)


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
    summary.add_row("Processes", f"{status['worker_processes'] - status['worker_processes_exited']} active | {status['worker_processes_exited']} exited")
    summary.add_row(
        "Durable units",
        f"{fmt_count(counts['finished'])} / {fmt_count(status['total_units'])} | {fmt_count(counts['certified'])} certified",
    )
    summary.add_row("Events", f"{fmt_count(status['events_processed'])} / {fmt_count(total)}  {pct:.1f}%")
    summary.add_row("Throughput", f"{fmt_count(status['event_rate_5m'])}/s aggregate (5m)")
    summary.add_row("Elapsed | ETA", f"{fmt_duration(status['elapsed_seconds'])} | {fmt_duration(status['eta_seconds'])}")
    summary.add_row("Queue", f"{fmt_count(counts['queued'])} queued | {counts['retried']} retries | {counts['failed']} failed | {counts['blocked']} blocked")
    active = "  ".join(status["active"][:8]) or "Workers starting or between tickers"
    issue = status["issues"][-1] if status["issues"] else None
    issue_text = "None" if issue is None else f"{issue.get('ticker', '?')} {issue.get('session_date') or ''}: {issue.get('error', '')}"
    resume_text = (
        "Previous attempt status archived; this view contains only the current attempt."
        if status.get("previous_attempt_archive")
        else "Fresh attempt; no prior dashboard status was present."
    )
    return Panel(
        Group(
            Text(f"Structural Checkpoint Campaign v5  {status['status'].upper()}", style="bold cyan"),
            summary,
            Text(f"Active  {active}"),
            Text(f"Latest issue  {issue_text}", style="red" if issue else "dim"),
            Text(resume_text, style="dim"),
            Text("Ctrl+C stops children; rerun the identical command to resume.", style="dim"),
        ),
        border_style="cyan",
    )


def run_process_campaign(binary: Path, campaign_args: list[str], workers: int, environ: dict[str, str]) -> int:
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
    planner_dir = runtime_dir / "planner"
    planner_dir.mkdir(parents=True, exist_ok=True)
    plan_path = planner_dir / "campaign-plan.json"
    manifest_path = runtime_dir / "campaign-manifest.json"
    start_date = option_value(campaign_args, "--start-date")
    end_date = option_value(campaign_args, "--end-date")
    if not start_date or not end_date:
        raise RuntimeError("process mode requires --start-date and --end-date")
    requested_identity = {
        "schema_version": 1,
        "checkpoint_set_id": set_id,
        "start_date": start_date,
        "end_date": end_date,
    }
    existing_manifest = read_status(manifest_path)
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
        {"--workers", "--runtime-dir", "--ticker-file", "--priority-ticker", "--core-index"},
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
                "--shard-worker",
            ]
            processes.append(subprocess.Popen([str(binary), *child_args], env=environ, stdout=log, stderr=subprocess.STDOUT, text=True))
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
        print("\nStop requested; waiting for worker checkpoints...", file=sys.stderr, flush=True)
    finally:
        if interrupted:
            if os.name != "nt":
                for process in processes:
                    if process.poll() is None:
                        process.send_signal(signal.SIGINT)
            deadline = time.monotonic() + 20
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
        print(f"Launcher options: --binary PATH, --no-build, --process-workers 1..{MAX_PROCESS_WORKERS}")
        print("All other options are forwarded to structure-checkpoint-campaign v5.")
        return 0
    environ = dict(os.environ)
    environ["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        binary = resolve_binary(launcher.binary, not launcher.no_build, environ)
        workers = (
            launcher.process_workers
            if launcher.process_workers is not None
            else int(option_value(campaign_args, "--workers") or "1")
        )
        validate_process_worker_count(workers)
        if workers > 1 and "--plan-only" not in campaign_args:
            return run_process_campaign(binary, campaign_args, workers, environ)
        return subprocess.run([str(binary), *campaign_args], env=environ, check=False).returncode
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"Unable to launch structure checkpoint campaign: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
