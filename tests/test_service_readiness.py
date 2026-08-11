from __future__ import annotations

import unittest

from src.backend.app import service_operational_evidence, service_readiness_payload


def readiness(
    *,
    service_id: str = "qmd",
    online: bool = True,
    service_status: str = "RUNNING",
    snapshot: dict | None = None,
    health: dict | None = None,
    metrics: dict | None = None,
    database_tables: dict | None = None,
    errors: dict | None = None,
) -> dict:
    return service_readiness_payload(
        service_id,
        online=online,
        service_status=service_status,
        snapshot=snapshot or {},
        health=health or {},
        metrics=metrics or {},
        database_tables=database_tables or {"rows": [], "error": ""},
        errors=errors or {},
    )


class ServiceReadinessTests(unittest.TestCase):
    def test_offline_does_not_claim_dependency_or_data_readiness(self) -> None:
        payload = readiness(online=False, errors={"snapshot": "connection refused"})

        self.assertEqual(payload["liveness"]["status"], "offline")
        self.assertEqual(payload["dependencies"]["status"], "blocked")
        self.assertEqual(payload["data"]["status"], "unknown")
        self.assertEqual(payload["execution"]["status"], "not_applicable")

    def test_structured_healthy_contract_and_tables_are_independently_ready(self) -> None:
        payload = readiness(
            snapshot={"header": {"status": "RUNNING"}, "attention": [], "error_state": {"active": False}},
            database_tables={"rows": [{"database": "q_live", "table": "bars", "status": "ok"}], "error": ""},
        )

        self.assertEqual(payload["liveness"]["status"], "ready")
        self.assertEqual(payload["dependencies"]["status"], "ready")
        self.assertEqual(payload["data"]["status"], "ready")

    def test_declared_attention_and_missing_table_degrade_their_dimensions(self) -> None:
        payload = readiness(
            snapshot={"attention": [{"status": "action_required", "message": "archive handoff blocked"}]},
            database_tables={"rows": [{"database": "q_live", "table": "bars", "status": "missing"}], "error": ""},
        )

        self.assertEqual(payload["dependencies"]["status"], "degraded")
        self.assertIn("archive handoff", payload["dependencies"]["evidence"])
        self.assertEqual(payload["data"]["status"], "degraded")

    def test_ibkr_execution_requires_explicit_auth_and_account_evidence(self) -> None:
        unknown = readiness(service_id="ibkr")
        ready = readiness(service_id="ibkr", metrics={"auth_status": "authenticated", "account_status": "matched"})

        self.assertEqual(unknown["execution"]["status"], "unknown")
        self.assertEqual(ready["execution"]["status"], "ready")

    def test_qmd_operational_evidence_preserves_lag_queue_and_recovery(self) -> None:
        payload = service_operational_evidence(
            "qmd",
            snapshot={
                "runtime": {"last_event_lag_ms": 125, "last_event_ts": "2026-08-10T16:00:00Z"},
                "queues": {"queue_drop_total": 2},
                "service_specific": {
                    "operational": {
                        "lanes": [
                            {"key": "compact_events", "enabled": True, "state": "healthy", "pending_rows": 3},
                            {"key": "intraday_bars", "enabled": True, "state": "failed", "pending_rows": 5},
                        ],
                        "recent_recoveries": [{"area": "massive_feed"}],
                    }
                },
            },
            health={},
            metrics={},
        )

        self.assertEqual(payload["freshness"]["last_event_lag_ms"], 125)
        self.assertEqual(payload["queues"]["drop_total"], 2)
        self.assertEqual(payload["queues"]["pending_rows"], 8)
        self.assertEqual(payload["transitions"]["failed_lanes"][0]["key"], "intraday_bars")
        self.assertEqual(payload["transitions"]["recent_recoveries"][0]["area"], "massive_feed")

    def test_qmd_history_operational_evidence_uses_declared_cache_contract(self) -> None:
        payload = service_operational_evidence(
            "qmd-history",
            snapshot={
                "runtime": {"cache_entries": 9, "cache_hit_rate": 0.75, "active_builds": 2},
                "coverage": {"status": "ready", "archive_session_date": "2026-08-08"},
                "queues": {"build_capacity": 4},
                "service_specific": {
                    "cache": {
                        "requirements": [
                            {
                                "requirement_id": "offline-17",
                                "scope": "offline",
                                "ticker": "AAPL",
                            }
                        ]
                    }
                },
            },
            health={},
            metrics={},
        )

        self.assertEqual(payload["coverage"]["archive_session_date"], "2026-08-08")
        self.assertEqual(payload["cache"]["entries"], 9)
        self.assertEqual(payload["cache"]["hit_rate"], 0.75)
        self.assertEqual(payload["queues"]["active_builds"], 2)
        self.assertEqual(payload["queues"]["build_capacity"], 4)
        self.assertEqual(payload["cache"]["requirements"][0]["requirement_id"], "offline-17")


if __name__ == "__main__":
    unittest.main()
