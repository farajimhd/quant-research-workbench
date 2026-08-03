from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"


def _source(name: str) -> str:
    return (SCRIPTS / name).read_text(encoding="utf-8").lower()


def test_workspace_stop_uses_registered_ownership_not_global_command_matches() -> None:
    source = _source("stop_workspace_services.ps1")

    assert "read-validregistration" in source
    assert "test-processstartidentity" in source
    assert "get-ownedprocessids" in source
    assert "test-workspaceserviceprocess" not in source
    assert 'commandline.contains("vite")' not in source
    assert 'commandline.contains("run_frontend.py")' not in source
    assert "get-portownerids" not in source
    assert "foreign processes and ports were left untouched" in source


def test_workspace_start_registers_each_role_and_rejects_foreign_port_adoption() -> None:
    source = _source("start_workspace_services.ps1")

    for role in ("qmd_history", "backend", "frontend"):
        assert f'role = "{role}"' in source
    for argument in (
        '"-registrypath"',
        '"-servicerole"',
        '"-serviceport"',
        '"-instanceid"',
        '"-repositoryroot"',
    ):
        assert argument in source
    assert "startup refuses to adopt existing port owners" in source


def test_tab_host_owns_children_with_manifest_and_kill_on_close_job() -> None:
    source = _source("run_windows_terminal_service_tab.ps1")

    assert "jobobjectlimitkillonjobclose" in source
    assert "assignprocesstojobobject" in source
    assert "host_started_at_utc" in source
    assert "child_started_at_utc" in source
    assert "registered_at_utc" in source
    assert "eventwaithandle" in source


def test_qmd_cargo_output_is_external_and_binary_is_executed_directly() -> None:
    source = _source("run_qmd_history_gateway.ps1")

    assert r"d:\tradingml\runtimes\qmd_history_gateway\cargo-target" in source
    assert "--target-dir $resolvedcargotargetdir" in source
    assert "cargo run" not in source
    assert '& $gatewayexecutable' in source
    assert "cargo output must be outside the repository" in source
