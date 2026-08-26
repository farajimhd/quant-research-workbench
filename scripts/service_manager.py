"""Unified, ownership-aware lifecycle manager for repository services."""

from __future__ import annotations

import argparse
import base64
import contextlib
import ctypes
from dataclasses import dataclass, field
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
from typing import Any, Iterable, Iterator, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import uuid


sys.dont_write_bytecode = True
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(line_buffering=True)

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
CATALOG_PATH = SCRIPTS / "service_catalog.json"
DEFAULT_RUNTIME_ROOT = Path(
    os.environ.get("QW_SERVICE_MANAGER_RUNTIME_ROOT", r"D:\TradingML\runtimes\service_manager")
)
DEFAULT_BAR_GPT_MANIFEST = Path(
    os.environ.get(
        "BAR_GPT_RELEASE_MANIFEST",
        r"D:\TradingML\runtimes\bar_gpt_service\configuration\releases.json",
    )
)
DEFAULT_TEXT_INTELLIGENCE_MANIFEST = Path(
    os.environ.get(
        "TEXT_INTELLIGENCE_FORECAST_RELEASE_MANIFEST",
        r"D:\TradingML\runtimes\text_intelligence\serving\news_forecast_funnel_v1\release.json",
    )
)
DYNAMIC_TARGETS = {"dev", "stale", "unhealthy", "stopped"}


class ServiceManagerError(RuntimeError):
    pass


@dataclass(frozen=True)
class Service:
    service_id: str
    title: str
    role: str
    port: int
    health_url: str
    launch_kind: str
    launcher: str
    arguments: tuple[str, ...]
    environment: Mapping[str, str]
    dependencies: tuple[str, ...]
    watch: tuple[str, ...]
    artifact_paths: tuple[str, ...]
    fingerprint_env: tuple[str, ...]
    start_priority: int
    graceful_timeout_seconds: int
    readiness: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ServiceStatus:
    service_id: str
    title: str
    state: str
    reason: str
    port: int
    owned: bool
    listening: bool
    ready: bool
    stale: bool
    desired_fingerprint: str
    running_fingerprint: str
    registry_path: str
    drift_components: tuple[str, ...] = ()
    run_log_root: str = ""


@dataclass(frozen=True)
class ManagerOptions:
    runtime_root: Path
    python: Path
    terminal_window: str
    terminal_target: str
    qmd_live_host_role: str
    ibkr_account: str
    bar_gpt_release_manifest: Path
    text_intelligence_release_manifest: Path
    timeout_seconds: int


def _load_catalog(path: Path = CATALOG_PATH) -> tuple[dict[str, Service], dict[str, tuple[str, ...]]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if int(payload.get("schema_version", 0)) != 1:
        raise ServiceManagerError(f"unsupported service catalog schema: {path}")
    raw_services = payload.get("services")
    raw_profiles = payload.get("profiles")
    if not isinstance(raw_services, dict) or not isinstance(raw_profiles, dict):
        raise ServiceManagerError("service catalog must define object-valued services and profiles")
    services: dict[str, Service] = {}
    for service_id, row in raw_services.items():
        if not isinstance(row, dict):
            raise ServiceManagerError(f"service entry must be an object: {service_id}")
        services[service_id] = Service(
            service_id=service_id,
            title=str(row["title"]),
            role=str(row["role"]),
            port=int(row["port"]),
            health_url=str(row["health_url"]),
            launch_kind=str(row.get("launch_kind") or "powershell"),
            launcher=str(row.get("launcher") or ""),
            arguments=tuple(str(value) for value in row.get("arguments") or ()),
            environment={str(key): str(value) for key, value in (row.get("environment") or {}).items()},
            dependencies=tuple(str(value) for value in row.get("dependencies") or ()),
            watch=tuple(str(value) for value in row.get("watch") or ()),
            artifact_paths=tuple(str(value) for value in row.get("artifact_paths") or ()),
            fingerprint_env=tuple(str(value) for value in row.get("fingerprint_env") or ()),
            start_priority=int(row.get("start_priority") or 50),
            graceful_timeout_seconds=int(row.get("graceful_timeout_seconds") or 30),
            readiness=dict(row.get("readiness") or {}),
        )
    profiles = {
        str(name): tuple(str(value) for value in values)
        for name, values in raw_profiles.items()
        if isinstance(values, list)
    }
    known = set(services)
    for service in services.values():
        missing = set(service.dependencies) - known
        if missing:
            raise ServiceManagerError(f"{service.service_id} has unknown dependencies: {sorted(missing)}")
    for name, members in profiles.items():
        missing = set(members) - known
        if missing:
            raise ServiceManagerError(f"profile {name} has unknown services: {sorted(missing)}")
    _topological_order(services, set(services))
    return services, profiles


def _topological_order(services: Mapping[str, Service], selected: set[str]) -> list[str]:
    ordered: list[str] = []
    temporary: set[str] = set()
    permanent: set[str] = set()

    def visit(service_id: str) -> None:
        if service_id in permanent:
            return
        if service_id in temporary:
            raise ServiceManagerError(f"service dependency cycle includes {service_id}")
        temporary.add(service_id)
        for dependency in services[service_id].dependencies:
            if dependency in selected:
                visit(dependency)
        temporary.remove(service_id)
        permanent.add(service_id)
        ordered.append(service_id)

    for service_id in sorted(services, key=lambda value: (services[value].start_priority, value)):
        if service_id in selected:
            visit(service_id)
    return ordered


def _dependency_closure(services: Mapping[str, Service], selected: set[str]) -> set[str]:
    result = set(selected)
    pending = list(selected)
    while pending:
        service_id = pending.pop()
        for dependency in services[service_id].dependencies:
            if dependency not in result:
                result.add(dependency)
                pending.append(dependency)
    return result


def _dependent_closure(services: Mapping[str, Service], selected: set[str]) -> set[str]:
    """Return services whose active lifecycle depends on the selected services."""

    result = set(selected)
    changed = True
    while changed:
        changed = False
        for service_id, service in services.items():
            if service_id not in result and any(dependency in result for dependency in service.dependencies):
                result.add(service_id)
                changed = True
    return result


def _powershell() -> str:
    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    candidate = system_root / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    return str(candidate) if candidate.is_file() else "powershell.exe"


def _windows_terminal() -> str:
    command = shutil.which("wt.exe")
    if command:
        return command
    candidate = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WindowsApps" / "wt.exe"
    if candidate.is_file():
        return str(candidate)
    raise ServiceManagerError("Windows Terminal was not found; install it or make wt.exe available")


def _pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    process_query_limited_information = 0x1000
    handle = ctypes.windll.kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return False
    ctypes.windll.kernel32.CloseHandle(handle)
    return True


def _port_open(port: int, timeout: float = 0.2) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def _http_probe(
    url: str,
    timeout: float = 2.0,
    max_body_bytes: int = 262_144,
) -> tuple[bool, str, dict[str, Any] | None]:
    request = Request(url, headers={"User-Agent": "qw-service-manager/1"})
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read(max_body_bytes + 1)
            if len(body) > max_body_bytes:
                return False, f"health response exceeds {max_body_bytes} bytes", None
            if not 200 <= response.status < 400:
                return False, f"HTTP {response.status}", None
            content_type = str(response.headers.get("Content-Type") or "")
            if "json" not in content_type.lower():
                return True, f"HTTP {response.status}", None
            payload = json.loads(body.decode("utf-8"))
            if not isinstance(payload, dict):
                return False, "health response is not an object", None
            declared = str(payload.get("service_status") or payload.get("status") or "").lower()
            if declared in {"failed", "error", "offline", "stopped", "unavailable"}:
                return False, f"declared {declared}", payload
            return True, f"HTTP {response.status}", payload
    except HTTPError as error:
        return False, f"HTTP {error.code}", None
    except (URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
        detail = error.reason if isinstance(error, URLError) else error
        return False, str(detail), None


def _semantic_readiness(
    service: Service,
    payload: dict[str, Any],
) -> tuple[bool, str, bool]:
    """Return ready, operator detail, and whether failure is a degradation."""
    rule = service.readiness
    accepted = {str(value).lower() for value in rule.get("accepted_statuses") or ()}
    declared = str(payload.get("service_status") or payload.get("status") or "").lower()
    if accepted and declared not in accepted:
        return False, f"status={declared or 'missing'}", declared in {"degraded", "warning", "catching_up"}

    metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {}
    if bool(rule.get("require_running")) and payload.get("running") is not True:
        return False, "running flag is not true", False

    kind = str(rule.get("kind") or "transport")
    if kind == "ibkr":
        expected = {
            "gateway_status": "ready",
            "auth_status": "authenticated",
            "keepalive_status": "ok",
            "clickhouse_status": "ready",
        }
        mismatches = [
            f"{key}={str(metrics.get(key) or 'missing').lower()}"
            for key, value in expected.items()
            if str(metrics.get(key) or "").lower() != value
        ]
        if metrics.get("supervisor_thread_alive") is not True:
            mismatches.append("supervisor_thread_alive=false")
        if mismatches:
            return False, " ".join(mismatches), True
    return True, f"semantic status={declared or 'reachable'}", False


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)


def _atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    temporary.write_text(value, encoding="utf-8")
    os.replace(temporary, path)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        # Windows PowerShell's UTF8 Set-Content emits a BOM. Ownership records
        # are intentionally readable from both Windows PowerShell 5 and Python.
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _record_matches_service(record: Mapping[str, Any], service: Service, registry_path: Path) -> bool:
    return _record_identity_matches(record, service, registry_path) and _pid_exists(
        int(record.get("host_pid") or 0)
    )


def _record_identity_matches(record: Mapping[str, Any], service: Service, registry_path: Path) -> bool:
    try:
        record_repo = Path(str(record.get("repository_root") or "")).resolve()
        record_path = Path(str(record.get("registry_path") or "")).resolve()
    except (OSError, ValueError):
        return False
    return (
        int(record.get("schema_version") or 0) == 1
        and str(record.get("service_role") or "") == service.role
        and os.path.normcase(str(record_repo)) == os.path.normcase(str(REPO_ROOT.resolve()))
        and os.path.normcase(str(record_path)) == os.path.normcase(str(registry_path.resolve()))
    )


def _format_values(options: ManagerOptions) -> dict[str, str]:
    return {
        "python": str(options.python),
        "ibkr_account": options.ibkr_account,
        "bar_gpt_release_manifest": str(options.bar_gpt_release_manifest),
        "text_intelligence_release_manifest": str(options.text_intelligence_release_manifest),
        "qmd_live_host_role": options.qmd_live_host_role,
    }


def _format(value: str, options: ManagerOptions) -> str:
    return value.format_map(_format_values(options))


def _launch_inputs(service: Service, options: ManagerOptions) -> dict[str, Any]:
    return {
        "service_id": service.service_id,
        "role": service.role,
        "port": service.port,
        "launch_kind": service.launch_kind,
        "launcher": service.launcher,
        "arguments": [_format(value, options) for value in service.arguments],
        "environment": {key: _format(value, options) for key, value in service.environment.items()},
        "dependencies": service.dependencies,
        "watch": service.watch,
        "artifact_paths": tuple(_format(value, options) for value in service.artifact_paths),
        "fingerprint_env": service.fingerprint_env,
        "start_priority": service.start_priority,
        "effective_nonsecret_environment": {
            key: os.environ.get(key, "") for key in service.fingerprint_env
        },
        "qmd_live_host_role": options.qmd_live_host_role if service.service_id == "qmd-live" else "",
    }


def _hash_json(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


_GENERATED_SOURCE_DIRECTORY_NAMES = frozenset(
    {
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "node_modules",
        "dist",
    }
)


def _is_generated_source_path(path: Path) -> bool:
    """Keep runtime/test artifacts from creating false source drift."""
    return (
        any(part.lower() in _GENERATED_SOURCE_DIRECTORY_NAMES for part in path.parts)
        or path.suffix.lower() in {".pyc", ".pyo"}
        or path.name.lower() in {".coverage"}
    )


def _fingerprint_components(service: Service, options: ManagerOptions) -> dict[str, str]:
    launch_inputs = _launch_inputs(service, options)
    source_digest = hashlib.sha256()
    matched: set[Path] = set()
    tab_host = REPO_ROOT / "scripts" / "run_windows_terminal_service_tab.ps1"
    if tab_host.is_file():
        matched.add(tab_host)
    for pattern in service.watch:
        for path in REPO_ROOT.glob(pattern):
            if path.is_file() and not _is_generated_source_path(path):
                matched.add(path)
            elif path.is_dir():
                matched.update(
                    child
                    for child in path.rglob("*")
                    if child.is_file() and not _is_generated_source_path(child)
                )
    for path in sorted(matched, key=lambda item: item.as_posix().lower()):
        relative = path.relative_to(REPO_ROOT).as_posix()
        source_digest.update(relative.encode("utf-8"))
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                source_digest.update(chunk)
    artifact_digest = hashlib.sha256()
    for raw_path in service.artifact_paths:
        artifact = Path(_format(raw_path, options)).expanduser()
        artifact_digest.update(str(artifact).encode("utf-8"))
        if artifact.is_file():
            with artifact.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    artifact_digest.update(chunk)
        else:
            artifact_digest.update(b"<missing>")
    environment = launch_inputs.pop("effective_nonsecret_environment")
    role = launch_inputs.pop("qmd_live_host_role")
    return {
        "launch": _hash_json(launch_inputs),
        "environment": _hash_json(environment),
        "host_role": _hash_json(role),
        "source": source_digest.hexdigest(),
        "artifacts": artifact_digest.hexdigest(),
    }


def _fingerprint(service: Service, options: ManagerOptions) -> str:
    return _hash_json(_fingerprint_components(service, options))


class ServiceManager:
    def __init__(
        self,
        services: Mapping[str, Service],
        profiles: Mapping[str, tuple[str, ...]],
        options: ManagerOptions,
    ) -> None:
        self.services = dict(services)
        self.profiles = dict(profiles)
        self.options = options
        self.runtime_root = options.runtime_root.resolve()
        if self.runtime_root == REPO_ROOT or REPO_ROOT in self.runtime_root.parents:
            raise ServiceManagerError(f"service-manager runtime must be outside the repository: {self.runtime_root}")
        self.registry_root = self.runtime_root / "instances"
        self.state_root = self.runtime_root / "state"
        self.qmd_runtime_root = self.runtime_root / "qmd-live"
        self._fingerprints: dict[str, str] = {}
        self._fingerprint_component_cache: dict[str, dict[str, str]] = {}

    def desired_fingerprint(self, service_id: str) -> str:
        if service_id not in self._fingerprints:
            self._fingerprints[service_id] = _hash_json(self.desired_fingerprint_components(service_id))
        return self._fingerprints[service_id]

    def desired_fingerprint_components(self, service_id: str) -> dict[str, str]:
        if service_id not in self._fingerprint_component_cache:
            self._fingerprint_component_cache[service_id] = _fingerprint_components(
                self.services[service_id], self.options
            )
        return dict(self._fingerprint_component_cache[service_id])

    def _state_path(self, service_id: str) -> Path:
        return self.state_root / f"{service_id}.json"

    def _default_registry_path(self, service_id: str) -> Path:
        return self.registry_root / f"{service_id}.json"

    def _state(self, service_id: str) -> dict[str, Any]:
        return _read_json(self._state_path(service_id)) or {}

    def _registry_path(self, service_id: str) -> Path:
        state = self._state(service_id)
        recorded = str(state.get("registry_path") or "")
        return Path(recorded) if recorded else self._default_registry_path(service_id)

    def _probe(self, service: Service) -> tuple[bool, str, dict[str, Any] | None]:
        ready, detail, payload = _http_probe(service.health_url)
        if not ready or payload is None:
            return ready, detail, payload
        semantic_ready, semantic_detail, degraded = _semantic_readiness(service, payload)
        if service.readiness.get("kind") == "qmd_live" and semantic_ready:
            snapshot_url = str(service.readiness.get("snapshot_url") or "").strip()
            snapshot_ready, snapshot_detail, snapshot = _http_probe(
                snapshot_url, timeout=4.0, max_body_bytes=32 * 1024 * 1024
            )
            if not snapshot_ready or snapshot is None:
                semantic_ready, semantic_detail = False, f"status snapshot unavailable: {snapshot_detail}"
            else:
                declared = str(payload.get("service_status") or payload.get("status") or "").lower()
                calendar = payload.get("market_calendar") if isinstance(payload.get("market_calendar"), dict) else {}
                inactive_catch_up = (
                    declared in {"catching_up", "closed"}
                    and calendar.get("active_collection_window") is not True
                )
                required_lanes = []
                health_operational = payload.get("operational")
                if isinstance(health_operational, dict):
                    required_lanes = [
                        row for row in health_operational.get("lanes") or []
                        if isinstance(row, dict) and row.get("required") is True
                    ]
                failed_lanes = [
                    str(row.get("key") or "unknown")
                    for row in required_lanes
                    if str(row.get("state") or "").lower() != "healthy"
                    and not (
                        inactive_catch_up
                        and str(row.get("state") or "").lower() in {"starting", "connecting"}
                        and int(row.get("pending_rows") or 0) == 0
                        and int(row.get("failures") or 0) == 0
                        and int(row.get("consecutive_failures") or 0) == 0
                    )
                ]
                saturation = float(service.readiness.get("queue_saturation_ratio") or 0.95)
                saturated_lanes = [
                    str(row.get("key") or "unknown")
                    for row in required_lanes
                    if int(row.get("max_pending_rows") or 0) > 0
                    and int(row.get("pending_rows") or 0) / int(row.get("max_pending_rows") or 1) >= saturation
                ]
                snapshot_error = snapshot.get("error_state") if isinstance(snapshot.get("error_state"), dict) else {}
                if failed_lanes:
                    semantic_ready, semantic_detail, degraded = False, f"required lanes failed: {','.join(failed_lanes)}", True
                elif saturated_lanes:
                    semantic_ready, semantic_detail, degraded = False, f"required queues saturated: {','.join(saturated_lanes)}", True
                elif snapshot_error.get("active") is True:
                    semantic_ready, semantic_detail, degraded = False, str(snapshot_error.get("message") or "active QMD degradation"), True
                else:
                    runtime = snapshot.get("runtime") if isinstance(snapshot.get("runtime"), dict) else {}
                    if calendar.get("active_collection_window") is True and calendar.get("market_closed") is not True:
                        lag_ms = int(runtime.get("last_event_lag_ms") or 0)
                        max_lag_ms = int(float(service.readiness.get("max_event_lag_seconds") or 30) * 1000)
                        if lag_ms > max_lag_ms:
                            semantic_ready, semantic_detail, degraded = False, f"live event lag {lag_ms}ms exceeds {max_lag_ms}ms", True
        declared_state = str(payload.get("service_status") or payload.get("status") or "").lower()
        manager_detail = {
            "degraded": degraded,
            "detail": semantic_detail,
            "state": declared_state,
        }
        payload = {**payload, "_service_manager": manager_detail}
        return semantic_ready, semantic_detail, payload

    def status(self, service_id: str) -> ServiceStatus:
        service = self.services[service_id]
        desired = self.desired_fingerprint(service_id)
        registry_path = self._registry_path(service_id)
        record = _read_json(registry_path) or {}
        owned = bool(record) and _record_matches_service(record, service, registry_path)
        listening = _port_open(service.port)
        ready, health_detail, health = self._probe(service) if listening else (False, "port closed", None)
        error_state = (health or {}).get("error_state") if isinstance(health, dict) else {}
        manager_state = (health or {}).get("_service_manager") if isinstance(health, dict) else {}
        degraded = bool(
            isinstance(error_state, dict)
            and (
                int(error_state.get("active_critical_count") or 0) > 0
                or int(error_state.get("active_error_count") or 0) > 0
            )
        ) or str((health or {}).get("status") or "").lower() == "degraded" or bool(
            isinstance(manager_state, dict) and manager_state.get("degraded")
        )
        semantic_state = str(manager_state.get("state") or "").lower() if isinstance(manager_state, dict) else ""
        running_fingerprint = str(record.get("desired_fingerprint") or "")
        if not running_fingerprint:
            running_fingerprint = str(self._state(service_id).get("last_fingerprint") or "")
        stale = bool(running_fingerprint) and running_fingerprint != desired
        desired_components = self.desired_fingerprint_components(service_id)
        running_components = record.get("fingerprint_components")
        if not isinstance(running_components, dict):
            running_components = self._state(service_id).get("fingerprint_components") or {}
        drift_components = tuple(
            key for key in desired_components
            if running_components and str(running_components.get(key) or "") != desired_components[key]
        )
        if listening and not owned:
            health_suffix = f"; health: {health_detail}" if not ready else ""
            state, reason = "foreign", "listener is not owned by the unified manager" + health_suffix
        elif owned and listening and degraded:
            state, reason = "degraded", "health contract reports active error or critical state"
        elif owned and ready and stale:
            changed = ", ".join(drift_components) or "unknown component"
            state, reason = "stale", f"changed: {changed}"
        elif owned and ready and semantic_state == "warming":
            state, reason = "warming", health_detail
        elif owned and ready and semantic_state == "catching_up":
            state, reason = "catching_up", health_detail
        elif owned and ready:
            state, reason = "ready", health_detail
        elif owned and listening:
            state, reason = "unhealthy", health_detail
        elif owned:
            state, reason = "starting", health_detail
        elif stale:
            state, reason = "stopped/stale", "stopped; desired fingerprint differs from last start"
        else:
            state, reason = "stopped", health_detail
        return ServiceStatus(
            service_id=service_id,
            title=service.title,
            state=state,
            reason=reason,
            port=service.port,
            owned=owned,
            listening=listening,
            ready=ready,
            stale=stale,
            desired_fingerprint=desired,
            running_fingerprint=running_fingerprint,
            registry_path=str(registry_path),
            drift_components=drift_components,
            run_log_root=str(record.get("run_log_root") or ""),
        )

    def statuses(self, selected: Iterable[str] | None = None) -> dict[str, ServiceStatus]:
        ids = list(selected) if selected is not None else list(self.services)
        return {service_id: self.status(service_id) for service_id in ids}

    def resolve_target(self, target: str, *, within: str = "all") -> set[str]:
        if target in self.services:
            return {target}
        if target in self.profiles:
            return set(self.profiles[target])
        if target not in DYNAMIC_TARGETS:
            choices = sorted(set(self.services) | set(self.profiles) | DYNAMIC_TARGETS)
            raise ServiceManagerError(f"unknown service target {target!r}; choose one of: {', '.join(choices)}")
        scope = self.resolve_target(within) if within != target else set(self.services)
        statuses = self.statuses(scope)
        if target == "dev":
            return {key for key, row in statuses.items() if row.owned and row.stale}
        if target == "stale":
            return {key for key, row in statuses.items() if row.stale}
        if target == "unhealthy":
            return {key for key, row in statuses.items() if row.owned and row.state in {"degraded", "unhealthy", "starting"}}
        return {key for key, row in statuses.items() if not row.listening and not row.owned}

    def print_status(self, selected: set[str], *, json_output: bool = False) -> bool:
        rows = self.statuses(_topological_order(self.services, selected))
        if json_output:
            print(json.dumps([row.__dict__ for row in rows.values()], indent=2, sort_keys=True))
        else:
            terminal_width = max(72, shutil.get_terminal_size(fallback=(120, 24)).columns)
            detail_width = max(8, terminal_width - 66)
            print("Service status")
            print(f"{'SERVICE':<20} {'STATE':<15} {'REVISION':<18} {'PORT':>5}  DETAIL")
            for row in rows.values():
                if row.stale:
                    revision = f"{(row.running_fingerprint or '-')[:7]}->{row.desired_fingerprint[:7]}"
                elif row.running_fingerprint:
                    revision = f"current {row.desired_fingerprint[:7]}"
                else:
                    revision = f"new {row.desired_fingerprint[:7]}"
                detail = row.reason.replace("\r", " ").replace("\n", " ")
                if len(detail) > detail_width:
                    detail = detail[: max(1, detail_width - 3)] + "..."
                print(f"{row.service_id:<20} {row.state:<15} {revision:<18} {row.port:>5}  {detail}")
        return all(row.state in {"ready", "warming", "catching_up"} for row in rows.values())

    def print_groups(self) -> None:
        print("Static profiles")
        for name, members in self.profiles.items():
            print(f"  {name:<18} {', '.join(members)}")
        print("\nIndividual services")
        for service_id in _topological_order(self.services, set(self.services)):
            service = self.services[service_id]
            print(f"  {service_id:<18} {service.title} (port {service.port})")
        print("\nDynamic selectors")
        print("  dev                running manager-owned services with changed fingerprints")
        print("  stale              changed running or previously started services")
        print("  unhealthy          manager-owned services that are not ready")
        print("  stopped            services not currently running")

    def validate(self, selected: set[str]) -> None:
        for service_id in _topological_order(self.services, selected):
            service = self.services[service_id]
            if service.launch_kind == "qmd_live":
                required = [SCRIPTS / "start_qmd_live_gateway.ps1", SCRIPTS / "stop_qmd_live_gateway.ps1"]
            else:
                required = [(REPO_ROOT / service.launcher).resolve()]
            missing = [str(path) for path in required if not path.is_file()]
            if missing:
                raise ServiceManagerError(f"{service_id} launcher is missing: {', '.join(missing)}")
            self._validate_launch_inputs(service)
            if service.launch_kind != "qmd_live":
                _validate_powershell(self._powershell_command(service), service_id)
            fingerprint = self.desired_fingerprint(service_id)
            print(f"[valid]    {service_id} {fingerprint[:12]}")
        print(f"[complete] Validated {len(selected)} service definitions.")

    @contextlib.contextmanager
    def operation_lock(self, action: str) -> Iterator[None]:
        self.runtime_root.mkdir(parents=True, exist_ok=True)
        path = self.runtime_root / "manager.lock"
        payload = json.dumps({"pid": os.getpid(), "action": action, "created_at_utc": _utc_now()})
        while True:
            try:
                descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    handle.write(payload)
                break
            except FileExistsError:
                current = _read_json(path)
                if current is None:
                    raise ServiceManagerError(
                        f"service-manager lock exists but is not readable yet: {path}; retry shortly"
                    )
                owner = int(current.get("pid") or 0)
                if owner and _pid_exists(owner):
                    raise ServiceManagerError(f"another service-manager operation is active: pid={owner}")
                path.unlink(missing_ok=True)
        try:
            yield
        finally:
            path.unlink(missing_ok=True)

    def _record_start(self, service_id: str, registry_path: Path, fingerprint: str) -> None:
        _atomic_json(
            self._state_path(service_id),
            {
                "schema_version": 1,
                "service_id": service_id,
                "registry_path": str(registry_path),
                "last_fingerprint": fingerprint,
                "fingerprint_components": self.desired_fingerprint_components(service_id),
                "launch_inputs": _launch_inputs(self.services[service_id], self.options),
                "last_started_at_utc": _utc_now(),
                "repository_root": str(REPO_ROOT),
            },
        )

    def reconcile_dead_registries(self, selected: Iterable[str]) -> list[Path]:
        archived: list[Path] = []
        for service_id in selected:
            service = self.services[service_id]
            registry_path = self._registry_path(service_id)
            record = _read_json(registry_path) or {}
            if not record or not _record_identity_matches(record, service, registry_path):
                continue
            try:
                host_pid = int(record.get("host_pid") or 0)
            except (TypeError, ValueError):
                continue
            if _pid_exists(host_pid):
                continue
            destination = (
                self.runtime_root / "dead-registry" / service_id /
                f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')}.json"
            )
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(registry_path, destination)
            archived.append(destination)
            print(f"[reconcile] {service_id} archived dead ownership pid={host_pid}")
        return archived

    def _validate_launch_inputs(self, service: Service) -> None:
        if service.service_id == "bar-gpt" and not self.options.bar_gpt_release_manifest.is_file():
            raise ServiceManagerError(
                f"BarGPT release manifest is missing: {self.options.bar_gpt_release_manifest}; "
                "use historical-core/live-core or pass --bar-gpt-release-manifest"
            )
        for raw_path in service.artifact_paths:
            path = Path(_format(raw_path, self.options))
            if service.service_id == "text-intelligence" and not path.is_file():
                raise ServiceManagerError(f"Text Intelligence DeepFM release manifest is missing: {path}")

    def _powershell_command(self, service: Service) -> str:
        lines = ["$env:PYTHONDONTWRITEBYTECODE = '1'"]
        for key, raw_value in service.environment.items():
            lines.append(f"$env:{key} = {_ps_literal(_format(raw_value, self.options))}")
        launcher = (REPO_ROOT / service.launcher).resolve()
        arguments = [_format(value, self.options) for value in service.arguments]
        if service.launch_kind == "python":
            tokens = ["&", _ps_literal(str(self.options.python)), "-B", _ps_literal(str(launcher))]
        else:
            tokens = ["&", _ps_literal(str(launcher))]
        tokens.extend(
            value if service.launch_kind != "python" and value.startswith("-") else _ps_literal(value)
            for value in arguments
        )
        lines.append(" ".join(tokens))
        lines.append("if ($null -ne $LASTEXITCODE) { exit $LASTEXITCODE }")
        return "\n".join(lines)

    def _open_tab(self, service: Service, fingerprint: str, *, dry_run: bool) -> Path:
        self._validate_launch_inputs(service)
        registry_path = self._default_registry_path(service.service_id).resolve()
        if dry_run:
            return registry_path
        registry_path.parent.mkdir(parents=True, exist_ok=True)
        instance_id = uuid.uuid4().hex
        launch_spec_root = (self.runtime_root / "launch-specs" / service.service_id / instance_id).resolve()
        command_path = launch_spec_root / "command.ps1"
        launch_metadata_path = launch_spec_root / "metadata.json"
        _atomic_text(command_path, self._powershell_command(service))
        _atomic_json(launch_metadata_path, {
            "fingerprint_components": self.desired_fingerprint_components(service.service_id),
            "launch_inputs": _launch_inputs(service, self.options),
        })
        window = "0" if self.options.terminal_target == "current" else self.options.terminal_window
        arguments = [
            _windows_terminal(), "-w", window, "new-tab", "--title", service.title,
            "--suppressApplicationTitle", "-d", str(REPO_ROOT), _powershell(), "-NoLogo",
            "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
            str(SCRIPTS / "run_windows_terminal_service_tab.ps1"), "-CommandPath", str(command_path),
            "-PowerShellExe", _powershell(), "-RegistryPath", str(registry_path),
            "-ServiceRole", service.role, "-ServicePort", str(service.port),
            "-InstanceId", instance_id, "-RepositoryRoot", str(REPO_ROOT),
            "-DesiredFingerprint", fingerprint,
            "-LaunchMetadataPath", str(launch_metadata_path),
            "-LogRoot", str((self.runtime_root / "logs").resolve()),
        ]
        completed = subprocess.run(arguments, cwd=REPO_ROOT, check=False)
        if completed.returncode:
            raise ServiceManagerError(f"Windows Terminal failed to start {service.service_id}: exit {completed.returncode}")
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and not registry_path.is_file():
            time.sleep(0.1)
        if not registry_path.is_file():
            raise ServiceManagerError(f"{service.service_id} did not publish its ownership record")
        return registry_path

    def _start_qmd_live(self, fingerprint: str, *, dry_run: bool) -> Path:
        if dry_run:
            return self.qmd_runtime_root / "instances" / "<instance>" / "qmd_live.json"
        launch_metadata = base64.b64encode(json.dumps({
            "fingerprint_components": self.desired_fingerprint_components("qmd-live"),
            "launch_inputs": _launch_inputs(self.services["qmd-live"], self.options),
        }, sort_keys=True).encode("utf-8")).decode("ascii")
        _run_script(
            "start_qmd_live_gateway.ps1",
            [
                "-HostRole", self.options.qmd_live_host_role,
                "-TerminalTarget", "Caller" if self.options.terminal_target == "current" else "Named",
                "-TerminalWindowName", self.options.terminal_window,
                "-QmdLiveServiceRuntimeRoot", str(self.qmd_runtime_root),
                "-PythonExe", str(self.options.python),
                "-DesiredFingerprint", fingerprint,
                "-LaunchMetadataBase64", launch_metadata,
            ],
        )
        deadline = time.monotonic() + 10
        matches: list[Path] = []
        while time.monotonic() < deadline:
            matches = list((self.qmd_runtime_root / "instances").glob("*/qmd_live.json"))
            if matches:
                break
            time.sleep(0.1)
        if not matches:
            raise ServiceManagerError("QMD Live did not publish its ownership record")
        registry_path = max(matches, key=lambda path: path.stat().st_mtime_ns)
        record = _read_json(registry_path) or {}
        record["desired_fingerprint"] = fingerprint
        record["fingerprint_components"] = self.desired_fingerprint_components("qmd-live")
        record["launch_inputs"] = _launch_inputs(self.services["qmd-live"], self.options)
        _atomic_json(registry_path, record)
        return registry_path

    def _wait_ready(self, service: Service) -> None:
        deadline = time.monotonic() + self.options.timeout_seconds
        last_detail = "not checked"
        while time.monotonic() < deadline:
            ready, last_detail, _ = self._probe(service)
            if ready:
                print(f"[ready]    {service.service_id}")
                return
            time.sleep(1)
        raise ServiceManagerError(
            f"readiness timeout for {service.service_id} after {self.options.timeout_seconds}s: {last_detail}"
        )

    def start(self, selected: set[str], *, dry_run: bool = False) -> list[str]:
        closure = _dependency_closure(self.services, selected)
        ordered = _topological_order(self.services, closure)
        if not dry_run:
            self.reconcile_dead_registries(ordered)
        initial = self.statuses(ordered)
        foreign = [row for row in initial.values() if row.state == "foreign"]
        if foreign:
            detail = ", ".join(f"{row.service_id}:{row.port}" for row in foreign)
            raise ServiceManagerError(
                f"start refuses foreign listeners ({detail}); stop them with their legacy authorities first"
            )
        started: list[str] = []
        try:
            for service_id in ordered:
                service = self.services[service_id]
                current = initial[service_id]
                if current.owned or current.listening:
                    if service_id == "qmd-live":
                        _, _, payload = self._probe(service)
                        effective_role = str((payload or {}).get("host_role") or "").lower()
                        requested_role = self.options.qmd_live_host_role.lower()
                        if effective_role != requested_role:
                            raise ServiceManagerError(
                                "QMD Live is running with a different host role: "
                                f"requested={requested_role} effective={effective_role or 'missing'}"
                            )
                    suffix = "; stale, use 'restart dev'" if current.stale else ""
                    print(f"[preserve] {service_id} is already running{suffix}")
                    continue
                fingerprint = self.desired_fingerprint(service_id)
                print(f"[start]    {service_id}" + (" (plan)" if dry_run else ""))
                registry_path = (
                    self._start_qmd_live(fingerprint, dry_run=dry_run)
                    if service.launch_kind == "qmd_live"
                    else self._open_tab(service, fingerprint, dry_run=dry_run)
                )
                started.append(service_id)
                if not dry_run:
                    self._record_start(service_id, registry_path, fingerprint)
                    self._wait_ready(service)
        except Exception as error:
            if started and not dry_run:
                print(f"[rollback] stopping services started by this failed operation: {', '.join(started)}")
                try:
                    self.stop(set(started))
                except Exception as rollback_error:
                    raise ServiceManagerError(f"{error}; rollback also failed: {rollback_error}") from error
            raise
        return started

    def _qmd_targets(self) -> list[dict[str, Any]]:
        ready, _, payload = _http_probe("http://127.0.0.1:8795/computation-targets", timeout=4)
        if not ready or not payload:
            return []
        return [row for row in payload.get("targets") or [] if isinstance(row, dict)]

    def _restore_qmd_targets(self, targets: Sequence[Mapping[str, Any]]) -> None:
        restored: list[str] = []
        now = datetime.now(UTC)
        for row in targets:
            expires_text = str(row.get("expires_at") or "")
            ttl: int | None = None
            if expires_text:
                with contextlib.suppress(ValueError):
                    expires = datetime.fromisoformat(expires_text.replace("Z", "+00:00"))
                    ttl = max(1, int((expires - now).total_seconds()))
            payload = {
                key: row[key]
                for key in (
                    "target_id", "owner", "scope", "tickers", "capabilities", "timeframes",
                    "parameter_hash", "anchor", "source_revision", "correlation_id", "causation_id",
                )
                if key in row
            }
            payload["ttl_seconds"] = ttl
            _http_json_request("PUT", "http://127.0.0.1:8795/computation-targets", payload, timeout=5)
            restored.append(str(row.get("target_id") or ""))
        if not restored:
            return
        snapshot = _http_json_request("GET", "http://127.0.0.1:8795/computation-targets", None, timeout=5)
        active = {str(row.get("target_id") or "") for row in snapshot.get("targets") or [] if isinstance(row, dict)}
        missing = sorted(set(restored) - active)
        if missing:
            raise ServiceManagerError(f"QMD Live restarted but computation targets were not restored: {missing}")
        print(f"[restore]  QMD Live computation targets: {len(restored)}")

    def stop(self, selected: set[str], *, dry_run: bool = False) -> list[str]:
        ordered = list(reversed(_topological_order(self.services, selected)))
        if not dry_run:
            self.reconcile_dead_registries(ordered)
        initial = self.statuses(ordered)
        foreign = [row for row in initial.values() if row.state == "foreign"]
        if foreign:
            detail = ", ".join(f"{row.service_id}:{row.port}" for row in foreign)
            raise ServiceManagerError(f"stop refuses foreign listeners before mutation ({detail})")
        stopped: list[str] = []
        for service_id in ordered:
            service = self.services[service_id]
            current = initial[service_id]
            if not current.owned:
                print(f"[preserve] {service_id} is already stopped")
                continue
            print(f"[stop]     {service_id}" + (" (plan)" if dry_run else ""))
            if not dry_run:
                if service.launch_kind == "qmd_live":
                    _run_script(
                        "stop_qmd_live_gateway.ps1",
                        [
                            "-QmdLiveServiceRuntimeRoot", str(self.qmd_runtime_root),
                            "-GracefulTimeoutSeconds", str(service.graceful_timeout_seconds),
                            "-PythonExe", str(self.options.python),
                        ],
                    )
                else:
                    _run_script(
                        "stop_workspace_services.ps1",
                        [
                            "-WorkspaceRuntimeRoot", str(self.runtime_root),
                            "-OnlyServiceRole", service.role,
                            "-GracefulTimeoutSeconds", str(service.graceful_timeout_seconds),
                            "-PythonExe", str(self.options.python),
                        ],
                    )
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline and _port_open(service.port):
                    time.sleep(0.2)
                if _port_open(service.port):
                    raise ServiceManagerError(f"{service_id} remains listening after its managed stop")
            stopped.append(service_id)
        return stopped

    def restart(self, selected: set[str], *, dry_run: bool = False, start_missing: bool = False) -> None:
        if not selected:
            print("[complete] No services match the target.")
            return
        affected = _dependent_closure(self.services, selected)
        dependent_statuses = self.statuses(affected - selected)
        running_dependents = {key for key, row in dependent_statuses.items() if row.owned}
        selected |= running_dependents
        if running_dependents:
            print(f"[coordinate] restarting active dependents: {', '.join(sorted(running_dependents))}")
        qmd_targets = self._qmd_targets() if "qmd-live" in selected and not dry_run else []
        before = self.statuses(selected)
        restartable = {key for key, row in before.items() if row.owned}
        missing = selected - restartable
        self.stop(restartable, dry_run=dry_run)
        to_start = restartable | (missing if start_missing else set())
        if missing and not start_missing:
            print(f"[preserve] stopped services were not started: {', '.join(sorted(missing))}; use --start-missing")
        if dry_run:
            closure = _dependency_closure(self.services, to_start)
            for service_id in _topological_order(self.services, closure):
                if service_id in to_start:
                    print(f"[start]    {service_id} (plan)")
                else:
                    current = self.status(service_id)
                    if current.owned or current.listening:
                        print(f"[preserve] {service_id}")
                    else:
                        print(f"[start]    {service_id} (plan)")
            return
        if to_start:
            self.start(to_start, dry_run=False)
        if qmd_targets and "qmd-live" in to_start:
            self._restore_qmd_targets(qmd_targets)


def _ps_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _run_script(name: str, arguments: Sequence[str]) -> None:
    completed = subprocess.run(
        [
            _powershell(), "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(SCRIPTS / name), *arguments,
        ],
        cwd=REPO_ROOT,
        check=False,
    )
    if completed.returncode:
        raise ServiceManagerError(f"{name} failed with exit code {completed.returncode}")


def _validate_powershell(command: str, label: str) -> None:
    encoded = base64.b64encode(command.encode("utf-16-le")).decode("ascii")
    validator = (
        "$text=[Text.Encoding]::Unicode.GetString([Convert]::FromBase64String($env:QW_SERVICE_COMMAND_B64));"
        "$tokens=$null;$errors=$null;"
        "[void][Management.Automation.Language.Parser]::ParseInput($text,[ref]$tokens,[ref]$errors);"
        "if($errors.Count){$errors|ForEach-Object{$_.Message};exit 1}"
    )
    completed = subprocess.run(
        [_powershell(), "-NoLogo", "-NoProfile", "-Command", validator],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "QW_SERVICE_COMMAND_B64": encoded},
    )
    if completed.returncode:
        detail = (completed.stderr or completed.stdout or "unknown parse error").strip()
        raise ServiceManagerError(f"generated PowerShell for {label} is invalid: {detail}")


def _http_json_request(method: str, url: str, payload: Mapping[str, Any] | None, *, timeout: float) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(url, data=data, headers={"Content-Type": "application/json"}, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
        raise ServiceManagerError(f"{method} {url} failed: {error}") from error
    if not isinstance(result, dict):
        raise ServiceManagerError(f"{method} {url} returned a non-object response")
    return result


def _resolve_python(requested: str) -> Path:
    candidates = [Path(requested)] if requested else []
    if os.environ.get("CONDA_PREFIX"):
        candidates.append(Path(os.environ["CONDA_PREFIX"]) / "python.exe")
    candidates.extend(
        [
            Path.home() / "miniconda3" / "envs" / "ml4t" / "python.exe",
            Path.home() / "anaconda3" / "envs" / "ml4t" / "python.exe",
            Path(sys.executable),
        ]
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise ServiceManagerError("Python was not found; pass --python-exe")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Start, stop, restart, plan, or inspect repository services by profile, service, or dynamic selector."
    )
    parser.add_argument("action", choices=("start", "stop", "restart", "status", "groups", "validate"))
    parser.add_argument("target", nargs="?", default="all")
    parser.add_argument("--within", default="all", help="Limit a dynamic selector to one static profile.")
    parser.add_argument("--plan", action="store_true", help="Print lifecycle actions without changing processes.")
    parser.add_argument("--start-missing", action="store_true", help="When restarting, also start selected stopped services.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable status JSON.")
    parser.add_argument("--runtime-root", type=Path, default=DEFAULT_RUNTIME_ROOT)
    parser.add_argument("--python-exe", default="")
    parser.add_argument("--terminal-target", choices=("named", "current"), default="named")
    parser.add_argument("--terminal-window", default="quant-research-workbench-services")
    parser.add_argument("--qmd-live-host-role", choices=("Laptop", "Workstation"), default="Laptop")
    parser.add_argument("--ibkr-account", default="paper")
    parser.add_argument("--bar-gpt-release-manifest", type=Path, default=DEFAULT_BAR_GPT_MANIFEST)
    parser.add_argument("--text-intelligence-release-manifest", type=Path, default=DEFAULT_TEXT_INTELLIGENCE_MANIFEST)
    parser.add_argument("--timeout-seconds", type=int, default=600)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    values = list(sys.argv[1:] if argv is None else argv)
    if values and values[0].lower() == "plan":
        if len(values) < 2 or values[1].lower() not in {"start", "stop", "restart"}:
            print("[failed] plan requires start, stop, or restart", file=sys.stderr)
            return 2
        values = [values[1], *values[2:], "--plan"]
    args = _parser().parse_args(values)
    try:
        services, profiles = _load_catalog()
        options = ManagerOptions(
            runtime_root=args.runtime_root,
            python=_resolve_python(args.python_exe),
            terminal_window=args.terminal_window,
            terminal_target=args.terminal_target,
            qmd_live_host_role=args.qmd_live_host_role,
            ibkr_account=args.ibkr_account,
            bar_gpt_release_manifest=args.bar_gpt_release_manifest.expanduser().resolve(),
            text_intelligence_release_manifest=args.text_intelligence_release_manifest.expanduser().resolve(),
            timeout_seconds=max(1, int(args.timeout_seconds)),
        )
        manager = ServiceManager(services, profiles, options)
        if args.action == "groups":
            manager.print_groups()
            return 0
        selected = manager.resolve_target(args.target, within=args.within)
        if args.action == "validate":
            manager.validate(selected)
            return 0
        if args.action == "status":
            return 0 if manager.print_status(selected, json_output=args.json) else 1
        if args.action == "start" and args.target == "dev":
            raise ServiceManagerError("start dev cannot refresh running stale services; use restart dev")
        lifecycle_context = (
            contextlib.nullcontext()
            if args.plan
            else manager.operation_lock(f"{args.action}:{args.target}")
        )
        with lifecycle_context:
            if args.action == "start":
                manager.start(selected, dry_run=args.plan)
            elif args.action == "stop":
                manager.stop(selected, dry_run=args.plan)
            else:
                manager.restart(selected, dry_run=args.plan, start_missing=args.start_missing)
        print("[complete] " + ("Plan finished." if args.plan else f"{args.action} finished."))
        return 0
    except (ServiceManagerError, ValueError, OSError, json.JSONDecodeError) as error:
        print(f"[failed]  {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
