from __future__ import annotations

from services.gateway_core.dashboard import service_status
from services.gateway_core.errors import classify_exception, error_summary_from_metrics


def test_resolved_error_preserves_authoritative_seen_timestamps() -> None:
    summary = error_summary_from_metrics(
        {
            "last_error": "TimeoutError('provider timed out')",
            "last_error_status": "resolved",
            "last_error_first_seen_at_utc": "2026-08-25T19:18:20Z",
            "last_error_seen_at_utc": "2026-08-25T19:18:25Z",
            "last_error_resolved_at_utc": "2026-08-25T19:22:22Z",
        },
        service="sec_gateway",
    )

    row = summary["latest_resolved_errors"][0]
    assert row["first_seen_utc"] == "2026-08-25T19:18:20Z"
    assert row["last_seen_utc"] == "2026-08-25T19:18:25Z"
    assert row["resolved_at_utc"] == "2026-08-25T19:22:22Z"


def test_resolved_error_uses_last_seen_when_first_seen_is_unavailable() -> None:
    summary = error_summary_from_metrics(
        {
            "last_error": "provider HTTP 500",
            "last_error_status": "resolved",
            "last_error_seen_at_utc": "2026-08-25T13:32:59Z",
            "last_error_resolved_at_utc": "2026-08-25T13:33:02Z",
        },
        service="news_gateway",
    )

    row = summary["latest_resolved_errors"][0]
    assert row["first_seen_utc"] == "2026-08-25T13:32:59Z"
    assert row["last_seen_utc"] == "2026-08-25T13:32:59Z"


def test_windows_connection_reset_repr_is_retryable_and_catching_up() -> None:
    message = "ConnectionResetError(10054, 'An existing connection was forcibly closed by the remote host')"
    record = classify_exception(message, service="sec_gateway", phase="polling", task="runtime")
    assert record.category == "provider_transient"
    assert record.retryable is True

    metrics = {
        "current_phase": "polling",
        "last_error": message,
        "last_error_status": "retrying",
        "last_error_seen_at_utc": "2026-08-25T20:29:11Z",
        "failed_filings": 1,
    }
    summary = error_summary_from_metrics(metrics, service="sec_gateway")
    assert summary["active_error_count"] == 0
    assert summary["retrying_count"] == 1
    assert summary["latest_active_errors"][0]["status"] == "retrying"
    assert service_status(metrics) == "CATCHING_UP"
