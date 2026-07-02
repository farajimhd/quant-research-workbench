from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from services.ibkr_gateway_supervisor.client import IbkrClientPortalClient, HttpResult, account_ids, can_reauthenticate, is_authenticated
from services.ibkr_gateway_supervisor.config import IbkrGatewayConfig
from services.ibkr_gateway_supervisor.event_log import SupervisorEventLog
from services.ibkr_gateway_supervisor.login import run_playwright_login
from services.ibkr_gateway_supervisor.notifications import Notifier
from services.ibkr_gateway_supervisor.terminal import SupervisorTerminal, SupervisorTerminalState, parse_event_time, tickle_next_due


class IbkrGatewaySupervisor:
    def __init__(self, config: IbkrGatewayConfig) -> None:
        self.config = config
        self.client = IbkrClientPortalClient(base_url=config.base_url, timeout_seconds=config.request_timeout_seconds)
        self.notifier = Notifier(config)
        self.event_log = SupervisorEventLog(config)
        self.terminal_state = SupervisorTerminalState(event_log_path=str(self.event_log.event_log_path))
        self.terminal = SupervisorTerminal(config, self.terminal_state) if config.terminal_rich_enabled else None
        self.process: subprocess.Popen[str] | None = None
        self.started_process = False
        self.gateway_listener_pid: int | None = None
        self.auth_failures = 0
        self.reauth_attempts = 0
        self.login_attempts = 0
        self.last_login_attempt_monotonic = 0.0
        self.authenticated = False
        self._stop = False

    def check_once(self) -> int:
        try:
            self.ensure_gateway()
            status = self.client.auth_status()
            self.emit("auth_status", result=self.public_result(status), authenticated=status.ok and is_authenticated(status.payload))
            if status.ok and is_authenticated(status.payload):
                accounts = self.client.accounts()
                ids = account_ids(accounts.payload)
                self.emit(
                    "accounts",
                    result={"ok": accounts.ok, "status_code": accounts.status_code, "error": accounts.error},
                    account_count=len(ids),
                    configured_account_present=bool(self.config.account_id and self.config.account_id in ids),
                )
                tickle, latency_ms = self.call_tickle()
                self.emit("tickle", result=self.public_result(tickle), latency_ms=latency_ms)
                return 0 if tickle.ok else 2
            if status.ok and can_reauthenticate(status.payload):
                reauth = self.client.reauthenticate()
                self.emit("reauthenticate", result=self.public_result(reauth))
                return 0 if reauth.ok else 2
            self.emit("fresh_login_required", status=self.public_result(status))
            return 1
        finally:
            self.stop_started_process()

    def run_forever(self) -> None:
        self.install_signal_handlers()
        self.config.log_root.mkdir(parents=True, exist_ok=True)
        if self.terminal is not None:
            self.terminal.start()
        try:
            self.emit("supervisor_started", config=self.config.public_dict(), message="IBKR supervisor started.")
            self.ensure_gateway()
            self.handle_auth_status()
            next_status = 0.0
            next_tickle = 0.0
            while not self._stop:
                now = time.monotonic()
                if now >= next_status:
                    self.handle_auth_status()
                    next_status = now + self.config.status_seconds
                if self.authenticated and now >= next_tickle:
                    self.handle_tickle()
                    next_tickle = now + self.config.tickle_seconds
                time.sleep(1.0)
        finally:
            self.stop_started_process()
            self.emit("supervisor_stopped", message="IBKR supervisor stopped.")
            if self.terminal is not None:
                self.terminal.stop()

    def ensure_gateway(self) -> None:
        if self.client.is_gateway_reachable():
            self.gateway_listener_pid = listener_pid_for_base_url(self.config.base_url)
            self.emit("gateway_reachable", root_url=self.client.root_url, listener_pid=self.gateway_listener_pid, already_running=True)
            return
        if not self.config.launch_gateway:
            raise RuntimeError(f"IBKR Client Portal Gateway is not reachable at {self.client.root_url}")
        self.validate_launch_paths()
        gateway_config_arg = self.gateway_config_argument()
        command = ["cmd.exe", "/c", str(self.config.run_bat_path), gateway_config_arg]
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        self.config.log_root.mkdir(parents=True, exist_ok=True)
        process_log_path = self.config.log_root / f"ibkr_gateway_process_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}.log"
        process_log = process_log_path.open("a", encoding="utf-8", errors="replace")
        self.process = subprocess.Popen(  # noqa: S603
            command,
            cwd=str(self.config.client_library_path),
            stdout=process_log,
            stderr=subprocess.STDOUT,
            text=True,
            creationflags=creationflags,
        )
        process_log.close()
        self.started_process = True
        self.emit("gateway_process_started", pid=self.process.pid, command=["cmd.exe", "/c", "run.bat", gateway_config_arg], process_log=str(process_log_path))
        deadline = time.monotonic() + self.config.startup_timeout_seconds
        while time.monotonic() < deadline:
            if self.client.is_gateway_reachable():
                self.gateway_listener_pid = listener_pid_for_base_url(self.config.base_url)
                self.emit("gateway_reachable", root_url=self.client.root_url, listener_pid=self.gateway_listener_pid)
                return
            if self.process.poll() is not None:
                tail = tail_text(process_log_path)
                raise RuntimeError(f"IBKR gateway process exited during startup with code {self.process.returncode}. log={process_log_path} tail={tail}")
            time.sleep(1.0)
        raise RuntimeError(f"IBKR gateway did not become reachable within {self.config.startup_timeout_seconds:.1f}s")

    def gateway_config_argument(self) -> str:
        try:
            return str(self.config.gateway_config_path.resolve().relative_to(self.config.client_library_path.resolve()))
        except ValueError:
            return str(self.config.gateway_config_path)

    def validate_launch_paths(self) -> None:
        missing = [
            str(path)
            for path in (self.config.client_library_path, self.config.gateway_config_path, self.config.run_bat_path)
            if not path.exists()
        ]
        if missing:
            raise RuntimeError("Missing IBKR gateway path(s): " + "; ".join(missing))

    def handle_auth_status(self) -> None:
        status = self.client.auth_status()
        self.emit("auth_status", result=self.public_result(status), authenticated=status.ok and is_authenticated(status.payload))
        if status.ok and is_authenticated(status.payload):
            self.authenticated = True
            self.auth_failures = 0
            self.reauth_attempts = 0
            self.login_attempts = 0
            return
        self.authenticated = False
        self.auth_failures += 1
        if status.ok and can_reauthenticate(status.payload) and self.reauth_attempts < self.config.max_reauth_attempts:
            self.reauth_attempts += 1
            reauth = self.client.reauthenticate()
            self.emit("reauthenticate", attempt=self.reauth_attempts, result=self.public_result(reauth))
            if reauth.ok:
                followup = self.client.auth_status()
                self.emit("auth_status_after_reauthenticate", result=self.public_result(followup), authenticated=followup.ok and is_authenticated(followup.payload))
                if followup.ok and is_authenticated(followup.payload):
                    self.authenticated = True
                    self.auth_failures = 0
                    self.reauth_attempts = 0
                    return
            self.attempt_auto_login("reauthenticate_failed")
            return
        if self.attempt_auto_login("fresh_login_required"):
            return
        if self.auth_failures >= self.config.max_auth_failures:
            self.notifier.notify_once(
                "ibkr_login_required",
                "IBKR Client Portal login failed",
                "The IBKR Client Portal Gateway is reachable, but automatic login did not authenticate the session. "
                f"account={self.config.account_key} attempts={self.login_attempts} last_status={self.public_result(status)}",
            )

    def attempt_auto_login(self, reason: str) -> bool:
        if not self.config.auto_login:
            self.emit("auto_login_skipped", reason=reason, auto_login=False)
            return False
        now = time.monotonic()
        if self.login_attempts >= self.config.max_login_attempts:
            self.emit("auto_login_skipped", reason=reason, attempts=self.login_attempts, max_attempts=self.config.max_login_attempts)
            return False
        if self.last_login_attempt_monotonic and now - self.last_login_attempt_monotonic < self.config.login_retry_seconds:
            self.emit(
                "auto_login_waiting",
                reason=reason,
                attempts=self.login_attempts,
                retry_seconds=self.config.login_retry_seconds,
            )
            return False
        self.login_attempts += 1
        self.last_login_attempt_monotonic = now
        self.emit("auto_login_started", reason=reason, attempt=self.login_attempts, account_key=self.config.account_key)
        try:
            authenticated = asyncio.run(run_playwright_login(self.config))
        except Exception as exc:  # noqa: BLE001
            self.emit("auto_login_failed", attempt=self.login_attempts, error=f"{type(exc).__name__}: {exc}")
            if self.login_attempts >= self.config.max_login_attempts:
                self.notifier.notify_once(
                    "ibkr_auto_login_failed",
                    "IBKR Client Portal automatic login failed",
                    f"account={self.config.account_key} attempts={self.login_attempts} error={type(exc).__name__}: {exc}",
                )
            return False
        status = self.client.auth_status()
        self.emit("auth_status_after_auto_login", result=self.public_result(status), authenticated=status.ok and is_authenticated(status.payload))
        self.authenticated = bool(authenticated and status.ok and is_authenticated(status.payload))
        if self.authenticated:
            self.emit("auto_login_completed", attempt=self.login_attempts, account_key=self.config.account_key)
            self.auth_failures = 0
            self.reauth_attempts = 0
            self.login_attempts = 0
            return True
        self.emit("auto_login_unverified", attempt=self.login_attempts, account_key=self.config.account_key)
        return False

    def handle_tickle(self) -> None:
        tickle, latency_ms = self.call_tickle()
        self.emit("tickle", result=self.public_result(tickle), latency_ms=latency_ms)
        if tickle.ok:
            return
        self.notifier.notify_once(
            "ibkr_tickle_failed",
            "IBKR Client Portal tickle failed",
            f"The keepalive call failed for account={self.config.account_key}. result={self.public_result(tickle)}",
        )

    def call_tickle(self) -> tuple[HttpResult, float]:
        started = time.perf_counter()
        tickle = self.client.tickle()
        latency_ms = round((time.perf_counter() - started) * 1000.0, 1)
        return tickle, latency_ms

    def stop_started_process(self) -> None:
        if not self.started_process:
            return
        if self.process is not None and self.process.poll() is None:
            self.emit("gateway_process_stopping", pid=self.process.pid)
            terminate_process_tree(self.process.pid)
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
        if self.gateway_listener_pid is not None:
            self.emit("gateway_listener_stopping", pid=self.gateway_listener_pid)
            terminate_process_tree(self.gateway_listener_pid)
            self.gateway_listener_pid = None

    def install_signal_handlers(self) -> None:
        def request_stop(_signum: int, _frame: object) -> None:
            self._stop = True

        signal.signal(signal.SIGINT, request_stop)
        signal.signal(signal.SIGTERM, request_stop)

    def emit(self, event: str, **payload: Any) -> None:
        row = {
            "ts_utc": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "event": event,
            **sanitize(payload),
        }
        self.update_terminal_state(row)
        self.event_log.write(row)
        self.terminal_state.clickhouse_status = self.event_log.clickhouse_status
        self.terminal_state.clickhouse_error = self.event_log.clickhouse_error
        if self.terminal is not None:
            self.terminal.update()
        else:
            print(plain_event_line(row), flush=True)

    def update_terminal_state(self, row: dict[str, Any]) -> None:
        state = self.terminal_state
        event = str(row.get("event") or "")
        state.updated_at_utc = datetime.now(UTC)
        state.recent_events.append(row)
        state.auth_failures = self.auth_failures
        state.reauth_attempts = self.reauth_attempts
        state.login_attempts = self.login_attempts
        if event == "supervisor_started":
            state.current_operation = "Starting IBKR Client Portal Gateway supervisor"
        elif event == "gateway_process_started":
            state.gateway_status = "starting"
            state.gateway_pid = int(row.get("pid") or 0) or None
            state.current_operation = "Starting local IBKR Client Portal Gateway"
        elif event == "gateway_reachable":
            state.gateway_status = "ready"
            state.listener_pid = int(row.get("listener_pid") or 0) or None
            state.current_operation = "Gateway is reachable"
        elif event in {"auth_status", "auth_status_after_reauthenticate", "auth_status_after_auto_login"}:
            result = row.get("result") if isinstance(row.get("result"), dict) else {}
            state.status_code = int(result.get("status_code") or 0)
            state.auth_status = "authenticated" if row.get("authenticated") else "unauthenticated"
            state.current_operation = "Authenticated" if row.get("authenticated") else "Waiting for authenticated session"
        elif event == "reauthenticate":
            state.current_operation = "Attempting IBKR session reauthentication"
        elif event == "auto_login_started":
            state.login_status = "running"
            state.current_operation = "Running Playwright login automation"
        elif event == "auto_login_completed":
            state.login_status = "ok"
            state.auth_status = "authenticated"
            state.current_operation = "Automatic login completed"
        elif event == "auto_login_failed":
            state.login_status = "failed"
            state.last_error = str(row.get("error") or "Automatic login failed")
            state.alerts.append(state.last_error)
        elif event == "auto_login_waiting":
            state.login_status = "waiting"
            state.current_operation = "Waiting before next automatic login attempt"
        elif event == "fresh_login_required":
            state.auth_status = "unauthenticated"
            state.current_operation = "Fresh login required"
        elif event == "accounts":
            state.account_status = "ready" if row.get("configured_account_present") else "missing"
        elif event == "tickle":
            result = row.get("result") if isinstance(row.get("result"), dict) else {}
            state.last_tickle_at_utc = parse_event_time(row.get("ts_utc"))
            state.next_tickle_due_utc = tickle_next_due(row.get("ts_utc"), self.config)
            state.last_tickle_status_code = int(result.get("status_code") or 0)
            state.last_tickle_latency_ms = float(row.get("latency_ms") or 0.0)
            if result.get("ok"):
                state.keepalive_status = "ok"
                state.tickle_count += 1
                state.tickle_failures = 0
                state.last_tickle_error = ""
                state.current_operation = "Keepalive tickle succeeded"
            else:
                state.keepalive_status = "failed"
                state.tickle_failures += 1
                state.last_tickle_error = str(result.get("error") or "Tickle failed")
                state.last_error = state.last_tickle_error
                state.current_operation = "Keepalive tickle failed"
                state.alerts.append(state.last_error)
        elif event in {"gateway_process_stopping", "gateway_listener_stopping"}:
            state.gateway_status = "stopping"
            state.current_operation = "Stopping supervisor-started gateway process"
        elif event == "supervisor_stopped":
            state.gateway_status = "stopped"
            state.current_operation = "Supervisor stopped"
        if "failed" in event:
            state.last_error = str(row.get("error") or row.get("message") or event)

    def public_result(self, result: HttpResult) -> dict[str, Any]:
        payload = result.payload
        if isinstance(payload, dict):
            payload = {key: value for key, value in payload.items() if "password" not in str(key).lower()}
        return {"ok": result.ok, "status_code": result.status_code, "payload": payload, "error": result.error}


def sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): sanitize(item) for key, item in value.items() if "password" not in str(key).lower()}
    if isinstance(value, (list, tuple)):
        return [sanitize(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def plain_event_line(row: dict[str, Any]) -> str:
    event = str(row.get("event") or "-")
    result = row.get("result") if isinstance(row.get("result"), dict) else {}
    status = event_status_text(row, result)
    parts = [str(row.get("ts_utc") or ""), event, status]
    if row.get("latency_ms"):
        parts.append(f"latency={float(row['latency_ms']):.0f}ms")
    for key in ("message", "reason", "error"):
        if row.get(key):
            parts.append(f"{key}={row[key]}")
            break
    if result.get("error"):
        parts.append(f"error={result['error']}")
    return " ".join(part for part in parts if part)


def event_status_text(row: dict[str, Any], result: dict[str, Any]) -> str:
    if "authenticated" in row:
        return "authenticated" if row.get("authenticated") else "unauthenticated"
    if "ok" in result:
        code = result.get("status_code") or "-"
        return f"ok status={code}" if result.get("ok") else f"failed status={code}"
    return "event"


def tail_text(path: Path, *, max_chars: int = 1_000) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return text[-max_chars:].replace("\r", "").replace("\n", " | ")


def listener_pid_for_base_url(base_url: str) -> int | None:
    try:
        from urllib.parse import urlsplit

        port = urlsplit(base_url).port
    except ValueError:
        port = None
    if port is None:
        return None
    if os.name != "nt":
        return None
    try:
        result = subprocess.run(["netstat", "-ano", "-p", "tcp"], capture_output=True, text=True, timeout=10, check=False)  # noqa: S603,S607
    except Exception:  # noqa: BLE001
        return None
    needle = f":{port}"
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        local_address, state, pid = parts[1], parts[3].upper(), parts[-1]
        if state == "LISTENING" and local_address.endswith(needle):
            try:
                return int(pid)
            except ValueError:
                return None
    return None


def terminate_process_tree(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, text=True, timeout=15, check=False)  # noqa: S603,S607
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return
