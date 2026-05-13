from __future__ import annotations

import sys
import traceback
from pathlib import Path

from src.backtest.cancel import BacktestCancelled
from src.backtest.config import BacktestConfig
from src.backtest.jobs import append_event, check_cancelled, read_job, update_job, utc_now
from src.backtest.runner import run_backtest


def run_job(path: Path) -> int:
    payload = read_job(path)
    config = BacktestConfig.from_dict(payload["config"])
    update_job(path, status="running", started_at=payload.get("started_at") or utc_now())

    def on_progress(*args) -> None:
        check_cancelled(path)
        if len(args) == 1 and isinstance(args[0], dict):
            payload = dict(args[0])
            update_progress_job(path, payload)
            if payload.get("event") in {"run_progress_initialized", "session_started", "session_complete"}:
                append_event(path, payload)
            return
        if len(args) == 3:
            session_date, daily_summary, run_dir = args
            event = {
                "event": "session_complete",
                "phase": "backtest",
                "status": "running",
                "session_date": str(session_date),
                "daily_summary": daily_summary,
                "run_dir": str(run_dir),
            }
            update_progress_job(path, event)
            append_event(path, event)
            return
        raise TypeError(f"Unsupported progress payload: {args!r}")

    try:
        check_cancelled(path)
        append_event(path, {"event": "job_started", "phase": "backtest", "status": "running"})
        result = run_backtest({**config.to_dict(), "created_by_app": True}, progress_callback=on_progress, cancel_check=lambda: check_cancelled(path))
        append_event(path, {"event": "job_complete", "phase": "backtest", "status": "complete", "run_dir": result["run_dir"]})
        update_job(path, status="complete", finished_at=utc_now(), result=result)
        return 0
    except BacktestCancelled as exc:
        append_event(path, {"event": "job_cancelled", "phase": "cancel", "status": "cancelled", "message": str(exc)})
        update_job(path, status="cancelled", finished_at=utc_now(), error=str(exc))
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


def update_progress_job(path: Path, payload: dict) -> None:
    update = {
        key: payload[key]
        for key in (
            "run_dir",
            "status",
            "progress_kind",
            "progress_unit",
            "processed_event_bars",
            "total_event_bars",
            "completed_sessions",
            "total_sessions",
            "latest_session",
            "current_session",
            "current_bar_time",
            "current_session_processed_bars",
            "current_session_total_bars",
        )
        if key in payload
    }
    if "session_date" in payload:
        update["latest_session"] = str(payload["session_date"])
    if "daily_summary" in payload:
        update["latest_daily_summary"] = payload["daily_summary"]
    if update:
        update_job(path, **update)


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python -m src.backtest.worker <job_dir>")
    raise SystemExit(run_job(Path(sys.argv[1])))


if __name__ == "__main__":
    main()
