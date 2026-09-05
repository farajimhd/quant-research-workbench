from collections import deque
import hashlib
import json
from pathlib import Path
import time

import pytest

from scripts.run_structure_checkpoint_campaign import (
    apply_priority_ranking,
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


def test_priority_report_pins_exact_order_and_rejects_ambiguous_scope(tmp_path):
    tickers = [f"T{i}" for i in range(10)]
    report = dict(schema_version=1, metric="canonical_reported_trade_dollar_volume",
                  session_date="2026-08-21", timezone="America/New_York",
                  session_start="04:00", session_end_exclusive="20:00", priority_tickers=tickers)
    path = tmp_path / "ranking.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    result = apply_priority_ranking(["--checkpoint-set-id", "fresh"], path)
    assert result[3::2] == tickers
    report["session_start"] = "09:30"
    path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(RuntimeError, match="full-session"):
        apply_priority_ranking([], path)


def test_recovery_rejects_old_algorithm_before_binding_source(tmp_path):
    source = tmp_path / "old"
    source.mkdir()
    (source / "campaign-manifest.json").write_text(json.dumps({
        "algorithm_version": 16, "checkpoint_set_id": "old", "start_date": "2025-01-01", "end_date": "2026-08-31"
    }), encoding="utf-8")
    with pytest.raises(RuntimeError, match="different structural algorithm"):
        prepare_recovery_resume(["--runtime-dir", str(tmp_path / "new"), "--checkpoint-set-id", "new"], str(source))


def test_stop_does_not_require_a_build_or_a_compatible_binary(tmp_path, monkeypatch):
    from scripts import run_structure_checkpoint_campaign as module
    monkeypatch.setattr(module, "resolve_binary", lambda *a, **k: pytest.fail("stop tried to resolve binary"))
    (tmp_path / "campaign-manifest.json").write_text(json.dumps({"checkpoint_set_id": "old"}), encoding="utf-8")
    assert module.main(["--stop-existing", "fast", "--runtime-dir", str(tmp_path), "--checkpoint-set-id", "old"]) == 0


def test_default_launch_builds_current_source_instead_of_selecting_stale_binary(tmp_path, monkeypatch):
    from scripts import run_structure_checkpoint_campaign as campaign
    old = tmp_path / "old.exe"
    old.write_bytes(b"old")
    built = tmp_path / "target" / "release" / campaign.BUILD_BINARY_NAME
    built.parent.mkdir(parents=True)
    calls = []
    monkeypatch.setattr(campaign, "binary_candidates", lambda *a: (old,))
    monkeypatch.setattr(campaign, "resolve_cargo", lambda *a: "cargo")
    def build(args, **kwargs):
        calls.append(args)
        built.write_bytes(b"fresh")
    monkeypatch.setattr(campaign.subprocess, "run", build)
    assert campaign.resolve_binary(None, True, {"CARGO_TARGET_DIR": str(tmp_path / "target")}) == built.resolve()
    assert len(calls) == 1 and "structural-prominence-v18" in calls[0]
    assert campaign.resolve_binary(None, False, {}) == old.resolve()


def test_supervisor_stops_waiting_workers_after_unrecoverable_exit(tmp_path, monkeypatch):
    from scripts import run_structure_checkpoint_campaign as campaign
    from types import SimpleNamespace
    source = tmp_path / "source"
    (source / "planner").mkdir(parents=True)
    plans = [{"ticker": ticker, "sessions": ["2025-01-02"], "estimated_events": 5} for ticker in ["A", "B"]]
    (source / "planner" / "campaign-plan.json").write_text(json.dumps(plans))
    manifest = {"checkpoint_set_id": "source", "universe_hash": hashlib.sha256(b"A\nB\n").hexdigest()}
    (source / "campaign-manifest.json").write_text(json.dumps(manifest))
    target = tmp_path / "target"
    registrations, workers = [], []
    def run(args, **kwargs):
        registrations.append(args[args.index("--register-set-state") + 1])
        return SimpleNamespace(returncode=0)
    class Worker:
        def __init__(self, args, **kwargs):
            self.returncode = 1 if not workers else None
            self.control = Path(args[args.index("--campaign-control-path") + 1])
            workers.append(self)
            kwargs["stdout"].write("Error: checkpoint identity mismatch\n" if self.returncode else "Waiting for priority certification\n")
            kwargs["stdout"].flush()
        def poll(self):
            if self.returncode is None and self.control.exists(): self.returncode = 0
            return self.returncode
        def wait(self): return self.poll()
    monkeypatch.setattr(campaign.subprocess, "run", run)
    monkeypatch.setattr(campaign.subprocess, "Popen", Worker)
    result = campaign.run_process_campaign(Path("binary"), "abc", ["--checkpoint-set-id", "target", "--runtime-dir", str(target), "--start-date", "2025-01-01", "--end-date", "2025-01-02"], 2, {}, source, manifest, "a" * 40)
    assert result == 1 and registrations == ["building", "failed"]
    assert len(workers) == 2 and all(worker.poll() is not None for worker in workers)
    assert json.loads((target / "campaign-status.json").read_text())["status"] == "failed"


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
    assert candidates[0].name == "structure_checkpoint_campaign_v18.exe"


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


def test_launcher_accepts_ninety_six_bounded_worker_processes() -> None:
    validate_process_worker_count(1)
    validate_process_worker_count(96)
    with pytest.raises(RuntimeError, match="between 1 and 96"):
        validate_process_worker_count(97)


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
        "algorithm_version": 18,
        "priority_tickers": ["SUGP", "JUNS"],
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


def test_startup_failure_is_not_hidden_as_queued_progress(tmp_path):
    from scripts.run_structure_checkpoint_campaign import retryable_worker_exit
    path = tmp_path / "campaign-status.json"
    (tmp_path / "worker.log").write_text("Error: DEADLOCK_AVOIDED (120000 ms)")
    status = aggregate_status([path], [{"sessions": [1, 2], "estimated_events": 100}], time.monotonic(), deque(), [_ExitedProcess()])
    assert status["status"] == "failed"
    assert status["worker_startup_failures"] == status["worker_processes_failed"] == 1
    assert status["counts"]["queued"] == 2
    assert status["eta_seconds"] is None
    assert "DEADLOCK_AVOIDED" in status["issues"][0]["error"]
    assert retryable_worker_exit("Error: DEADLOCK_AVOIDED")
    assert not retryable_worker_exit("connection reset; serialized payload hash drifted")


def test_zero_exit_without_full_coverage_fails_closed(tmp_path):
    class ExitedZero:
        def poll(self): return 0
    path = tmp_path / "campaign-status.json"
    path.write_text(json.dumps({"status": "completed", "total_units": 2, "counts": {"certified": 1, "finished": 1}}))
    status = aggregate_status([path], [{"sessions": [1, 2]}], time.monotonic(), deque(), [ExitedZero()])
    assert status["status"] == "failed"
    assert not status_is_fully_certified(status)


def test_supervisor_restarts_transient_failure_and_seals_only_certified_work(tmp_path, monkeypatch):
    import scripts.run_structure_checkpoint_campaign as campaign
    from types import SimpleNamespace
    source = tmp_path / "source"
    (source / "planner").mkdir(parents=True)
    plans = [{"ticker": "TEST", "sessions": ["2025-01-02"], "estimated_events": 5}]
    manifest = {"checkpoint_set_id": "source", "start_date": "2025-01-01", "end_date": "2025-01-02", "universe_hash": hashlib.sha256(b"TEST\n").hexdigest()}
    (source / "campaign-manifest.json").write_text(json.dumps(manifest))
    (source / "planner" / "campaign-plan.json").write_text(json.dumps(plans))
    calls = []
    registrations = []
    def run(args, **kwargs):
        registrations.append(args[args.index("--register-set-state") + 1])
        return SimpleNamespace(returncode=0)
    class Worker:
        def __init__(self, args, **kwargs):
            calls.append(args)
            directory = Path(args[args.index("--runtime-dir") + 1])
            self.returncode = 1 if len(calls) == 1 else 0
            if self.returncode:
                kwargs["stdout"].write("Error: DEADLOCK_AVOIDED\n")
                kwargs["stdout"].flush()
            else:
                (directory / "campaign-status.json").write_text(json.dumps({"status": "completed", "total_units": 1, "counts": {"certified": 1, "finished": 1, "skipped": 1}, "events_processed": 5}))
        def poll(self): return self.returncode
        def wait(self): return self.returncode
    monkeypatch.setattr(campaign.subprocess, "run", run)
    monkeypatch.setattr(campaign.subprocess, "Popen", Worker)
    target = tmp_path / "target"
    result = campaign.run_process_campaign(Path("binary"), "abc", ["--checkpoint-set-id", "target", "--runtime-dir", str(target), "--start-date", "2025-01-01", "--end-date", "2025-01-02"], 1, {}, source, manifest, "a" * 40)
    assert result == 0
    assert len(calls) == 2 and calls[0] == calls[1]
    assert registrations == ["building", "sealed"]
    final = json.loads((target / "campaign-status.json").read_text())
    assert final["worker_restarts"] == 1 and final["counts"]["certified"] == 1
    assert list((target / "workers" / "worker-01").glob("attempt-*/worker.log"))


def test_recovery_stages_are_visible_and_coverage_does_not_claim_an_eta(tmp_path):
    path = tmp_path / "status.json"
    path.write_text(json.dumps({"status": "running", "counts": {"skipped": 16, "certified": 16, "finished": 16},
        "events_processed": 1000000, "events_advanced": 0, "active": {},
        "stages": {"ABC": "persist recovery"}, "issues": []}))
    result = aggregate_status([path], [{"estimated_events": 2000000, "sessions": list(range(32))}],
        time.monotonic() - 60, deque([(time.monotonic() - 30, 0)]), [_RunningProcess()])
    assert result["stages"] == {"persist recovery": 1}
    assert result["worker_details"] == ["W01 ABC: persist recovery"]
    assert result["eta_seconds"] is None
    assert result["counts"]["certified"] == 16
    assert result["replayed_events"] == 0
