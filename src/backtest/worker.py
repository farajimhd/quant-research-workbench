from __future__ import annotations

import sys
import traceback
from pathlib import Path

from src.backtest.config import BacktestConfig
from src.backtest.jobs import append_event, read_job, update_job, utc_now
from src.backtest.runner import run_backtest


def run_job(path: Path) -> int:
    payload = read_job(path)
    config = BacktestConfig.from_dict(payload["config"])
    update_job(path, status="running", started_at=payload.get("started_at") or utc_now())

    def on_progress(session_date, daily_summary, run_dir) -> None:
        append_event(
            path,
            {
                "event": "session_complete",
                "phase": "backtest",
                "status": "running",
                "session_date": str(session_date),
                "daily_summary": daily_summary,
                "run_dir": str(run_dir),
            },
        )

    try:
        append_event(path, {"event": "job_started", "phase": "backtest", "status": "running"})
        result = run_backtest({**config.to_dict(), "created_by_app": True}, progress_callback=on_progress)
        append_event(path, {"event": "job_complete", "phase": "backtest", "status": "complete", "run_dir": result["run_dir"]})
        update_job(path, status="complete", finished_at=utc_now(), result=result)
        return 0
    except Exception as exc:
        append_event(
            path,
            {
                "event": "job_failed",
                "phase": "backtest",
                "status": "failed",
                "message": str(exc),
                "traceback": traceback.format_exc(),
            },
        )
        update_job(path, status="failed", finished_at=utc_now(), error=str(exc), traceback=traceback.format_exc())
        return 1


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python -m src.backtest.worker <job_dir>")
    raise SystemExit(run_job(Path(sys.argv[1])))


if __name__ == "__main__":
    main()

