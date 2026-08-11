from __future__ import annotations

import unittest

from src.backend.app import service_operational_evidence


class ServiceOperationalContractTests(unittest.TestCase):
    def test_service_without_declared_metrics_keeps_uniform_unknown_contract(self) -> None:
        payload = service_operational_evidence(
            "reference",
            snapshot={},
            health={},
            metrics={},
        )
        self.assertEqual(payload["schema_version"], 2)
        self.assertEqual(payload["authority"]["service"], "reference")
        self.assertFalse(payload["authority"]["evidence_present"])
        self.assertEqual(payload["coverage"], {})
        self.assertIsNone(payload["freshness"]["last_event_utc"])
        self.assertIsNone(payload["queues"]["depth"])
        self.assertEqual(payload["checkpoint"], {})
        self.assertFalse(payload["degradation"]["degraded"])
        self.assertFalse(payload["degradation"]["evidence_present"])

    def test_declared_generic_evidence_is_projected_without_service_specific_code(self) -> None:
        payload = service_operational_evidence(
            "news",
            snapshot={
                "checked_at_utc": "2026-08-11T15:00:00Z",
                "coverage": {"status": "ready", "through": "2026-08-11"},
                "queues": {"queue_size": 3, "oldest_age_ms": 25},
                "checkpoint": {"cursor": "news:42"},
                "attention": [{"status": "warning", "message": "slow provider"}],
            },
            health={},
            metrics={"last_event_utc": "2026-08-11T14:59:59Z"},
        )
        self.assertEqual(payload["coverage"]["through"], "2026-08-11")
        self.assertEqual(payload["queues"]["depth"], 3)
        self.assertEqual(payload["checkpoint"]["cursor"], "news:42")
        self.assertEqual(payload["freshness"]["last_event_utc"], "2026-08-11T14:59:59Z")
        self.assertTrue(payload["degradation"]["degraded"])
        self.assertTrue(payload["degradation"]["evidence_present"])

    def test_qmd_lane_cache_and_transition_evidence_remains_projected(self) -> None:
        payload = service_operational_evidence(
            "qmd",
            snapshot={
                "queues": {"queue_drop_total": 2, "build_capacity": 4},
                "service_specific": {
                    "source": "qmd-status-v2",
                    "operational": {
                        "lanes": [
                            {"name": "events", "state": "failed", "pending_rows": 7}
                        ],
                        "recent_recoveries": [{"name": "bars"}],
                    },
                    "cache": {"entries": 9, "hits": 11, "misses": 1},
                },
            },
            health={},
            metrics={"last_event_lag_ms": 14},
        )
        self.assertEqual(payload["queues"]["drop_total"], 2)
        self.assertEqual(payload["queues"]["pending_rows"], 7)
        self.assertEqual(payload["cache"]["entries"], 9)
        self.assertEqual(len(payload["transitions"]["failed_lanes"]), 1)
        self.assertTrue(payload["degradation"]["degraded"])


if __name__ == "__main__":
    unittest.main()
