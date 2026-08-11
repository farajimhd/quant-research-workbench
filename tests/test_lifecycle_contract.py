from __future__ import annotations

import unittest

from src.backend.lifecycle_contract import lifecycle_projection
from src.data_provider.jobs import attach_job_summary


class LifecycleContractTests(unittest.TestCase):
    def test_historical_resume_requires_restart_safe_checkpoint(self) -> None:
        blocked = lifecycle_projection(
            resource_type="historical_trading_run",
            resource_id="run-1",
            status="stopped",
            checkpoint={"resume_supported": False},
            supported_commands=("resume",),
            authority="historical_run_controller",
        )
        ready = lifecycle_projection(
            resource_type="historical_trading_run",
            resource_id="run-1",
            status="stopped",
            checkpoint={"resume_supported": True},
            supported_commands=("resume",),
            authority="historical_run_controller",
        )

        self.assertFalse(blocked["commands"][0]["enabled"])
        self.assertTrue(ready["commands"][0]["enabled"])
        self.assertTrue(ready["terminal"])

    def test_market_data_job_exposes_same_lifecycle_shape(self) -> None:
        payload = attach_job_summary(
            {
                "job_id": "job-1",
                "status": "paused",
                "created_at": "2026-08-11T10:00:00Z",
                "updated_at": "2026-08-11T10:01:00Z",
                "request": {"processed_root": "", "resume_stage": "bars"},
            },
            events=[],
        )

        lifecycle = payload["lifecycle"]
        self.assertEqual(lifecycle["state"], "paused")
        self.assertEqual(lifecycle["resource_type"], "market_data_build")
        self.assertTrue(
            next(row for row in lifecycle["commands"] if row["command"] == "resume")[
                "enabled"
            ]
        )


if __name__ == "__main__":
    unittest.main()
