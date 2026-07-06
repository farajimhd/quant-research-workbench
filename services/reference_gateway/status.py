"""Standard status snapshot adapter for the reference gateway."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from services.gateway_core.dashboard import build_dashboard_snapshot
from services.reference_gateway.terminal import ReferenceRunRecord


def build_reference_status_snapshot(record: ReferenceRunRecord) -> dict[str, Any]:
    metrics = {
        "current_phase": latest_operation_name(record),
        "current_phase_message": latest_operation_detail(record),
        "status": record.final_status,
        "audit_status": getattr(record.audit, "status", ""),
        "audit_failures": failed_audit_count(record),
        "source_rows_fetched": sum(state.rows or 0 for state in record.source_states),
        "source_statuses": [
            {
                "name": state.source,
                "status": state.status,
                "rows": state.rows,
                "detail": state.note,
                "coverage": state.coverage,
                "targets": state.targets,
            }
            for state in record.source_states
        ],
        "tasks": [
            {
                "name": op.name,
                "status": op.status,
                "rows": op.rows,
                "seconds": op.seconds,
                "message": op.detail,
            }
            for op in record.operations
        ],
        "table_progress": [
            {
                "group": state.group_id,
                "status": state.status,
                "rows": state.rows,
                "tables_present": state.tables_present,
                "tables_total": state.tables_total,
                "latest_update": state.latest_update,
            }
            for state in record.table_states
        ],
        "write_policy_status": "allowed" if record.write_policy.writes_allowed else "blocked",
        "write_policy_reason": record.write_policy.reason,
        "wall_seconds": record.wall_seconds,
    }
    return build_dashboard_snapshot(
        service_name="reference_gateway",
        config=record.config,
        metrics=metrics,
        sources_sinks=metrics["source_statuses"],
        service_specific={
            "write_policy": asdict(record.write_policy),
            "operations": metrics["tasks"],
            "source_states": metrics["source_statuses"],
            "table_states": metrics["table_progress"],
            "audit": record.audit.public_dict() if record.audit is not None else {},
        },
    )


def latest_operation_name(record: ReferenceRunRecord) -> str:
    return record.operations[-1].name if record.operations else "starting"


def latest_operation_detail(record: ReferenceRunRecord) -> str:
    return record.operations[-1].detail if record.operations else ""


def failed_audit_count(record: ReferenceRunRecord) -> int:
    if record.audit is None:
        return 0
    return sum(1 for check in record.audit.checks if check.status != "ok")
