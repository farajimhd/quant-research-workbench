from __future__ import annotations

import sys
import traceback
from pathlib import Path

from src.data_provider.builder import build_market_data_parallel
from src.data_provider.config import BuildRequest
from src.data_provider.jobs import BuildCancelled, append_event, check_cancelled, read_job, update_job, utc_now


def run_job(path: Path) -> int:
    payload = read_job(path)
    request = BuildRequest.from_dict(payload["request"])
    resources = payload.get("resources") or {}
    max_workers = int(resources.get("max_workers") or 1)
    update_job(path, status="running", started_at=payload.get("started_at") or utc_now())

    def on_progress(event: dict) -> None:
        append_event(path, event)
        check_cancelled(path)

    try:
        result = build_market_data_parallel(request, job_path=path, max_workers=max_workers, progress_callback=on_progress)
        append_event(path, {"event": "job_complete", "phase": "job", "status": "complete", "processed_root": result["processed_root"]})
        update_job(path, status="complete", finished_at=utc_now(), result=result)
        return 0
    except BuildCancelled as exc:
        append_event(path, {"event": "job_cancelled", "phase": "cancel", "status": "cancelled", "message": str(exc)})
        update_job(path, status="cancelled", finished_at=utc_now(), error=str(exc))
        return 2
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
