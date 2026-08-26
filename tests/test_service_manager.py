from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "scripts" / "service_manager.py"
SPEC = importlib.util.spec_from_file_location("service_manager_under_test", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
service_manager = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = service_manager
SPEC.loader.exec_module(service_manager)


def _options(tmp_path: Path) -> object:
    bar_manifest = tmp_path / "bar.json"
    text_manifest = tmp_path / "text.json"
    bar_manifest.write_text("[]", encoding="utf-8")
    text_manifest.write_text("{}", encoding="utf-8")
    return service_manager.ManagerOptions(
        runtime_root=tmp_path / "runtime",
        python=Path(sys.executable),
        terminal_window="test-services",
        terminal_target="named",
        qmd_live_host_role="Laptop",
        ibkr_account="paper",
        bar_gpt_release_manifest=bar_manifest,
        text_intelligence_release_manifest=text_manifest,
        timeout_seconds=2,
    )


def _ownership_record(
    manager: object,
    service_id: str,
    *,
    pid: int,
    fingerprint: str,
) -> dict[str, object]:
    service = manager.services[service_id]
    registry = manager._default_registry_path(service_id)
    return {
        "schema_version": 1,
        "service_role": service.role,
        "host_pid": pid,
        "desired_fingerprint": fingerprint,
        "repository_root": str(service_manager.REPO_ROOT),
        "registry_path": str(registry),
    }


def test_catalog_defines_operator_profiles_and_dynamic_dependencies() -> None:
    services, profiles = service_manager._load_catalog()

    assert set(profiles) >= {
        "historical",
        "historical-core",
        "live",
        "live-core",
        "gateways",
        "intelligence",
        "middleware",
        "market-data",
    }
    assert profiles["middleware"] == profiles["intelligence"]
    assert services["reference-gateway"].dependencies == ("ibkr-supervisor",)
    assert services["news-hypothesis"].dependencies == ("model-gateway",)
    assert services["text-intelligence"].dependencies == ()
    order = service_manager._topological_order(services, set(profiles["live"]))
    assert order.index("qmd-history") < order.index("backend") < order.index("frontend")
    assert order.index("model-gateway") < order.index("news-hypothesis")
    assert order.index("ibkr-supervisor") < order.index("reference-gateway")
    assert services["qmd-live"].readiness["kind"] == "qmd_live"
    assert services["ibkr-supervisor"].readiness["kind"] == "ibkr"


def test_fingerprint_detects_uncommitted_source_and_safe_environment_changes(
    tmp_path: Path, monkeypatch,
) -> None:
    source_root = tmp_path / "repo"
    source = source_root / "service" / "main.py"
    source.parent.mkdir(parents=True)
    source.write_text("VERSION = 1\n", encoding="utf-8")
    monkeypatch.setattr(service_manager, "REPO_ROOT", source_root)
    service = service_manager.Service(
        service_id="sample",
        title="Sample",
        role="sample",
        port=9999,
        health_url="http://127.0.0.1:9999/health",
        launch_kind="powershell",
        launcher="run.ps1",
        arguments=(),
        environment={},
        dependencies=(),
        watch=("service/**",),
        artifact_paths=(),
        fingerprint_env=("SAMPLE_MODE",),
        start_priority=50,
        graceful_timeout_seconds=10,
    )
    options = _options(tmp_path)

    first = service_manager._fingerprint(service, options)
    source.write_text("VERSION = 2\n", encoding="utf-8")
    second = service_manager._fingerprint(service, options)
    monkeypatch.setenv("SAMPLE_MODE", "development")
    third = service_manager._fingerprint(service, options)

    assert first != second
    assert second != third


def test_fingerprint_ignores_generated_source_cache_files(tmp_path: Path, monkeypatch) -> None:
    source_root = tmp_path / "repo"
    source = source_root / "service" / "main.py"
    source.parent.mkdir(parents=True)
    source.write_text("VERSION = 1\n", encoding="utf-8")
    monkeypatch.setattr(service_manager, "REPO_ROOT", source_root)
    service = service_manager.Service(
        service_id="sample",
        title="Sample",
        role="sample",
        port=9999,
        health_url="http://127.0.0.1:9999/health",
        launch_kind="powershell",
        launcher="run.ps1",
        arguments=(),
        environment={},
        dependencies=(),
        watch=("service/**",),
        artifact_paths=(),
        fingerprint_env=(),
        start_priority=50,
        graceful_timeout_seconds=10,
    )
    options = _options(tmp_path)
    first = service_manager._fingerprint(service, options)

    cache_file = source.parent / "__pycache__" / "main.cpython-312.pyc"
    cache_file.parent.mkdir()
    cache_file.write_bytes(b"generated bytecode")
    pytest_cache = source.parent / ".pytest_cache" / "v" / "cache" / "nodeids"
    pytest_cache.parent.mkdir(parents=True)
    pytest_cache.write_text("generated test cache", encoding="utf-8")

    assert service_manager._fingerprint(service, options) == first


def test_fingerprint_reports_changed_component(tmp_path: Path, monkeypatch) -> None:
    services, profiles = service_manager._load_catalog()
    manager = service_manager.ServiceManager(services, profiles, _options(tmp_path))
    registry = manager._default_registry_path("model-gateway")
    registry.parent.mkdir(parents=True, exist_ok=True)
    components = manager.desired_fingerprint_components("model-gateway")
    running_components = {**components, "source": "old-source"}
    registry.write_text(json.dumps({
        **_ownership_record(manager, "model-gateway", pid=123, fingerprint="old"),
        "fingerprint_components": running_components,
    }), encoding="utf-8")
    monkeypatch.setattr(service_manager, "_pid_exists", lambda pid: pid == 123)
    monkeypatch.setattr(service_manager, "_port_open", lambda port, timeout=0.2: True)
    monkeypatch.setattr(service_manager, "_http_probe", lambda url, timeout=2.0: (
        True, "HTTP 200", {"status": "ready"},
    ))

    status = manager.status("model-gateway")

    assert status.state == "stale"
    assert status.drift_components == ("source",)
    assert status.reason == "changed: source"


def test_dev_is_running_stale_while_stale_also_includes_stopped(
    tmp_path: Path, monkeypatch,
) -> None:
    services, profiles = service_manager._load_catalog()
    manager = service_manager.ServiceManager(services, profiles, _options(tmp_path))
    backend_registry = manager._default_registry_path("backend")
    backend_registry.parent.mkdir(parents=True, exist_ok=True)
    backend_registry.write_text(
        json.dumps(_ownership_record(
            manager,
            "backend",
            pid=123,
            fingerprint="old-backend",
        )),
        encoding="utf-8",
    )
    frontend_state = manager._state_path("frontend")
    frontend_state.parent.mkdir(parents=True, exist_ok=True)
    frontend_state.write_text(
        json.dumps({
            "service_id": "frontend",
            "registry_path": str(manager._default_registry_path("frontend")),
            "last_fingerprint": "old-frontend",
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(service_manager, "_pid_exists", lambda pid: pid == 123)
    monkeypatch.setattr(service_manager, "_port_open", lambda port, timeout=0.2: port == 8000)
    monkeypatch.setattr(
        service_manager,
        "_http_probe",
        lambda url, timeout=2.0: (True, "HTTP 200", {"status": "ok"}),
    )

    assert manager.resolve_target("dev", within="app") == {"backend"}
    assert manager.resolve_target("stale", within="app") == {"backend", "frontend"}


def test_start_plan_expands_dependencies_without_mutating_runtime(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    services, profiles = service_manager._load_catalog()
    manager = service_manager.ServiceManager(services, profiles, _options(tmp_path))
    monkeypatch.setattr(service_manager, "_port_open", lambda port, timeout=0.2: False)

    started = manager.start({"frontend"}, dry_run=True)

    assert started == ["qmd-history", "backend", "frontend"]
    assert "[start]    qmd-history (plan)" in capsys.readouterr().out
    assert not manager.runtime_root.exists()


def test_plan_command_does_not_acquire_the_mutation_lock(tmp_path: Path, monkeypatch) -> None:
    lock = mock.Mock(side_effect=AssertionError("plan acquired mutation lock"))
    start = mock.Mock(return_value=[])
    monkeypatch.setattr(service_manager.ServiceManager, "operation_lock", lock)
    monkeypatch.setattr(service_manager.ServiceManager, "start", start)

    exit_code = service_manager.main([
        "start",
        "model-gateway",
        "--plan",
        "--runtime-root",
        str(tmp_path / "runtime"),
        "--python-exe",
        sys.executable,
    ])

    assert exit_code == 0
    lock.assert_not_called()
    start.assert_called_once_with({"model-gateway"}, dry_run=True)


def test_powershell_launcher_parameters_are_not_quoted_as_positional_values(tmp_path: Path) -> None:
    services, profiles = service_manager._load_catalog()
    manager = service_manager.ServiceManager(services, profiles, _options(tmp_path))

    command = manager._powershell_command(services["model-gateway"])

    assert " -PythonExe '" in command
    assert "'-PythonExe'" not in command


def test_status_marks_manager_owned_active_errors_degraded(tmp_path: Path, monkeypatch) -> None:
    services, profiles = service_manager._load_catalog()
    manager = service_manager.ServiceManager(services, profiles, _options(tmp_path))
    registry = manager._default_registry_path("model-gateway")
    registry.parent.mkdir(parents=True, exist_ok=True)
    fingerprint = manager.desired_fingerprint("model-gateway")
    registry.write_text(
        json.dumps(_ownership_record(
            manager,
            "model-gateway",
            pid=456,
            fingerprint=fingerprint,
        )),
        encoding="utf-8",
    )
    monkeypatch.setattr(service_manager, "_pid_exists", lambda pid: pid == 456)
    monkeypatch.setattr(service_manager, "_port_open", lambda port, timeout=0.2: True)
    monkeypatch.setattr(
        service_manager,
        "_http_probe",
        lambda url, timeout=2.0: (
            True,
            "HTTP 200",
            {"status": "ok", "error_state": {"active_error_count": 1}},
        ),
    )

    status = manager.status("model-gateway")

    assert status.state == "degraded"
    assert manager.resolve_target("unhealthy", within="intelligence") == {"model-gateway"}


def test_bar_gpt_warming_is_progressing_not_unhealthy(tmp_path: Path, monkeypatch, capsys) -> None:
    services, profiles = service_manager._load_catalog()
    manager = service_manager.ServiceManager(services, profiles, _options(tmp_path))
    registry = manager._default_registry_path("bar-gpt")
    registry.parent.mkdir(parents=True, exist_ok=True)
    fingerprint = manager.desired_fingerprint("bar-gpt")
    registry.write_text(
        json.dumps(_ownership_record(manager, "bar-gpt", pid=456, fingerprint=fingerprint)),
        encoding="utf-8",
    )
    monkeypatch.setattr(service_manager, "_pid_exists", lambda pid: pid == 456)
    monkeypatch.setattr(service_manager, "_port_open", lambda port, timeout=0.2: True)
    monkeypatch.setattr(
        service_manager,
        "_http_probe",
        lambda url, timeout=2.0: (
            True,
            "HTTP 200",
            {"status": "warming", "error_state": {"active_error_count": 0}},
        ),
    )

    status = manager.status("bar-gpt")

    assert status.state == "warming"
    assert status.ready is True
    assert manager.resolve_target("unhealthy", within="intelligence") == set()
    assert manager.print_status({"bar-gpt"}) is True
    assert "warming" in capsys.readouterr().out


def test_bar_gpt_warming_with_active_error_is_degraded(tmp_path: Path, monkeypatch) -> None:
    services, profiles = service_manager._load_catalog()
    manager = service_manager.ServiceManager(services, profiles, _options(tmp_path))
    registry = manager._default_registry_path("bar-gpt")
    registry.parent.mkdir(parents=True, exist_ok=True)
    fingerprint = manager.desired_fingerprint("bar-gpt")
    registry.write_text(
        json.dumps(_ownership_record(manager, "bar-gpt", pid=456, fingerprint=fingerprint)),
        encoding="utf-8",
    )
    monkeypatch.setattr(service_manager, "_pid_exists", lambda pid: pid == 456)
    monkeypatch.setattr(service_manager, "_port_open", lambda port, timeout=0.2: True)
    monkeypatch.setattr(
        service_manager,
        "_http_probe",
        lambda url, timeout=2.0: (
            True,
            "HTTP 200",
            {"status": "warming", "error_state": {"active_error_count": 1}},
        ),
    )

    status = manager.status("bar-gpt")

    assert status.state == "degraded"
    assert manager.resolve_target("unhealthy", within="intelligence") == {"bar-gpt"}


def test_qmd_semantic_readiness_rejects_required_queue_saturation(
    tmp_path: Path, monkeypatch,
) -> None:
    services, profiles = service_manager._load_catalog()
    manager = service_manager.ServiceManager(services, profiles, _options(tmp_path))
    registry = manager._default_registry_path("qmd-live")
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text(json.dumps(_ownership_record(
        manager, "qmd-live", pid=456, fingerprint=manager.desired_fingerprint("qmd-live"),
    )), encoding="utf-8")
    health = {
        "status": "running", "running": True,
        "market_calendar": {"active_collection_window": True, "market_closed": False},
        "operational": {"lanes": [{
            "key": "compact_events", "required": True, "state": "healthy",
            "pending_rows": 96, "max_pending_rows": 100,
        }]},
    }
    snapshot = {
        "status": "running",
        "error_state": {"active": False, "message": ""},
        "runtime": {"last_event_lag_ms": 1000},
    }
    monkeypatch.setattr(service_manager, "_pid_exists", lambda pid: pid == 456)
    monkeypatch.setattr(service_manager, "_port_open", lambda port, timeout=0.2: True)
    monkeypatch.setattr(service_manager, "_http_probe", lambda url, timeout=2.0, **kwargs: (
        (True, "HTTP 200", snapshot) if url.endswith("/snapshot/status")
        else (True, "HTTP 200", health)
    ))

    status = manager.status("qmd-live")

    assert status.state == "degraded"
    assert status.ready is False


def test_qmd_semantic_readiness_uses_declared_capacity_not_historical_peak(
    tmp_path: Path, monkeypatch,
) -> None:
    services, profiles = service_manager._load_catalog()
    manager = service_manager.ServiceManager(services, profiles, _options(tmp_path))
    registry = manager._default_registry_path("qmd-live")
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text(json.dumps(_ownership_record(
        manager, "qmd-live", pid=459, fingerprint=manager.desired_fingerprint("qmd-live"),
    )), encoding="utf-8")
    health = {
        "status": "running", "running": True,
        "market_calendar": {"active_collection_window": True, "market_closed": False},
        "operational": {"lanes": [{
            "key": "canonical_events", "required": True, "state": "healthy",
            "pending_rows": 90, "capacity_rows": 1000, "max_pending_rows": 90,
        }]},
    }
    snapshot = {
        "status": "running", "error_state": {"active": False},
        "runtime": {"last_event_lag_ms": 1000},
    }
    monkeypatch.setattr(service_manager, "_pid_exists", lambda pid: pid == 459)
    monkeypatch.setattr(service_manager, "_port_open", lambda port, timeout=0.2: True)
    monkeypatch.setattr(service_manager, "_http_probe", lambda url, timeout=2.0, **kwargs: (
        (True, "HTTP 200", snapshot) if url.endswith("/snapshot/status")
        else (True, "HTTP 200", health)
    ))

    status = manager.status("qmd-live")

    assert status.state == "ready"
    assert status.ready is True


def test_qmd_semantic_readiness_accepts_healthy_fresh_required_lanes(
    tmp_path: Path, monkeypatch,
) -> None:
    services, profiles = service_manager._load_catalog()
    manager = service_manager.ServiceManager(services, profiles, _options(tmp_path))
    registry = manager._default_registry_path("qmd-live")
    registry.parent.mkdir(parents=True, exist_ok=True)
    fingerprint = manager.desired_fingerprint("qmd-live")
    registry.write_text(json.dumps(_ownership_record(
        manager, "qmd-live", pid=457, fingerprint=fingerprint,
    )), encoding="utf-8")
    health = {
        "status": "running", "running": True,
        "market_calendar": {"active_collection_window": True, "market_closed": False},
        "operational": {"lanes": [{
            "key": "compact_events", "required": True, "state": "healthy",
            "pending_rows": 10, "max_pending_rows": 100,
        }]},
    }
    snapshot = {
        "status": "running", "error_state": {"active": False},
        "runtime": {"last_event_lag_ms": 1000},
    }
    monkeypatch.setattr(service_manager, "_pid_exists", lambda pid: pid == 457)
    monkeypatch.setattr(service_manager, "_port_open", lambda port, timeout=0.2: True)
    monkeypatch.setattr(service_manager, "_http_probe", lambda url, timeout=2.0, **kwargs: (
        (True, "HTTP 200", snapshot) if url.endswith("/snapshot/status")
        else (True, "HTTP 200", health)
    ))

    status = manager.status("qmd-live")

    assert status.state == "ready"
    assert status.ready is True


def test_qmd_semantic_readiness_accepts_bounded_closed_market_catch_up(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    services, profiles = service_manager._load_catalog()
    manager = service_manager.ServiceManager(services, profiles, _options(tmp_path))
    registry = manager._default_registry_path("qmd-live")
    registry.parent.mkdir(parents=True, exist_ok=True)
    fingerprint = manager.desired_fingerprint("qmd-live")
    registry.write_text(json.dumps(_ownership_record(
        manager, "qmd-live", pid=458, fingerprint=fingerprint,
    )), encoding="utf-8")
    health = {
        "status": "catching_up", "running": True,
        "market_calendar": {"active_collection_window": False, "market_closed": True},
        "operational": {"lanes": [
            {
                "key": "canonical_events", "required": True, "state": "starting",
                "pending_rows": 0, "max_pending_rows": 0, "failures": 0,
            },
            {
                "key": "compact_events", "required": True, "state": "healthy",
                "pending_rows": 10, "max_pending_rows": 100,
            },
        ]},
    }
    snapshot = {
        "status": "catching_up", "error_state": {"active": False},
        "runtime": {"last_event_lag_ms": 20_000_000},
    }
    monkeypatch.setattr(service_manager, "_pid_exists", lambda pid: pid == 458)
    monkeypatch.setattr(service_manager, "_port_open", lambda port, timeout=0.2: True)
    monkeypatch.setattr(service_manager, "_http_probe", lambda url, timeout=2.0, **kwargs: (
        (True, "HTTP 200", snapshot) if url.endswith("/snapshot/status")
        else (True, "HTTP 200", health)
    ))

    status = manager.status("qmd-live")

    assert status.state == "catching_up"
    assert status.ready is True
    assert manager.resolve_target("unhealthy", within="market-data") == set()
    assert manager.print_status({"qmd-live"}) is True
    assert "catching_up" in capsys.readouterr().out

    health["operational"]["lanes"][0]["failures"] = 1
    failed = manager.status("qmd-live")
    assert failed.state == "degraded"
    assert failed.ready is False


def test_dead_registry_reconciliation_archives_only_dead_owned_record(
    tmp_path: Path, monkeypatch,
) -> None:
    services, profiles = service_manager._load_catalog()
    manager = service_manager.ServiceManager(services, profiles, _options(tmp_path))
    registry = manager._default_registry_path("model-gateway")
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text(json.dumps(_ownership_record(
        manager, "model-gateway", pid=999, fingerprint="old",
    )), encoding="utf-8")
    monkeypatch.setattr(service_manager, "_pid_exists", lambda pid: False)

    archived = manager.reconcile_dead_registries(["model-gateway"])

    assert not registry.exists()
    assert len(archived) == 1
    assert archived[0].is_file()


def test_restart_plan_shows_stop_and_start_for_running_target(tmp_path: Path, monkeypatch, capsys) -> None:
    services, profiles = service_manager._load_catalog()
    manager = service_manager.ServiceManager(services, profiles, _options(tmp_path))
    registry = manager._default_registry_path("model-gateway")
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text(
        json.dumps(_ownership_record(
            manager,
            "model-gateway",
            pid=789,
            fingerprint=manager.desired_fingerprint("model-gateway"),
        )),
        encoding="utf-8",
    )
    monkeypatch.setattr(service_manager, "_pid_exists", lambda pid: pid == 789)
    monkeypatch.setattr(service_manager, "_port_open", lambda port, timeout=0.2: True)
    monkeypatch.setattr(
        service_manager,
        "_http_probe",
        lambda url, timeout=2.0: (True, "HTTP 200", {"status": "ok"}),
    )

    manager.restart({"model-gateway"}, dry_run=True)

    output = capsys.readouterr().out
    assert "[stop]     model-gateway (plan)" in output
    assert "[start]    model-gateway (plan)" in output


def test_restart_coordinates_running_dependents_before_dependency(tmp_path: Path, monkeypatch, capsys) -> None:
    services, profiles = service_manager._load_catalog()
    manager = service_manager.ServiceManager(services, profiles, _options(tmp_path))
    owned = {"backend", "frontend"}
    monkeypatch.setattr(manager, "statuses", lambda selected: {
        service_id: service_manager.ServiceStatus(
            service_id=service_id,
            title=services[service_id].title,
            state="running" if service_id in owned else "stopped",
            reason="test",
            port=services[service_id].port,
            owned=service_id in owned,
            listening=service_id in owned,
            ready=service_id in owned,
            stale=False,
            desired_fingerprint="",
            running_fingerprint="",
            registry_path="",
        )
        for service_id in selected
    })

    manager.restart({"backend"}, dry_run=True)

    output = capsys.readouterr().out
    assert "[coordinate] restarting active dependents: frontend" in output
    assert output.index("[stop]     frontend (plan)") < output.index("[stop]     backend (plan)")
    assert output.index("[start]    backend (plan)") < output.index("[start]    frontend (plan)")


def test_managed_tab_launch_uses_runtime_files_not_encoded_command_line(tmp_path: Path, monkeypatch) -> None:
    services, profiles = service_manager._load_catalog()
    manager = service_manager.ServiceManager(services, profiles, _options(tmp_path))
    captured: list[str] = []

    def run(arguments, **_kwargs):
        captured.extend(str(value) for value in arguments)
        registry = Path(captured[captured.index("-RegistryPath") + 1])
        registry.parent.mkdir(parents=True, exist_ok=True)
        registry.write_text("{}", encoding="utf-8")
        return mock.Mock(returncode=0)

    monkeypatch.setattr(service_manager.subprocess, "run", run)
    monkeypatch.setattr(service_manager, "_windows_terminal", lambda: "wt.exe")
    monkeypatch.setattr(service_manager, "_powershell", lambda: "powershell.exe")
    manager._open_tab(
        services["model-gateway"],
        manager.desired_fingerprint("model-gateway"),
        dry_run=False,
    )

    assert "-CommandPath" in captured
    assert "-LaunchMetadataPath" in captured
    assert "-EncodedCommand" not in captured
    assert "-LaunchMetadataBase64" not in captured
    command_path = Path(captured[captured.index("-CommandPath") + 1])
    metadata_path = Path(captured[captured.index("-LaunchMetadataPath") + 1])
    assert command_path.is_file()
    assert metadata_path.is_file()
    assert manager.runtime_root.resolve() in command_path.resolve().parents
    assert "run_model_gateway.ps1" in command_path.read_text(encoding="utf-8")
    assert json.loads(metadata_path.read_text(encoding="utf-8"))["launch_inputs"]["service_id"] == "model-gateway"


def test_unified_wrapper_and_owned_stop_extension_are_present() -> None:
    wrapper = (REPO_ROOT / "scripts" / "services.ps1").read_text(encoding="utf-8").lower()
    tab_host = (REPO_ROOT / "scripts" / "run_windows_terminal_service_tab.ps1").read_text(encoding="utf-8").lower()
    stop = (REPO_ROOT / "scripts" / "stop_workspace_services.ps1").read_text(encoding="utf-8").lower()

    assert "service_manager.py" in wrapper
    assert "desiredfingerprint" in tab_host
    assert "desired_fingerprint" in tab_host
    assert "onlyservicerole" in stop
    assert "unknown managed service role" in stop
    assert "foreign processes and ports were left untouched" in stop
    assert "stdout.log" in tab_host
    assert "stderr.log" in tab_host
    assert "exit.json" in tab_host
    assert "commandpath" in tab_host
    assert "launchmetadatapath" in tab_host
    assert "-file" in tab_host


def test_windows_powershell_bom_ownership_record_is_readable(tmp_path: Path) -> None:
    path = tmp_path / "owned.json"
    path.write_text('{"service_role":"model_gateway","host_pid":42}', encoding="utf-8-sig")

    assert service_manager._read_json(path) == {
        "service_role": "model_gateway",
        "host_pid": 42,
    }


def test_compact_status_output_stays_within_terminal_width(tmp_path: Path, monkeypatch, capsys) -> None:
    services, profiles = service_manager._load_catalog()
    manager = service_manager.ServiceManager(services, profiles, _options(tmp_path))
    row = service_manager.ServiceStatus(
        service_id="text-intelligence",
        title="Text Intelligence",
        state="degraded",
        reason="A deliberately long dependency failure explanation that must be truncated safely.",
        port=8804,
        owned=True,
        listening=True,
        ready=True,
        stale=False,
        desired_fingerprint="a" * 64,
        running_fingerprint="a" * 64,
        registry_path="owned.json",
    )
    monkeypatch.setattr(manager, "statuses", lambda selected=None: {row.service_id: row})
    monkeypatch.setattr(service_manager.shutil, "get_terminal_size", lambda fallback: __import__("os").terminal_size((80, 24)))

    manager.print_status({row.service_id})

    assert max(len(line) for line in capsys.readouterr().out.splitlines()) <= 80


def test_redirected_status_preserves_ready_degraded_and_stopped_states(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    services, profiles = service_manager._load_catalog()
    manager = service_manager.ServiceManager(services, profiles, _options(tmp_path))
    common = {
        "port": 1, "owned": True, "listening": True, "ready": True,
        "stale": False, "desired_fingerprint": "a" * 64,
        "running_fingerprint": "a" * 64, "registry_path": "owned.json",
    }
    rows = {
        "model-gateway": service_manager.ServiceStatus(
            service_id="model-gateway", title="Model", state="ready", reason="semantic status=ready",
            **common,
        ),
        "text-intelligence": service_manager.ServiceStatus(
            service_id="text-intelligence", title="Text", state="degraded", reason="required queue saturated",
            **common,
        ),
        "bar-gpt": service_manager.ServiceStatus(
            service_id="bar-gpt", title="BarGPT", state="stopped", reason="port closed",
            **{**common, "owned": False, "listening": False, "ready": False, "running_fingerprint": ""},
        ),
    }
    monkeypatch.setattr(manager, "statuses", lambda selected=None: rows)

    assert manager.print_status(set(rows)) is False
    output = capsys.readouterr().out
    assert "model-gateway" in output and "ready" in output
    assert "text-intelligence" in output and "degraded" in output
    assert "bar-gpt" in output and "stopped" in output


def test_start_preserves_a_running_stale_service(tmp_path: Path, monkeypatch, capsys) -> None:
    services, profiles = service_manager._load_catalog()
    manager = service_manager.ServiceManager(services, profiles, _options(tmp_path))
    stale = service_manager.ServiceStatus(
        service_id="model-gateway", title="Model Gateway", state="stale",
        reason="changed", port=8802, owned=True, listening=True, ready=True,
        stale=True, desired_fingerprint="b" * 64, running_fingerprint="a" * 64,
        registry_path="owned.json",
    )
    monkeypatch.setattr(manager, "status", lambda service_id: stale)
    open_tab = mock.Mock()
    monkeypatch.setattr(manager, "_open_tab", open_tab)

    manager.start({"model-gateway"})

    open_tab.assert_not_called()
    assert "stale, use 'restart dev'" in capsys.readouterr().out


def test_start_refuses_to_adopt_foreign_listener(tmp_path: Path, monkeypatch) -> None:
    services, profiles = service_manager._load_catalog()
    manager = service_manager.ServiceManager(services, profiles, _options(tmp_path))
    foreign = service_manager.ServiceStatus(
        service_id="model-gateway", title="Model Gateway", state="foreign",
        reason="foreign", port=8802, owned=False, listening=True, ready=True,
        stale=False, desired_fingerprint="b" * 64, running_fingerprint="",
        registry_path="",
    )
    monkeypatch.setattr(manager, "status", lambda service_id: foreign)

    try:
        manager.start({"model-gateway"})
    except service_manager.ServiceManagerError as error:
        assert "foreign listeners" in str(error)
    else:
        raise AssertionError("foreign listener was adopted")


def test_qmd_restart_snapshots_and_restores_computation_targets(tmp_path: Path, monkeypatch) -> None:
    services, profiles = service_manager._load_catalog()
    manager = service_manager.ServiceManager(services, profiles, _options(tmp_path))
    current = service_manager.ServiceStatus(
        service_id="qmd-live", title="QMD Live", state="ready", reason="ready",
        port=8795, owned=True, listening=True, ready=True, stale=False,
        desired_fingerprint="a" * 64, running_fingerprint="a" * 64,
        registry_path="qmd.json",
    )
    targets = [{"target_id": "chart:AAPL"}]
    monkeypatch.setattr(manager, "statuses", lambda selected=None: {"qmd-live": current})
    monkeypatch.setattr(manager, "_qmd_targets", lambda: targets)
    stop = mock.Mock(return_value=["qmd-live"])
    start = mock.Mock(return_value=["qmd-live"])
    restore = mock.Mock()
    monkeypatch.setattr(manager, "stop", stop)
    monkeypatch.setattr(manager, "start", start)
    monkeypatch.setattr(manager, "_restore_qmd_targets", restore)

    manager.restart({"qmd-live"})

    stop.assert_called_once_with({"qmd-live"}, dry_run=False)
    start.assert_called_once_with({"qmd-live"}, dry_run=False)
    restore.assert_called_once_with(targets)
