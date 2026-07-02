from __future__ import annotations

import json
import signal
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from services.ibkr_gateway_supervisor.client import IbkrClientPortalClient, HttpResult, account_ids, can_reauthenticate, is_authenticated
from services.ibkr_gateway_supervisor.config import IbkrGatewayConfig
from services.ibkr_gateway_supervisor.notifications import Notifier


class IbkrGatewaySupervisor:
    def __init__(self, config: IbkrGatewayConfig) -> None:
        self.config = config
        self.client = IbkrClientPortalClient(base_url=config.base_url, timeout_seconds=config.request_timeout_seconds)
        self.notifier = Notifier(config)
        self.process: subprocess.Popen[str] | None = None
        self.started_process = False
        self.auth_failures = 0
        self.reauth_attempts = 0
        self._stop = False

    def check_once(self) -> int:
        self.ensure_gateway()
        status = self.client.auth_status()
        self.emit("auth_status", result=self.public_result(status))
        if status.ok and is_authenticated(status.payload):
            accounts = self.client.accounts()
            ids = account_ids(accounts.payload)
            self.emit(
                "accounts",
                result={"ok": accounts.ok, "status_code": accounts.status_code, "error": accounts.error},
                account_count=len(ids),
                configured_account_present=bool(self.config.account_id and self.config.account_id in ids),
            )
            tickle = self.client.tickle()
            self.emit("tickle", result=self.public_result(tickle))
            return 0 if tickle.ok else 2
        if status.ok and can_reauthenticate(status.payload):
            reauth = self.client.reauthenticate()
            self.emit("reauthenticate", result=self.public_result(reauth))
            return 0 if reauth.ok else 2
        self.emit("fresh_login_required", status=self.public_result(status))
        return 1

    def run_forever(self) -> None:
        self.install_signal_handlers()
        self.config.log_root.mkdir(parents=True, exist_ok=True)
        self.emit("supervisor_started", config=self.config.public_dict())
        try:
            self.ensure_gateway()
            next_status = 0.0
            next_tickle = 0.0
            while not self._stop:
                now = time.monotonic()
                if now >= next_status:
                    self.handle_auth_status()
                    next_status = now + self.config.status_seconds
                if now >= next_tickle:
                    self.handle_tickle()
                    next_tickle = now + self.config.tickle_seconds
                time.sleep(1.0)
        finally:
            self.stop_started_process()
            self.emit("supervisor_stopped")

    def ensure_gateway(self) -> None:
        if self.client.is_gateway_reachable():
            return
        if not self.config.launch_gateway:
            raise RuntimeError(f"IBKR Client Portal Gateway is not reachable at {self.client.root_url}")
        self.validate_launch_paths()
        command = ["cmd.exe", "/c", str(self.config.run_bat_path), str(self.config.gateway_config_path)]
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        self.process = subprocess.Popen(  # noqa: S603
            command,
            cwd=str(self.config.client_library_path),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            creationflags=creationflags,
        )
        self.started_process = True
        self.emit("gateway_process_started", pid=self.process.pid, command=["cmd.exe", "/c", "run.bat", str(self.config.gateway_config_path)])
        deadline = time.monotonic() + self.config.startup_timeout_seconds
        while time.monotonic() < deadline:
            if self.client.is_gateway_reachable():
                self.emit("gateway_reachable", root_url=self.client.root_url)
                return
            if self.process.poll() is not None:
                raise RuntimeError(f"IBKR gateway process exited during startup with code {self.process.returncode}")
            time.sleep(1.0)
        raise RuntimeError(f"IBKR gateway did not become reachable within {self.config.startup_timeout_seconds:.1f}s")

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
        self.emit("auth_status", result=self.public_result(status))
        if status.ok and is_authenticated(status.payload):
            self.auth_failures = 0
            self.reauth_attempts = 0
            return
        self.auth_failures += 1
        if status.ok and can_reauthenticate(status.payload) and self.reauth_attempts < self.config.max_reauth_attempts:
            self.reauth_attempts += 1
            reauth = self.client.reauthenticate()
            self.emit("reauthenticate", attempt=self.reauth_attempts, result=self.public_result(reauth))
            return
        if self.auth_failures >= self.config.max_auth_failures:
            self.notifier.notify_once(
                "ibkr_login_required",
                "IBKR Client Portal login required",
                "The IBKR Client Portal Gateway is reachable, but the session is not authenticated. "
                f"Run the Playwright login helper for account={self.config.account_key}. Last status={self.public_result(status)}",
            )

    def handle_tickle(self) -> None:
        tickle = self.client.tickle()
        self.emit("tickle", result=self.public_result(tickle))
        if tickle.ok:
            return
        self.notifier.notify_once(
            "ibkr_tickle_failed",
            "IBKR Client Portal tickle failed",
            f"The keepalive call failed for account={self.config.account_key}. result={self.public_result(tickle)}",
        )

    def stop_started_process(self) -> None:
        if not self.started_process or self.process is None or self.process.poll() is not None:
            return
        self.emit("gateway_process_stopping", pid=self.process.pid)
        try:
            if hasattr(signal, "CTRL_BREAK_EVENT"):
                self.process.send_signal(signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
            else:
                self.process.terminate()
            self.process.wait(timeout=10)
        except Exception:  # noqa: BLE001
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()

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
        print(json.dumps(row, sort_keys=True, default=str), flush=True)
        try:
            self.config.log_root.mkdir(parents=True, exist_ok=True)
            path = self.config.log_root / f"ibkr_gateway_supervisor_{datetime.now(UTC).strftime('%Y%m%d')}.jsonl"
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, sort_keys=True, default=str) + "\n")
        except Exception:
            pass

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
