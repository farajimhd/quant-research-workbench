from __future__ import annotations

from types import SimpleNamespace

from services.ibkr_gateway_supervisor.main import SupervisorService


def test_operational_supervisor_reports_running_monitoring_status() -> None:
    state = SimpleNamespace(
        last_error="",
        gateway_status="ready",
        auth_status="authenticated",
        keepalive_status="ok",
        account_status="unknown",
        tickle_count=10,
        tickle_failures=0,
        auth_failures=0,
        reauth_attempts=0,
        login_attempts=0,
        event_log_path="events.jsonl",
        clickhouse_status="ready",
        clickhouse_error="",
    )
    service = SupervisorService.__new__(SupervisorService)
    service.supervisor = SimpleNamespace(terminal_state=state)
    service.thread = SimpleNamespace(is_alive=lambda: True)
    service.last_error = ""

    metrics = service.metrics()

    assert metrics["status"] == "running"
    assert metrics["current_phase"] == "monitoring"
    assert metrics["supervisor_thread_alive"] is True


def test_non_operational_supervisor_remains_starting() -> None:
    state = SimpleNamespace(
        last_error="",
        gateway_status="ready",
        auth_status="unknown",
        keepalive_status="starting",
        account_status="unknown",
        tickle_count=0,
        tickle_failures=0,
        auth_failures=0,
        reauth_attempts=0,
        login_attempts=0,
        event_log_path="events.jsonl",
        clickhouse_status="ready",
        clickhouse_error="",
    )
    service = SupervisorService.__new__(SupervisorService)
    service.supervisor = SimpleNamespace(terminal_state=state)
    service.thread = SimpleNamespace(is_alive=lambda: True)
    service.last_error = ""

    assert service.metrics()["status"] == "starting"
