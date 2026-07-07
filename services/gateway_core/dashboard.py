from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from typing import Any

from services.gateway_core.errors import error_summary_from_metrics


def build_dashboard_snapshot(
    *,
    service_name: str,
    config: Any,
    metrics: dict[str, Any],
    recent_items: dict[str, Any] | None = None,
    dependencies: list[dict[str, Any]] | None = None,
    sources_sinks: list[dict[str, Any]] | None = None,
    service_specific: dict[str, Any] | None = None,
) -> dict[str, Any]:
    config_public = public_config(config)
    current_phase = str(metrics.get("current_phase") or metrics.get("active_mode") or "running")
    status = service_status(metrics)
    now = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    read_db = read_database(config_public)
    write_db = write_database(config_public)
    return {
        "header": {
            "service": service_name,
            "status": status,
            "bind": config_public.get("bind", ""),
            "mode": config_public.get("operator_mode", config_public.get("mode", "")),
            "run_mode": config_public.get("run_mode", ""),
            "execute": config_public.get("execute", ""),
            "read_database": read_db,
            "write_database": write_db,
            "data_root": str(config_public.get("data_root_win") or config_public.get("data_root") or ""),
            "snapshot_utc": now,
            "market_status": metrics.get("market_status", ""),
            "market_status_source": metrics.get("market_status_source", ""),
        },
        "current_operation": {
            "phase": current_phase,
            "status": status,
            "started_at": metrics.get("current_phase_started_at_utc") or metrics.get("started_at_utc") or "",
            "elapsed": "",
            "message": metrics.get("current_phase_message") or metrics.get("active_detail") or metrics.get("last_worker_message") or "",
            "next_action": metrics.get("next_poll_at_utc") or "",
        },
        "configuration": compact_config(config_public),
        "dependencies": dependencies or preflight_dependencies(metrics),
        "runtime": runtime_summary(metrics),
        "daily_summary": daily_summary(metrics),
        "tasks": task_summary(metrics),
        "task_table_progress": table_progress(metrics),
        "configured_tables": configured_tables(config_public, read_database=read_db, write_database=write_db),
        "queues": queue_summary(metrics),
        "coverage": coverage_summary(metrics),
        "sources_sinks": sources_sinks or [],
        "recent_items": recent_items or {},
        "error_state": error_summary_from_metrics(metrics, service=service_name),
        "warnings_errors": {"last_error": metrics.get("last_error", ""), "market_status_error": metrics.get("market_status_error", "")},
        "service_specific": service_specific or metrics,
    }


def public_config(config: Any) -> dict[str, Any]:
    if hasattr(config, "public_dict"):
        payload = config.public_dict()
    elif is_dataclass(config):
        payload = asdict(config)
    elif isinstance(config, dict):
        payload = dict(config)
    else:
        payload = {}
    return flatten_config(payload)


def flatten_config(payload: dict[str, Any]) -> dict[str, Any]:
    flat = dict(payload)
    for key in ("service", "database", "storage", "pipeline"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            for nested_key, value in nested.items():
                flat.setdefault(nested_key, value)
    return flat


def service_status(metrics: dict[str, Any]) -> str:
    phase = str(metrics.get("current_phase") or "").lower()
    explicit_status = str(metrics.get("status") or "").lower()
    preflight = str(metrics.get("preflight_status") or "").lower()
    if phase == "failed" or preflight == "failed":
        return "FAILED"
    provider_cooldown = float(metrics.get("sec_request_cooldown_remaining_seconds") or metrics.get("provider_cooldown_remaining_seconds") or 0.0)
    provider_cooldown_reason = str(metrics.get("sec_request_cooldown_reason") or metrics.get("provider_cooldown_reason") or "").lower()
    if phase == "provider_cooldown" or provider_cooldown > 0:
        return "DEGRADED" if provider_cooldown_reason in {"sec_http_403", "sec_http_429"} else "CATCHING_UP"
    if phase in {"preflight", "coverage_bootstrap", "gap_planning"}:
        return "PREFLIGHT" if phase == "preflight" else "CATCHING_UP"
    if phase in {"idle", "waiting"}:
        return "IDLE"
    if phase:
        return "RUNNING"
    if explicit_status in {"failed", "blocked"}:
        return explicit_status.upper()
    if explicit_status in {"degraded", "warning"}:
        return "DEGRADED"
    if explicit_status in {"running", "ok", "healthy", "ready"}:
        return "RUNNING"
    return "STARTING"


def compact_config(config: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "bind",
        "execute",
        "is_workstation",
        "read_database",
        "write_database",
        "clickhouse_database",
        "source_database",
        "target_database",
        "market_status_enabled",
        "market_status_refresh_seconds",
        "terminal_refresh_seconds",
    ]
    return {key: config.get(key) for key in keys if key in config}


def preflight_dependencies(metrics: dict[str, Any]) -> list[dict[str, Any]]:
    checks = metrics.get("preflight_checks")
    if isinstance(checks, list):
        return [dict(item) for item in checks if isinstance(item, dict)]
    return []


def runtime_summary(metrics: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "poll_runs",
        "poll_failures",
        "provider_rows",
        "processed_rows",
        "written_rows",
        "skipped_existing",
        "failed_rows",
        "feed_items",
        "processed_filings",
        "written_filings",
        "source_rows_fetched",
        "embedding_rows_written",
        "cycles",
        "last_cycle_seconds",
        "last_poll_at_utc",
    ]
    return {key: metrics.get(key) for key in keys if key in metrics}


def daily_summary(metrics: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "provider_rows",
        "unique_news_rows",
        "written_rows",
        "duplicate_news_rows",
        "feed_items",
        "written_filings",
        "embedding_rows_written",
        "coverage_rows_written",
        "failed_rows",
        "failed_filings",
        "last_poll_at_utc",
        "last_embedding_at_utc",
    ]
    return {key: metrics.get(key) for key in keys if key in metrics}


def task_summary(metrics: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for name, status_key in (
        ("preflight", "preflight_status"),
        ("coverage", "gap_status"),
        ("audit", "audit_status"),
        ("publish", "publish_status"),
        ("model", "model_status"),
    ):
        if status_key in metrics:
            rows.append({"task": name, "status": metrics.get(status_key), "detail": metrics.get(status_key.replace("_status", "_message"), "")})
    return rows


def table_progress(metrics: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    if "gap_fill_total_chunks" in metrics:
        rows.append(
            {
                "task_table": "startup gap fill",
                "operation": "gap_fill",
                "status": metrics.get("gap_status", ""),
                "done": metrics.get("gap_fill_flushed_chunks", 0),
                "total": metrics.get("gap_fill_total_chunks", 0),
                "detail": metrics.get("gap_message", ""),
            }
        )
    if "coverage_interval_count" in metrics:
        rows.append({"task_table": "coverage", "operation": "reconcile", "status": metrics.get("gap_status", ""), "done": metrics.get("coverage_interval_count", 0), "total": "", "detail": metrics.get("gap_message", "")})
    return rows


def configured_tables(config: dict[str, Any], *, read_database: str, write_database: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for key, value in sorted(config.items()):
        if not is_table_config_key(key):
            continue
        if value is None or value == "":
            continue
        table_names = table_names_from_value(value)
        for table_name in table_names:
            database = table_database_for_key(key, read_database=read_database, write_database=write_database)
            identity = (database, table_name)
            if identity in seen:
                continue
            seen.add(identity)
            rows.append(
                {
                    "name": table_name,
                    "status": "configured",
                    "database": database,
                    "config_key": key,
                    "rows": None,
                    "detail": f"{database}.{table_name}",
                }
            )
    return rows


def is_table_config_key(key: str) -> bool:
    text = key.lower()
    if "database" in text or "timetable" in text:
        return False
    return text.endswith("_table") or text.endswith("_tables") or text.endswith("_table_name")


def table_names_from_value(value: Any) -> list[str]:
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def table_database_for_key(key: str, *, read_database: str, write_database: str) -> str:
    lowered = key.lower()
    if any(token in lowered for token in ("source", "read", "context", "live")):
        return read_database or write_database
    return write_database or read_database


def queue_summary(metrics: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for label, depth_key, active_key, done_key, failed_key in (
        ("background publish", "background_queue_size", "background_active_batches", "background_completed_batches", "background_failed_batches"),
        ("SEC live workers", "live_queue_size", "live_active_workers", "live_completed_filings", "live_worker_failures"),
        ("Text embedding", "active_queries", "active_queries", "embedding_rows_written", "last_error"),
    ):
        if depth_key in metrics:
            rows.append(
                {
                    "queue_worker": label,
                    "status": "running",
                    "depth": metrics.get(depth_key),
                    "active": metrics.get(active_key),
                    "done": metrics.get(done_key),
                    "failed": metrics.get(failed_key),
                }
            )
    return rows


def coverage_summary(metrics: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": metrics.get("gap_status", ""),
        "message": metrics.get("gap_message", ""),
        "manual_gap_fill_command": metrics.get("manual_gap_fill_command", ""),
        "coverage_interval_count": metrics.get("coverage_interval_count", ""),
        "active_window_utc": metrics.get("active_window_utc", ""),
    }


def read_database(config: dict[str, Any]) -> str:
    return str(config.get("read_database") or config.get("clickhouse_read_database") or config.get("source_database") or config.get("clickhouse_database") or "")


def write_database(config: dict[str, Any]) -> str:
    return str(config.get("write_database") or config.get("clickhouse_write_database") or config.get("target_database") or config.get("clickhouse_database") or "")
