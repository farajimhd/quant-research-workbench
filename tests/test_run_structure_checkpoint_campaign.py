from collections import deque
import hashlib
import json
from pathlib import Path
import time

import pytest

from scripts.run_structure_checkpoint_campaign import (
    _DetachedProcessView,
    aggregate_status,
    archive_previous_attempt_statuses,
    binary_candidates,
    parse_launcher_args,
    prepare_shards,
    prepare_recovery_resume,
    recovery_plan,
    request_campaign_stop,
    sha256_file,
    source_commit,
    status_is_fully_certified,
    validate_process_worker_count,
    worker_process_creationflags,
)


def test_powershell_launcher_resolves_python_from_the_active_host() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "run_structure_checkpoint_campaign.ps1"
    ).read_text(encoding="utf-8")

    assert "C:\\Users\\g835l" not in source
    assert "Resolve-PythonExecutable" in source
    assert "$env:CONDA_PREFIX" in source
    assert "if (-not $env:DOTENV_PATHS)" in source
    assert "'secrets\\.env'" in source
    assert "& $resolvedPython @launcherArguments" in source


def test_campaign_uses_historical_sip_condition_policy_without_clock_preflight() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "run_structure_checkpoint_campaign.py"
    ).read_text(encoding="utf-8")

    assert 'STRUCTURE_INPUT_POLICY = "historical-sip-condition-v1"' in source
    assert "--validate-execution-clock-only" not in source
    assert "--execution-clock-reimport" not in source


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
    assert candidates[0].name == "structure_checkpoint_campaign_v7.exe"


def test_executable_hash_is_reported_from_exact_file_bytes(tmp_path: Path) -> None:
    binary = tmp_path / "campaign.exe"
    binary.write_bytes(b"corrected-campaign")

    assert sha256_file(binary) == hashlib.sha256(b"corrected-campaign").hexdigest().upper()


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


def test_rebuild_option_is_owned_by_launcher() -> None:
    launcher, campaign = parse_launcher_args(
        ["--rebuild", "--checkpoint-set-id", "successor"]
    )

    assert launcher.rebuild is True
    assert campaign == ["--checkpoint-set-id", "successor"]


def test_explicit_source_commit_is_validated_without_git() -> None:
    revision = "9" * 40
    assert source_commit(revision) == revision
    with pytest.raises(RuntimeError, match="40-character"):
        source_commit("933e3335")


def test_source_commit_option_is_owned_by_launcher() -> None:
    revision = "9" * 40
    launcher, campaign = parse_launcher_args(
        ["--source-commit", revision, "--checkpoint-set-id", "successor"]
    )

    assert launcher.source_commit == revision
    assert campaign == ["--checkpoint-set-id", "successor"]


def test_process_worker_option_is_owned_by_launcher() -> None:
    launcher, campaign = parse_launcher_args(
        ["--process-workers", "32", "--workers", "32", "--checkpoint-set-id", "canonical-v16"]
    )

    assert launcher.process_workers == 32
    assert campaign == ["--workers", "32", "--checkpoint-set-id", "canonical-v16"]


def test_recovery_resume_is_owned_by_launcher() -> None:
    launcher, campaign = parse_launcher_args(
        ["--resume-from-runtime", r"D:\old", "--checkpoint-set-id", "successor"]
    )

    assert launcher.resume_from_runtime == r"D:\old"
    assert campaign == ["--checkpoint-set-id", "successor"]


def test_monitor_option_is_owned_by_launcher() -> None:
    launcher, campaign = parse_launcher_args(
        [
            "--monitor-existing",
            "--runtime-dir",
            r"D:\runtime",
            "--checkpoint-set-id",
            "set-v1",
        ]
    )

    assert launcher.monitor_existing is True
    assert campaign == [
        "--runtime-dir",
        r"D:\runtime",
        "--checkpoint-set-id",
        "set-v1",
    ]


def test_stop_option_publishes_validated_campaign_control(tmp_path: Path) -> None:
    (tmp_path / "campaign-manifest.json").write_text(
        json.dumps({"checkpoint_set_id": "set-v1"}), encoding="utf-8"
    )

    path = request_campaign_stop(tmp_path, "set-v1", "fast")
    request = json.loads(path.read_text(encoding="utf-8"))

    assert request["checkpoint_set_id"] == "set-v1"
    assert request["action"] == "stop_fast"
    assert request["request_id"]


def test_supervisor_and_stop_options_are_not_forwarded_to_native_worker() -> None:
    launcher, campaign = parse_launcher_args(
        [
            "--supervisor-child",
            "--foreground-supervisor",
            "--stop-existing",
            "graceful",
            "--checkpoint-set-id",
            "set-v1",
        ]
    )

    assert launcher.supervisor_child is True
    assert launcher.foreground_supervisor is True
    assert launcher.stop_existing == "graceful"
    assert campaign == ["--checkpoint-set-id", "set-v1"]


def test_stop_request_rejects_a_different_checkpoint_set(tmp_path: Path) -> None:
    (tmp_path / "campaign-manifest.json").write_text(
        json.dumps({"checkpoint_set_id": "set-v1"}), encoding="utf-8"
    )

    with pytest.raises(RuntimeError, match="does not match"):
        request_campaign_stop(tmp_path, "set-v2", "graceful")


def test_detached_process_view_uses_durable_worker_state() -> None:
    assert _DetachedProcessView(None).poll() is None
    assert _DetachedProcessView({"status": "running"}).poll() is None
    assert _DetachedProcessView({"status": "completed"}).poll() == 0
    assert _DetachedProcessView({"status": "failed"}).poll() == 1


def test_launcher_accepts_eighty_bounded_worker_processes() -> None:
    validate_process_worker_count(1)
    validate_process_worker_count(80)
    with pytest.raises(RuntimeError, match="between 1 and 80"):
        validate_process_worker_count(81)


def test_native_workers_are_windowless_on_windows_only() -> None:
    assert worker_process_creationflags("nt") == 0x08000000
    assert worker_process_creationflags("posix") == 0


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


def test_recovery_resume_binds_source_identity_and_prioritizes_strategy_tickers(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    (source / "planner").mkdir(parents=True)
    plans = [
        {"ticker": "A", "estimated_events": 1},
        {"ticker": "JUNS", "estimated_events": 2},
        {"ticker": "SUGP", "estimated_events": 3},
    ]
    universe_hash = hashlib.sha256(b"A\nJUNS\nSUGP\n").hexdigest()
    manifest = {
        "checkpoint_set_id": "legacy",
        "start_date": "2025-01-01",
        "end_date": "2026-08-31",
        "universe_hash": universe_hash,
    }
    (source / "campaign-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (source / "planner" / "campaign-plan.json").write_text(
        json.dumps(plans), encoding="utf-8"
    )
    (source / "campaign-status.json").write_text(
        json.dumps({"status": "interrupted"}), encoding="utf-8"
    )

    args, source_runtime, source_manifest = prepare_recovery_resume(
        ["--runtime-dir", str(target), "--checkpoint-set-id", "successor"], str(source)
    )

    assert source_runtime == source.resolve()
    assert source_manifest == manifest
    assert args[:4] == ["--priority-ticker", "SUGP", "--priority-ticker", "JUNS"]
    assert args[args.index("--start-date") + 1] == "2025-01-01"
    assert args[args.index("--end-date") + 1] == "2026-08-31"
    assert args[args.index("--recovery-source-checkpoint-set-id") + 1] == "legacy"
    assert [plan["ticker"] for plan in recovery_plan(source_runtime, source_manifest)] == [
        "SUGP",
        "JUNS",
        "A",
    ]


def test_recovery_resume_refuses_to_mutate_source_runtime(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "campaign-manifest.json").write_text(
        json.dumps(
            {
                "checkpoint_set_id": "legacy",
                "start_date": "2025-01-01",
                "end_date": "2026-08-31",
            }
        ),
        encoding="utf-8",
    )
    (source / "planner").mkdir()
    (source / "planner" / "campaign-plan.json").write_text("[]", encoding="utf-8")

    with pytest.raises(RuntimeError, match="new runtime directory"):
        prepare_recovery_resume(
            ["--runtime-dir", str(source), "--checkpoint-set-id", "successor"],
            str(source),
        )


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
