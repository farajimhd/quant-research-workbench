from collections import deque
import json
from pathlib import Path
import time

import pytest

from scripts.run_structure_checkpoint_campaign import (
    aggregate_status,
    archive_previous_attempt_statuses,
    binary_candidates,
    parse_launcher_args,
    prepare_shards,
    status_is_fully_certified,
    validate_process_worker_count,
)


class _RunningProcess:
    def poll(self):
        return None


class _ExitedProcess:
    def poll(self):
        return 1


def test_only_complete_certified_status_short_circuits_a_resume() -> None:
    assert status_is_fully_certified(
        {"status": "completed", "total_units": 2, "counts": {"certified": 2}}
    )
    assert not status_is_fully_certified(
        {"status": "completed", "total_units": 2, "counts": {"certified": 0}}
    )
    assert not status_is_fully_certified(
        {"status": "failed", "total_units": 2, "counts": {"certified": 2}}
    )


def test_exited_process_cannot_leave_phantom_active_worker(tmp_path: Path) -> None:
    status_path = tmp_path / "campaign-status.json"
    status_path.write_text(
        json.dumps(
            {
                "counts": {"active": 1, "finished": 2},
                "events_processed": 10,
                "active": {"SUGP": "2026-08-21"},
                "issues": [],
            }
        ),
        encoding="utf-8",
    )
    status = aggregate_status(
        [status_path],
        [{"estimated_events": 10, "sessions": [1, 2, 3]}],
        time.monotonic(),
        deque(),
        [_ExitedProcess()],
    )

    assert status["counts"]["active"] == 0
    assert status["counts"]["queued"] == 1
    assert status["active"] == []


def test_prebuilt_runtime_binary_is_preferred_without_cargo() -> None:
    candidates = binary_candidates(None, {"TRADING_RUNTIME_ROOT": r"E:\TradingRuntime"})

    assert candidates[0] == Path(r"E:\TradingRuntime") / "bin" / candidates[0].name
    assert candidates[0].name == "structure_checkpoint_campaign_v6.exe"


def test_explicit_binary_precedes_environment_and_runtime_defaults() -> None:
    candidates = binary_candidates(
        r"C:\campaign\explicit.exe",
        {
            "QMD_STRUCTURE_CAMPAIGN_BINARY": r"C:\campaign\configured.exe",
            "TRADING_RUNTIME_ROOT": r"E:\TradingRuntime",
        },
    )

    assert str(candidates[0]).endswith("explicit.exe")
    assert str(candidates[1]).endswith("configured.exe")


def test_launcher_options_are_not_forwarded_to_native_campaign() -> None:
    launcher, campaign = parse_launcher_args(
        ["--no-build", "--start-date", "2026-01-01", "--workers", "16"]
    )

    assert launcher.no_build is True
    assert campaign == ["--start-date", "2026-01-01", "--workers", "16"]


def test_process_worker_option_is_owned_by_launcher() -> None:
    launcher, campaign = parse_launcher_args(
        ["--process-workers", "32", "--workers", "32", "--checkpoint-set-id", "canonical-v16"]
    )

    assert launcher.process_workers == 32
    assert campaign == ["--workers", "32", "--checkpoint-set-id", "canonical-v16"]


def test_launcher_accepts_eighty_bounded_worker_processes() -> None:
    validate_process_worker_count(1)
    validate_process_worker_count(80)
    with pytest.raises(RuntimeError, match="between 1 and 80"):
        validate_process_worker_count(81)


def test_shards_start_priority_tickers_and_balance_estimated_events() -> None:
    plans = [
        {"ticker": "SUGP", "estimated_events": 100},
        {"ticker": "JUNS", "estimated_events": 90},
        {"ticker": "A", "estimated_events": 80},
        {"ticker": "B", "estimated_events": 70},
        {"ticker": "C", "estimated_events": 60},
    ]

    shards = prepare_shards(plans, 2)

    assert shards[0][0]["ticker"] == "SUGP"
    assert shards[1][0]["ticker"] == "JUNS"
    assert sorted(plan["ticker"] for shard in shards for plan in shard) == ["A", "B", "C", "JUNS", "SUGP"]


def test_resume_archives_stale_status_and_starts_with_clean_truthful_queue(tmp_path: Path) -> None:
    worker_dir = tmp_path / "workers" / "worker-01"
    worker_dir.mkdir(parents=True)
    stale = {
        "counts": {"failed": 7, "blocked": 11, "finished": 18, "queued": 0},
        "events_processed": 500,
        "issues": [{"ticker": "OLD", "error": "previous attempt"}],
    }
    (tmp_path / "campaign-status.json").write_text(json.dumps(stale), encoding="utf-8")
    (worker_dir / "campaign-status.json").write_text(json.dumps(stale), encoding="utf-8")

    archive = archive_previous_attempt_statuses(tmp_path, [worker_dir])

    assert archive is not None
    assert not (tmp_path / "campaign-status.json").exists()
    assert not (worker_dir / "campaign-status.json").exists()
    assert (archive / "campaign-status.json").is_file()
    assert (archive / "workers" / "worker-01" / "campaign-status.json").is_file()

    plans = [{"ticker": "SUGP", "sessions": ["2026-08-20", "2026-08-21"], "estimated_events": 1_000}]
    status = aggregate_status(
        [worker_dir / "campaign-status.json"],
        plans,
        time.monotonic(),
        deque(),
        [_RunningProcess()],
    )

    assert status["counts"]["queued"] == 2
    assert status["counts"]["failed"] == 0
    assert status["counts"]["blocked"] == 0
    assert status["issues"] == []
