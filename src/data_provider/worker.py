from __future__ import annotations

import sys
import traceback
from pathlib import Path

from src.data_provider.builder import build_market_data_parallel
from src.data_provider.config import BuildRequest
from src.data_provider.jobs import BuildCancelled, BuildPaused, append_event, check_stopped, read_events, read_job, summarize_events, update_job, utc_now


def resume_stage_for_event(event: dict) -> str | None:
    phase = str(event.get("phase") or "")
    if bool(event.get("stateful")) or phase == "stateful_features":
        return "stateful_features"
    return None


def run_job(path: Path) -> int:
    payload = read_job(path)
    request = BuildRequest.from_dict(payload["request"])
    resources = payload.get("resources") or {}
    session_workers = int(resources.get("session_workers") or resources.get("bar_workers") or resources.get("max_workers") or 8)
    update_job(path, status="running", started_at=payload.get("started_at") or utc_now(), finished_at=None, error=None, traceback=None)

    def on_progress(event: dict) -> None:
        append_event(path, event)
        check_stopped(path, resume_stage=resume_stage_for_event(event))

    try:
        check_stopped(path)
        result = build_market_data_parallel(
            request,
            job_path=path,
            session_workers=session_workers,
            progress_callback=on_progress,
        )
        append_event(path, {"event": "job_complete", "phase": "job", "status": "complete", "processed_root": result["processed_root"]})
        update_job(path, status="complete", finished_at=utc_now(), result=result, summary=summarize_events(read_events(path)))
        return 0
    except BuildCancelled as exc:
        append_event(path, {"event": "job_cancelled", "phase": "cancel", "status": "cancelled", "message": str(exc)})
        update_job(path, status="cancelled", finished_at=utc_now(), error=str(exc))
        return 2
    except BuildPaused as exc:
        request_payload = payload.get("request") or {}
        if exc.resume_stage:
            request_payload = {**request_payload, "resume_from_build_id": payload.get("job_id"), "resume_stage": exc.resume_stage}
        append_event(
            path,
            {
                "event": "job_paused",
                "phase": "pause",
                "status": "paused",
                "message": str(exc),
                "resume_stage": exc.resume_stage,
            },
        )
        update_job(
            path,
            status="paused",
            paused_at=utc_now(),
            finished_at=utc_now(),
            error=None,
            traceback=None,
            request=request_payload,
            summary=summarize_events(read_events(path)),
        )
        return 3
    except Exception as exc:
        append_event(
            path,
            {
                "event": "job_failed",
                "phase": "job",
                "status": "failed",
                "message": str(exc),
                "traceback": traceback.format_exc(),
            },
        )
        update_job(path, status="failed", finished_at=utc_now(), error=str(exc), traceback=traceback.format_exc())
        return 1


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python -m src.data_provider.worker <job_dir>")
    raise SystemExit(run_job(Path(sys.argv[1])))


if __name__ == "__main__":
    main()
