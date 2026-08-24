from __future__ import annotations

import importlib.util
import threading
import time
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
    assert "datetimeoffset]::parseexact(" in source
    assert "expectedutc -is [datetime]" in source
    assert "-expectedutc $record.host_started_at_utc" in source
    assert "datetimestyles]::roundtripkind" in source
    assert "cultureinfo]::invariantculture" in source
    assert source.index("miniconda3\\python.exe") < source.index("get-command python")
    assert "\\microsoft\\windowsapps\\" in source
    assert "foreign processes and ports were left untouched" in source


def test_workspace_start_registers_each_role_and_rejects_foreign_port_adoption() -> None:
    source = _source("start_workspace_services.ps1")

    for role in ("qmd_history", "backend", "frontend", "bar_gpt"):
        assert f'role = "{role}"' in source
    assert 'role = "qmd_live"' not in source
    assert "qmdliveport" not in source
    assert 'join-path $psscriptroot "run_qmd_gateway.ps1"' not in source
    for argument in (
        '"-registrypath"',
        '"-servicerole"',
        '"-serviceport"',
        '"-instanceid"',
        '"-repositoryroot"',
    ):
        assert argument in source
    assert "startup refuses to adopt existing port owners" in source
    assert "assert-repositorygitsize" in source
    assert "maintain_repository_git.ps1 -compact" in source
    assert "maxgitdirectorygb = 2.0" in source
    assert "pythondontwritebytecode" in source
    assert "retrying that tab once" in source
    assert source.index("miniconda3\\python.exe") < source.index("get-command python")
    assert "\\microsoft\\windowsapps\\" in source


def test_workspace_stop_has_no_qmd_live_shutdown_authority() -> None:
    source = _source("stop_workspace_services.ps1")

    assert '$serviceroles = @("qmd_history", "backend", "frontend", "bar_gpt")' in source
    assert "qmdliveport" not in source
    assert "leaving independently managed service ownership record untouched" in source


def test_application_manager_composes_existing_owned_lifecycles() -> None:
    source = _source("manage_application_services.py")

    assert '"start_qmd_live_gateway.ps1"' in source
    assert '"stop_qmd_live_gateway.ps1"' in source
    assert '"start_workspace_services.ps1"' in source
    assert '"stop_workspace_services.ps1"' in source
    assert '"-withbargpt"' in source
    assert '"-bargptreleasemanifest"' in source
    assert "checkpoint_sha256" in source
    assert "contract_hash" in source
    assert 'if args.action == "start"' in source
    assert "prepare_bar_gpt_release_manifest.py" in source
    assert '"-pythonexe"' in source
    assert '"--qmd-live-host-role"' in source
    assert 'default="laptop"' in source
    assert '["-hostrole", qmd_live_host_role, "-terminaltarget", terminal_target]' in source
    assert "effective host role does not match the request" in source
    assert 'qmd_health.get("host_role", "")' in source
    assert source.index("_validate_manifest(args.release_manifest)") < source.index("stop(keep_qmd_live=true)")
    assert "qmd live is already healthy; preserving its stream" in source
    assert source.index('"stop_workspace_services.ps1"') < source.index('"stop_qmd_live_gateway.ps1"')


def test_qmd_live_has_separate_managed_start_and_stop_scripts() -> None:
    start_source = _source("start_qmd_live_gateway.ps1")
    stop_source = _source("stop_qmd_live_gateway.ps1")

    assert 'join-path $psscriptroot "run_qmd_gateway.ps1"' in start_source
    assert 'role = "qmd_live"' in start_source
    assert '[validateset("laptop", "workstation")]' in start_source
    assert '[string]$hostrole = "laptop"' in start_source
    assert '" -hostrole "' in start_source
    assert '"-servicerole"' in start_source
    assert "startup refuses to adopt an existing port owner" in start_source
    assert '"qmd_live"' in stop_source
    assert start_source.index("miniconda3\\python.exe") < start_source.index("get-command python")
    assert "\\microsoft\\windowsapps\\" in start_source
    assert "read-validregistration" in stop_source
    assert "test-processstartidentity" in stop_source
    assert "datetimeoffset]::parseexact(" in stop_source
    assert "expectedutc -is [datetime]" in stop_source
    assert "datetimestyles]::roundtripkind" in stop_source
    assert "cultureinfo]::invariantculture" in stop_source
    assert stop_source.index("miniconda3\\python.exe") < stop_source.index("get-command python")
    assert "\\microsoft\\windowsapps\\" in stop_source
    assert "legacyworkspaceruntimeroot" in stop_source
    assert r"d:\tradingml\runtimes\workspace_services" in stop_source
    assert "foreign processes and ports were left untouched" in stop_source


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
    live_source = _source("run_qmd_gateway.ps1")

    assert r"d:\tradingml\runtimes\qmd_history_gateway\cargo-target" in source
    assert "--target-dir $resolvedcargotargetdir" in source
    assert "cargo run" not in source
    assert '& $gatewayexecutable' in source
    assert "cargo output must be outside the repository" in source
    assert r"d:\tradingml\runtimes\qmd_gateway" in live_source
    assert 'join-path $resolvedruntimeroot "logs"' in live_source
    assert '$env:qmd_host_role = $hostrole.tolowerinvariant()' in live_source
    assert '$env:qmd_derived_reconciliation_enabled = "true"' in live_source
    assert '$env:qmd_derived_reconciliation_interval_ms = "300000"' in live_source
    assert '$env:qmd_derived_coverage_table = "qmd_derived_coverage_v1"' in live_source
    assert '$env:qmd_historical_pipeline_python = $python' in live_source
    assert '$env:qmd_historical_update_runtime_root = $historicalupdateruntimeroot' in live_source
    assert "workstation qmd historical autorun requires an exact python executable" in live_source
    assert "workstation qmd historical autorun requires the updater script" in live_source
    assert live_source.index("qmd_historical_pipeline_python") < live_source.index("if ($checkonly)")
    assert 'join-path $reporoot ".tmp' not in live_source


def test_qmd_live_survives_an_unexpected_terminal_monitor_exit() -> None:
    source = _source("run_qmd_gateway.ps1")

    assert "$terminalexitcode -ne 130" in source
    assert "the healthy gateway will remain supervised" in source
    assert "$gatewayprocess.waitforexit(1000)" in source
    assert "a presentation failure must not terminate live market-data authority" in source


def test_qmd_structure_checkpoint_rebuild_uses_ephemeral_operator_authority() -> None:
    launcher = _source("run_qmd_gateway.ps1")
    rebuild = _source("rebuild_qmd_structure_checkpoint.ps1")

    assert "qmd_operator_token" in launcher
    assert "operator_token.dpapi" in launcher
    assert "protecteddata]::protect" in launcher
    assert "dataprotectionscope]::localmachine" in launcher
    assert "icacls.exe" in launcher
    assert "inheritance:r" in launcher
    assert "operatortokentemporarypath" in launcher
    assert '"${operatortokenidentity}:(f)"' in launcher
    assert "copy-item -literalpath $operatortokentemporarypath" in launcher
    assert "remove-item env:qmd_operator_token" in launcher
    assert "remove-item -literalpath $operatortokenpath" in launcher
    assert "x-qmd-operator-token" in rebuild
    assert "protecteddata]::unprotect" in rebuild
    assert "supportsshouldprocess" in rebuild
    assert "planonly" in rebuild
    assert "[datetime]::utcnow.adddays(-7)" in rebuild
    assert "source-plan" in rebuild
    assert "uncovered segment" in rebuild


def test_direct_cargo_commands_also_write_outside_the_repository() -> None:
    cargo_config = (REPO_ROOT / ".cargo" / "config.toml").read_text(encoding="utf-8").lower()

    assert 'target-dir = "d:/tradingml/runtimes/cargo-target/quant-research-workbench"' in cargo_config
    assert not (REPO_ROOT / "target").exists()


def test_direct_python_and_pytest_commands_do_not_create_repository_caches() -> None:
    sitecustomize = (REPO_ROOT / "sitecustomize.py").read_text(encoding="utf-8").lower()
    pytest_bootstrap = (REPO_ROOT / "conftest.py").read_text(encoding="utf-8").lower()
    pytest_config = (REPO_ROOT / "pytest.ini").read_text(encoding="utf-8").lower()

    assert "sys.dont_write_bytecode = true" in sitecustomize
    assert "sys.dont_write_bytecode = true" in pytest_bootstrap
    assert "-p no:cacheprovider" in pytest_config


def test_frontend_invokes_npm_without_the_windows_batch_wrapper() -> None:
    module_path = SCRIPTS / "run_frontend.py"
    spec = importlib.util.spec_from_file_location("run_frontend_under_test", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    command = module.npm_command()

    assert Path(command[0]).name.lower() == "node.exe"
    assert Path(command[1]).name.lower() == "npm-cli.js"
    assert all(Path(part).suffix.lower() not in {".cmd", ".bat"} for part in command)
    assert module.sys.dont_write_bytecode is True


def test_frontend_dev_mirror_refreshes_external_source_without_touching_dependencies(
    tmp_path: Path,
) -> None:
    module_path = SCRIPTS / "run_frontend.py"
    spec = importlib.util.spec_from_file_location("run_frontend_mirror_under_test", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    source_root = tmp_path / "source"
    runtime_root = tmp_path / "runtime"
    for name in module.SYNCED_DIRECTORIES:
        (source_root / name).mkdir(parents=True)
        (source_root / name / f"{name}.txt").write_text("initial", encoding="utf-8")
    for name in module.SYNCED_FILES:
        (source_root / name).write_text(f"initial:{name}", encoding="utf-8")
    dependency_sentinel = runtime_root / "node_modules" / "sentinel.txt"
    dependency_sentinel.parent.mkdir(parents=True)
    dependency_sentinel.write_text("preserve", encoding="utf-8")

    module.SOURCE_ROOT = source_root
    module.DEV_SYNC_INTERVAL_SECONDS = 0.01
    module.sync_frontend(runtime_root)
    stop_event = threading.Event()
    mirror = threading.Thread(
        target=module.mirror_frontend_source,
        args=(runtime_root, stop_event),
        daemon=True,
    )
    mirror.start()
    source_file = source_root / "src" / "src.txt"
    runtime_file = runtime_root / "src" / "src.txt"
    try:
        source_file.write_text("updated typography", encoding="utf-8")
        deadline = time.monotonic() + 2.0
        while runtime_file.read_text(encoding="utf-8") != "updated typography":
            assert time.monotonic() < deadline
            time.sleep(0.01)

        source_file.unlink()
        deadline = time.monotonic() + 2.0
        while runtime_file.exists():
            assert time.monotonic() < deadline
            time.sleep(0.01)
    finally:
        stop_event.set()
        mirror.join(timeout=1.0)

    assert not mirror.is_alive()
    assert dependency_sentinel.read_text(encoding="utf-8") == "preserve"


def test_git_maintenance_backs_up_before_pruning_unreachable_objects() -> None:
    source = _source("maintain_repository_git.ps1")

    assert r"d:\tradingml\runtimes\repository_maintenance" in source
    assert "new-item -itemtype hardlink" in source
    assert '"fsck", "--connectivity-only"' in source
    assert '"reflog", "expire", "--expire-unreachable=now", "--all"' in source
    assert '"gc", "--prune=now"' in source


def test_gateway_launchers_resolve_machine_specific_code_authority() -> None:
    authority = _source("repository_code_authority.ps1")
    reference = _source("run_reference_gateway.ps1")
    live_services = _source("start_live_gateway_services.ps1")

    assert r"d:\tradingcodes\quant-research-workbench" in authority
    assert r"d:\tradingml\codes\quant-research-workbench" in authority
    assert "do not start services from a fallback checkout" in authority
    assert "resolve-repositorycodeauthority" in reference
    assert "& $authoritativelauncher @psboundparameters" in reference
    assert "resolve-repositorycodeauthority" in live_services
    assert '$launcherroot = join-path $reporoot "scripts"' in live_services
    assert 'join-path $psscriptroot "run_reference_gateway.ps1"' not in live_services
