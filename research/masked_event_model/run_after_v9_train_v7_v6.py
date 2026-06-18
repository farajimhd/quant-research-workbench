from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shlex
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


DEFAULT_CODE_ROOT = Path(r"D:\TradingML\codes\masked_event_model")
DEFAULT_LOG_ROOT = Path(r"D:\TradingML\runtimes\masked_event_model\scheduled_runs")
DEFAULT_FALLBACK_WANDB_PROJECT = "June2026-event-token-mae"
DEFAULT_WANDB_ENTITY = "mehdifaraji"


@dataclass(slots=True)
class PythonProcess:
    pid: int
    command_line: str


@dataclass(slots=True)
class Job:
    version: str
    run_name: str
    command: list[str]
    log_path: Path


def main() -> None:
    args = parse_args()
    code_root = Path(args.code_root).resolve()
    log_root = Path(args.log_root).resolve()
    run_id = args.run_id or f"after-v9-v7-v6-{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}"
    run_dir = log_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = run_dir / "manifest.json"

    v6_script = find_version_launcher(code_root, "v6")
    v7_script = find_version_launcher(code_root, "v7")
    v9_script = find_version_launcher(code_root, "v9")
    for path in (v6_script, v7_script, v9_script):
        if not path.exists():
            raise FileNotFoundError(f"Required training launcher is missing: {path}")

    v9_processes = matching_v9_training_processes()
    detected_project = first_option_value([process.command_line for process in v9_processes], "--wandb-project")
    detected_entity = first_option_value([process.command_line for process in v9_processes], "--wandb-entity")
    wandb_project = args.wandb_project or detected_project or read_default_from_launcher(v9_script, "wandb_project") or DEFAULT_FALLBACK_WANDB_PROJECT
    wandb_entity = args.wandb_entity or detected_entity or read_default_from_launcher(v9_script, "wandb_entity") or DEFAULT_WANDB_ENTITY

    jobs = [
        build_job(
            python_exe=Path(args.python_exe),
            script=v7_script,
            version="v7",
            run_name=args.v7_run_name,
            wandb_project=wandb_project,
            wandb_entity=wandb_entity,
            event_mask_schedule="fixed",
            event_mask_ratio=0.70,
            epochs=args.epochs,
            run_dir=run_dir,
            extra_args=args.extra_train_args,
        ),
        build_job(
            python_exe=Path(args.python_exe),
            script=v6_script,
            version="v6",
            run_name=args.v6_run_name,
            wandb_project=wandb_project,
            wandb_entity=wandb_entity,
            event_mask_schedule="mixed",
            event_mask_ratio=0.70,
            epochs=args.epochs,
            run_dir=run_dir,
            extra_args=args.extra_train_args,
        ),
    ]

    manifest = {
        "run_id": run_id,
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "code_root": str(code_root),
        "log_root": str(log_root),
        "run_dir": str(run_dir),
        "v9_script": str(v9_script),
        "initial_v9_processes": [asdict(process) for process in v9_processes],
        "wandb_project": wandb_project,
        "wandb_entity": wandb_entity,
        "jobs": [job_to_json(job) for job in jobs],
        "status": "planned",
    }
    write_json(manifest_path, manifest)

    print_banner("Masked event scheduled v7/v6 training")
    print(f"run_dir={run_dir}", flush=True)
    print(f"waiting_for_v9={not args.skip_wait}", flush=True)
    print(f"wandb_project={wandb_project}", flush=True)
    print(f"wandb_entity={wandb_entity}", flush=True)
    for job in jobs:
        print(f"{job.version} command: {command_to_text(job.command)}", flush=True)
        print(f"{job.version} log: {job.log_path}", flush=True)

    if args.dry_run:
        manifest["status"] = "dry_run_complete"
        write_json(manifest_path, manifest)
        print("Dry run complete; no waiting or training started.", flush=True)
        return

    if not args.skip_wait:
        wait_for_v9(args.poll_seconds, args.empty_confirm_polls, manifest_path, manifest)

    for job in jobs:
        manifest["status"] = f"running_{job.version}"
        manifest[f"{job.version}_started_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        write_json(manifest_path, manifest)
        return_code = run_job(job)
        manifest[f"{job.version}_finished_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        manifest[f"{job.version}_return_code"] = return_code
        if return_code != 0:
            manifest["status"] = f"failed_{job.version}"
            write_json(manifest_path, manifest)
            raise SystemExit(return_code)
        manifest["status"] = f"finished_{job.version}"
        write_json(manifest_path, manifest)

    manifest["status"] = "complete"
    manifest["completed_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    write_json(manifest_path, manifest)
    print_banner("Scheduled v7/v6 training complete")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Wait for the current workstation v9 training to finish, then run v7 and v6 training sequentially.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--code-root", default=str(default_code_root()))
    parser.add_argument("--log-root", default=str(DEFAULT_LOG_ROOT))
    parser.add_argument("--python-exe", default=sys.executable)
    parser.add_argument("--run-id", default="")
    parser.add_argument("--wandb-project", default="", help="Overrides the detected/current v9 W&B project.")
    parser.add_argument("--wandb-entity", default="", help="Overrides the detected/current v9 W&B entity.")
    parser.add_argument("--v7-run-name", default="v7-fixedmask070-emb32-bs4096-unweightedmean-attnpool-4epochs-after-v9")
    parser.add_argument("--v6-run-name", default="v6-mixedmask-emb32-bs4096-unweightedmean-meanpool-4epochs-after-v7")
    parser.add_argument("--epochs", type=int, default=4)
    parser.add_argument("--poll-seconds", type=int, default=300)
    parser.add_argument("--empty-confirm-polls", type=int, default=2)
    parser.add_argument("--skip-wait", action="store_true", help="Start v7 immediately instead of waiting for v9.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--extra-train-args",
        nargs=argparse.REMAINDER,
        default=[],
        help="Arguments appended to both v7 and v6 launcher commands. Put this option last.",
    )
    return parser.parse_args()


def default_code_root() -> Path:
    script_parent = Path(__file__).resolve().parent
    if (script_parent / "v7" / "train_10shard_long.py").exists():
        return script_parent
    if (script_parent / "v7" / "research" / "masked_event_model" / "v7" / "train_10shard_long.py").exists():
        return script_parent
    return DEFAULT_CODE_ROOT


def find_version_launcher(code_root: Path, version: str) -> Path:
    candidates = [
        code_root / version / "research" / "masked_event_model" / version / "train_10shard_long.py",
        code_root / version / "train_10shard_long.py",
        code_root / "research" / "masked_event_model" / version / "train_10shard_long.py",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def build_job(
    *,
    python_exe: Path,
    script: Path,
    version: str,
    run_name: str,
    wandb_project: str,
    wandb_entity: str,
    event_mask_schedule: str,
    event_mask_ratio: float,
    epochs: int,
    run_dir: Path,
    extra_args: list[str],
) -> Job:
    command = [
        str(python_exe),
        "-u",
        str(script),
        "--fresh-start",
        "--epochs",
        str(int(epochs)),
        "--event-mask-ratio",
        f"{event_mask_ratio:.2f}",
        "--event-mask-schedule",
        event_mask_schedule,
        "--wandb-project",
        wandb_project,
        "--wandb-mode",
        "online",
        "--run-name",
        run_name,
    ]
    command.extend(extra_args)
    return Job(version=version, run_name=run_name, command=command, log_path=run_dir / f"{version}.log")


def matching_v9_training_processes() -> list[PythonProcess]:
    current_pid = os.getpid()
    matches: list[PythonProcess] = []
    for process in list_python_processes():
        command = process.command_line.lower()
        if process.pid == current_pid:
            continue
        if "masked_event_model" not in command:
            continue
        if "\\v9\\" not in command and "/v9/" not in command:
            continue
        if "train_10shard_long.py" not in command and "train.py" not in command:
            continue
        if "run_after_v9_train_v7_v6.py" in command:
            continue
        matches.append(process)
    return matches


def list_python_processes() -> list[PythonProcess]:
    command = [
        "powershell",
        "-NoProfile",
        "-Command",
        (
            "Get-CimInstance Win32_Process "
            "-Filter \"Name = 'python.exe' OR Name = 'pythonw.exe'\" | "
            "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"
        ),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Failed to query Python processes: {result.stderr.strip()}")
    text = result.stdout.strip()
    if not text:
        return []
    payload: Any = json.loads(text)
    rows = payload if isinstance(payload, list) else [payload]
    processes: list[PythonProcess] = []
    for row in rows:
        command_line = str(row.get("CommandLine") or "")
        if not command_line:
            continue
        processes.append(PythonProcess(pid=int(row["ProcessId"]), command_line=command_line))
    return processes


def wait_for_v9(poll_seconds: int, empty_confirm_polls: int, manifest_path: Path, manifest: dict[str, Any]) -> None:
    print_banner("Waiting for v9 training to finish")
    empty_count = 0
    poll_index = 0
    while empty_count < max(1, int(empty_confirm_polls)):
        matches = matching_v9_training_processes()
        poll_index += 1
        manifest["last_wait_poll_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        manifest["last_v9_processes"] = [asdict(process) for process in matches]
        write_json(manifest_path, manifest)
        if matches:
            empty_count = 0
            pids = ", ".join(str(process.pid) for process in matches)
            print(f"{timestamp()} v9 still running; pids={pids}; next check in {poll_seconds}s", flush=True)
            time.sleep(max(1, int(poll_seconds)))
            continue
        empty_count += 1
        if empty_count < max(1, int(empty_confirm_polls)):
            print(f"{timestamp()} no v9 process found; confirming ({empty_count}/{empty_confirm_polls})", flush=True)
            time.sleep(max(1, int(poll_seconds)))
    print(f"{timestamp()} v9 is finished; starting queued jobs.", flush=True)


def run_job(job: Job) -> int:
    print_banner(f"Starting {job.version}: {job.run_name}")
    job.log_path.parent.mkdir(parents=True, exist_ok=True)
    start = time.perf_counter()
    with job.log_path.open("w", encoding="utf-8", buffering=1) as log:
        log.write(f"started_at={dt.datetime.now(dt.timezone.utc).isoformat()}\n")
        log.write(f"command={command_to_text(job.command)}\n")
        process = subprocess.Popen(
            job.command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        try:
            assert process.stdout is not None
            for line in process.stdout:
                print(line, end="", flush=True)
                log.write(line)
        except KeyboardInterrupt:
            print(f"{timestamp()} KeyboardInterrupt received; terminating {job.version} child process.", flush=True)
            terminate_process(process)
            raise
        return_code = process.wait()
        elapsed = time.perf_counter() - start
        log.write(f"\nfinished_at={dt.datetime.now(dt.timezone.utc).isoformat()}\n")
        log.write(f"return_code={return_code}\n")
        log.write(f"elapsed_seconds={elapsed:.3f}\n")
    print(f"{timestamp()} {job.version} finished return_code={return_code} elapsed_hours={elapsed / 3600.0:.2f}", flush=True)
    return int(return_code)


def terminate_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=60)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=30)


def first_option_value(commands: list[str], option: str) -> str:
    for command in commands:
        value = option_value(command, option)
        if value:
            return value
    return ""


def option_value(command: str, option: str) -> str:
    parts = shlex.split(command, posix=False)
    prefix = f"{option}="
    for index, part in enumerate(parts):
        cleaned = part.strip('"')
        if cleaned == option and index + 1 < len(parts):
            return parts[index + 1].strip('"')
        if cleaned.startswith(prefix):
            return cleaned[len(prefix) :].strip('"')
    return ""


def read_default_from_launcher(script: Path, key: str) -> str:
    if not script.exists():
        return ""
    pattern = re.compile(rf'["\']{re.escape(key)}["\']\s*:\s*["\']([^"\']+)["\']')
    match = pattern.search(script.read_text(encoding="utf-8"))
    return match.group(1) if match else ""


def command_to_text(command: list[str]) -> str:
    return subprocess.list2cmdline(command)


def job_to_json(job: Job) -> dict[str, Any]:
    return {
        "version": job.version,
        "run_name": job.run_name,
        "command": job.command,
        "log_path": str(job.log_path),
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp_path.replace(path)


def print_banner(text: str) -> None:
    print("=" * 100, flush=True)
    print(text, flush=True)
    print("=" * 100, flush=True)


def timestamp() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


if __name__ == "__main__":
    main()
