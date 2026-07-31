from __future__ import annotations

import unittest
from unittest import mock

from . import main


class TextIntelligenceHealthTests(unittest.TestCase):
    def test_active_deterministic_error_degrades_service(self) -> None:
        deterministic = {
            "deterministic_runtime_status": "running",
            "deterministic_queue_size": 0,
            "deterministic_active_workers": 0,
            "deterministic_failed": 2,
            "deterministic_last_error": "TimeoutError: timed out",
            "deterministic_last_error_status": "active",
            "deterministic_reconcile_last_error": "TimeoutError: timed out",
            "deterministic_reconcile_error_status": "active",
        }
        with mock.patch.object(
            main.scoped_runtime, "snapshot_metrics", return_value=deterministic
        ):
            metrics = main._snapshot_metrics()

        self.assertEqual(metrics["status"], "degraded")
        self.assertEqual(metrics["current_phase"], "degraded")
        self.assertEqual(metrics["last_error_status"], "active")
        self.assertEqual(metrics["errors"], 1)

    def test_resolved_failure_count_does_not_keep_service_degraded(self) -> None:
        deterministic = {
            "deterministic_runtime_status": "running",
            "deterministic_queue_size": 0,
            "deterministic_active_workers": 0,
            "deterministic_failed": 2,
            "deterministic_last_error": "TimeoutError: timed out",
            "deterministic_last_error_status": "resolved",
            "deterministic_reconcile_last_error": "",
            "deterministic_reconcile_error_status": "resolved",
        }
        with mock.patch.object(
            main.scoped_runtime, "snapshot_metrics", return_value=deterministic
        ):
            metrics = main._snapshot_metrics()

        self.assertEqual(metrics["status"], "running")
        self.assertEqual(metrics["current_phase"], "idle")
        self.assertEqual(metrics["last_error_status"], "resolved")
        self.assertEqual(metrics["errors"], 0)


if __name__ == "__main__":
    unittest.main()
