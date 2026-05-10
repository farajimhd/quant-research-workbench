from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from src.data_provider.config import BuildRequest
from src.data_provider.file_lock import file_lock


JOB_DIR = "jobs"
JOB_FILE = "job.json"
EVENTS_FILE = "events.jsonl"
CANCEL_FILE = "cancel.requested"
LOG_FILE = "worker.log"


class BuildCancelled(RuntimeError):
    pass


def jobs_root(processed_root: Path) -> Path:
    return processed_root / JOB_DIR


def job_dir(processed_root: Path, job_id: str) -> Path:
    return jobs_root(processed_root) / job_id


def job_file(path: Path) -> Path:
    return path / JOB_FILE


def events_file(path: Path) -> Path:
    return path / EVENTS_FILE


def events_lock_file(path: Path) -> Path:
    return path / f"{EVENTS_FILE}.lock"


def cancel_file(path: Path) -> Path:
    return path / CANCEL_FILE


def log_file(path: Path) -> Path:
    return path / LOG_FILE


def utc_now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def request_to_dict(request: BuildRequest) -> dict[str, Any]:
    raw = asdict(request)
    raw["raw_root"] = str(request.raw_root)
    raw["processed_root"] = str(request.processed_root)
    raw["start_date"] = request.start_date.isoformat()
    raw["end_date"] = request.end_date.isoformat()
    return raw


def read_job(path: Path) -> dict[str, Any]:
    if not job_file(path).exists():
        return {}
    return json.loads(job_file(path).read_text(encoding="utf-8"))


def write_job(path: Path, payload: dict[str, Any]) -> None:
    path.mkdir(parents=True, exist_ok=True)
    tmp = job_file(path).with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(job_file(path))


def update_job(path: Path, **updates: Any) -> dict[str, Any]:
    payload = read_job(path)
    payload.update(updates)
    payload["updated_at"] = utc_now()
    write_job(path, payload)
    return payload


def append_event(path: Path, event: dict[str, Any]) -> None:
    path.mkdir(parents=True, exist_ok=True)
    payload = dict(event)
    payload.setdefault("emitted_at", utc_now())
    with file_lock(events_lock_file(path)):
        with events_file(path).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, default=str, sort_keys=True) + "\n")


def read_events(path: Path) -> list[dict[str, Any]]:
    if not events_file(path).exists():
        return []
    rows = []
    with file_lock(events_lock_file(path)):
        lines = events_file(path).read_text(encoding="utf-8").splitlines()
    for line in lines:
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def is_cancel_requested(path: Path) -> bool:
    return cancel_file(path).exists()


def check_cancelled(path: Path) -> None:
    if is_cancel_requested(path):
        raise BuildCancelled("Build job was cancelled.")


def submit_build_job(request: BuildRequest, *, max_workers: int = 4, polars_threads: int = 6) -> dict[str, Any]:
    job_id = uuid.uuid4().hex
    path = job_dir(request.processed_root, job_id)
    payload = {
        "job_id": job_id,
        "status": "queued",
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "started_at": None,
        "finished_at": None,
        "request": request_to_dict(request),
        "resources": {"max_workers": max_workers, "polars_threads": polars_threads},
    }
    write_job(path, payload)
    env = os.environ.copy()
    env["POLARS_MAX_THREADS"] = str(polars_threads)
    with log_file(path).open("a", encoding="utf-8") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "src.data_provider.worker", str(path)],
            cwd=str(Path(__file__).resolve().parents[2]),
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    payload["pid"] = process.pid
    payload["status"] = "running"
    payload["started_at"] = utc_now()
    write_job(path, payload)
    return payload


def cancel_build_job(processed_root: Path, job_id: str) -> dict[str, Any]:
    path = job_dir(processed_root, job_id)
    cancel_file(path).write_text(utc_now(), encoding="utf-8")
    append_event(path, {"event": "cancel_requested", "phase": "cancel", "status": "canceling"})
    return update_job(path, status="canceling")


def get_build_status(processed_root: Path, job_id: str) -> dict[str, Any]:
    path = job_dir(processed_root, job_id)
    payload = read_job(path)
    payload["events"] = read_events(path)
    payload["job_dir"] = str(path)
    payload["log_path"] = str(log_file(path))
    return payload


def list_build_jobs(processed_root: Path) -> list[dict[str, Any]]:
    root = jobs_root(processed_root)
    if not root.exists():
        return []
    jobs = [read_job(path) for path in root.iterdir() if path.is_dir() and job_file(path).exists()]
    return sorted(jobs, key=lambda item: str(item.get("created_at") or ""), reverse=True)
