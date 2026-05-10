from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from src.backtest.config import BacktestConfig
from src.data_provider.file_lock import file_lock


JOB_DIR = "jobs"
JOB_FILE = "job.json"
EVENTS_FILE = "events.jsonl"
LOG_FILE = "worker.log"


def utc_now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def jobs_root(output_root: Path) -> Path:
    return output_root / JOB_DIR


def job_dir(output_root: Path, job_id: str) -> Path:
    return jobs_root(output_root) / job_id


def job_file(path: Path) -> Path:
    return path / JOB_FILE


def events_file(path: Path) -> Path:
    return path / EVENTS_FILE


def events_lock_file(path: Path) -> Path:
    return path / f"{EVENTS_FILE}.lock"


def log_file(path: Path) -> Path:
    return path / LOG_FILE


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
    with file_lock(events_lock_file(path)):
        lines = events_file(path).read_text(encoding="utf-8").splitlines()
    events = []
    for line in lines:
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def submit_backtest_job(config: BacktestConfig) -> dict[str, Any]:
    job_id = uuid.uuid4().hex
    path = job_dir(config.output_root, job_id)
    payload = {
        "job_id": job_id,
        "status": "queued",
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "started_at": None,
        "finished_at": None,
        "config": config.to_dict(),
    }
    write_job(path, payload)
    with log_file(path).open("a", encoding="utf-8") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "src.backtest.worker", str(path)],
            cwd=str(Path(__file__).resolve().parents[2]),
            env=os.environ.copy(),
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    payload["pid"] = process.pid
    payload["status"] = "running"
    payload["started_at"] = utc_now()
    write_job(path, payload)
    return payload


def get_backtest_status(output_root: Path, job_id: str) -> dict[str, Any]:
    path = job_dir(output_root, job_id)
    payload = read_job(path)
    payload["events"] = read_events(path)
    payload["job_dir"] = str(path)
    payload["log_path"] = str(log_file(path))
    return payload


def list_backtest_jobs(output_root: Path) -> list[dict[str, Any]]:
    root = jobs_root(output_root)
    if not root.exists():
        return []
    jobs = [read_job(path) for path in root.iterdir() if path.is_dir() and job_file(path).exists()]
    return sorted(jobs, key=lambda item: str(item.get("created_at") or ""), reverse=True)

