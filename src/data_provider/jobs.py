from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from src.data_provider.config import BuildRequest
from src.data_provider.file_lock import file_lock
from src.data_provider.manifest import delete_artifacts_for_build


JOB_DIR = "jobs"
JOB_FILE = "job.json"
EVENTS_FILE = "events.jsonl"
CANCEL_FILE = "cancel.requested"
LOG_FILE = "worker.log"


class BuildCancelled(RuntimeError):
    pass


TERMINAL_STATUSES = {"complete", "failed", "error", "cancelled", "canceled"}


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


def generated_build_name(request: BuildRequest, job_id: str) -> str:
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    return f"market_data_{request.start_date.isoformat()}_{request.end_date.isoformat()}_{timestamp}_{job_id[:6]}"


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
    payload.setdefault("build_id", path.name)
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


def terminate_process_tree(pid: int | None) -> dict[str, Any]:
    if not pid:
        return {"terminated": False, "reason": "missing_pid"}
    if os.name == "nt":
        try:
            result = subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired:
            return {"terminated": False, "reason": "taskkill_timeout"}
        output = "\n".join(part.strip() for part in [result.stdout, result.stderr] if part.strip())
        already_stopped = "not found" in output.lower() or "not running" in output.lower()
        return {
            "terminated": result.returncode == 0,
            "already_stopped": already_stopped,
            "returncode": result.returncode,
            "message": output,
        }
    try:
        os.kill(pid, 15)
        return {"terminated": True}
    except ProcessLookupError:
        return {"terminated": False, "already_stopped": True, "reason": "not_found"}
    except PermissionError as exc:
        return {"terminated": False, "reason": str(exc)}


def payload_pid(payload: dict[str, Any]) -> int | None:
    try:
        return int(payload.get("pid") or 0) or None
    except (TypeError, ValueError):
        return None


def settle_canceling_job(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    if str(payload.get("status") or "").lower() != "canceling" or not is_cancel_requested(path):
        return payload
    termination = terminate_process_tree(payload_pid(payload))
    append_event(
        path,
        {
            "event": "job_cancelled",
            "phase": "cancel",
            "status": "cancelled",
            "message": "Build job cancellation settled by status refresh.",
            "termination": termination,
        },
    )
    return update_job(
        path,
        status="cancelled",
        finished_at=payload.get("finished_at") or utc_now(),
        error=payload.get("error") or "Build job was cancelled by user.",
        cancellation=termination,
    )


def parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def build_duration_seconds(payload: dict[str, Any]) -> float | None:
    start = parse_utc(payload.get("started_at") or payload.get("created_at"))
    end = parse_utc(payload.get("finished_at") or payload.get("updated_at"))
    if not start or not end:
        return None
    return max(0.0, round((end - start).total_seconds(), 3))


def summarize_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    artifact_events = [event for event in events if event.get("event") == "artifact_complete"]
    plan = next((event.get("plan") for event in reversed(events) if event.get("event") == "plan_complete" and isinstance(event.get("plan"), list)), [])
    expected = [row for row in plan if row.get("expected_market_session")] if isinstance(plan, list) else []
    output = [row for row in expected if row.get("build_role") in {None, "output"}]
    reference = [row for row in expected if row.get("build_role") == "reference_only"]
    missing = [row for row in expected if not row.get("exists")]
    return {
        "artifact_count": len(artifact_events),
        "rows_written": sum(int(event.get("rows_out") or 0) for event in artifact_events),
        "bytes_written": sum(int(event.get("size_bytes") or 0) for event in artifact_events),
        "expected_sessions": len(expected),
        "reference_sessions": len(reference),
        "output_sessions": len(output),
        "missing_sessions": len(missing),
        "event_count": len(events),
    }


def artifact_paths_from_events(events: list[dict[str, Any]]) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()
    for event in events:
        if event.get("event") != "artifact_complete":
            continue
        path = str(event.get("path") or "")
        if not path or path in seen:
            continue
        seen.add(path)
        paths.append(path)
    return paths


def attach_job_summary(payload: dict[str, Any], events: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    if not payload:
        return payload
    event_rows = events if events is not None else read_events(job_dir(Path(payload.get("request", {}).get("processed_root", "")), str(payload.get("job_id", ""))))
    summary = {**(payload.get("summary") or {}), **summarize_events(event_rows)}
    duration = build_duration_seconds(payload)
    if duration is not None:
        summary["duration_sec"] = duration
    request = payload.get("request") or {}
    payload.setdefault("build_name", request.get("build_name") or f"market_data_{payload.get('created_at', '')}")
    payload["summary"] = summary
    return payload


def submit_build_job(
    request: BuildRequest,
    *,
    session_workers: int = 8,
    polars_threads: int = 10,
) -> dict[str, Any]:
    job_id = uuid.uuid4().hex
    request.build_id = job_id
    request.build_name = request.build_name or generated_build_name(request, job_id)
    path = job_dir(request.processed_root, job_id)
    payload = {
        "job_id": job_id,
        "build_name": request.build_name,
        "status": "queued",
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "started_at": None,
        "finished_at": None,
        "request": request_to_dict(request),
        "resources": {"session_workers": session_workers, "polars_threads": polars_threads},
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
    payload = read_job(path)
    if not payload:
        return {"status": "not_found", "job_id": job_id}
    status = str(payload.get("status") or "").lower()
    if status in TERMINAL_STATUSES:
        return attach_job_summary(payload)
    cancel_file(path).write_text(utc_now(), encoding="utf-8")
    append_event(path, {"event": "cancel_requested", "phase": "cancel", "status": "canceling"})
    termination = terminate_process_tree(payload_pid(payload))
    append_event(
        path,
        {
            "event": "job_cancelled",
            "phase": "cancel",
            "status": "cancelled",
            "message": "Build job cancellation requested by user.",
            "termination": termination,
        },
    )
    return attach_job_summary(
        update_job(
            path,
            status="cancelled",
            finished_at=utc_now(),
            error="Build job was cancelled by user.",
            cancellation=termination,
        )
    )


def delete_build_job(processed_root: Path, job_id: str, *, delete_data: bool = True) -> dict[str, Any]:
    root = processed_root.resolve()
    root_jobs = jobs_root(root).resolve()
    path = job_dir(root, job_id).resolve()
    if root_jobs != path and root_jobs not in path.parents:
        raise ValueError("Refusing to delete outside build jobs root")
    payload = read_job(path)
    if not payload:
        events = read_events(path) if path.exists() else []
        data_result = delete_artifacts_for_build(root, job_id, artifact_paths_from_events(events)) if delete_data else {
            "deleted_artifacts": 0,
            "deleted_files": 0,
            "missing_files": 0,
            "skipped_files": [],
            "skipped_superseded_files": [],
        }
        if path.exists():
            shutil.rmtree(path)
        return {"status": "deleted", "job_id": job_id, "deleted_data": delete_data, "orphaned_job": True, **data_result}
    if str(payload.get("status") or "").lower() in {"queued", "running", "canceling"}:
        raise ValueError("Stop the build before deleting it")
    events = read_events(path)
    data_result = delete_artifacts_for_build(root, job_id, artifact_paths_from_events(events)) if delete_data else {
        "deleted_artifacts": 0,
        "deleted_files": 0,
        "missing_files": 0,
        "skipped_files": [],
        "skipped_superseded_files": [],
    }
    shutil.rmtree(path)
    return {"status": "deleted", "job_id": job_id, "deleted_data": delete_data, **data_result}


def get_build_status(processed_root: Path, job_id: str) -> dict[str, Any]:
    path = job_dir(processed_root, job_id)
    payload = read_job(path)
    payload = settle_canceling_job(path, payload) if payload else payload
    events = read_events(path)
    payload = attach_job_summary(payload, events)
    payload["events"] = events
    payload["job_dir"] = str(path)
    payload["log_path"] = str(log_file(path))
    return payload


def list_build_jobs(processed_root: Path) -> list[dict[str, Any]]:
    root = jobs_root(processed_root)
    if not root.exists():
        return []
    jobs = [
        attach_job_summary(settle_canceling_job(path, read_job(path)), read_events(path))
        for path in root.iterdir()
        if path.is_dir() and job_file(path).exists()
    ]
    return sorted(jobs, key=lambda item: str(item.get("created_at") or ""), reverse=True)
