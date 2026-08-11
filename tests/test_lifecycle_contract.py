from __future__ import annotations

import unittest
import json
import tempfile
from pathlib import Path

from src.backend.lifecycle_contract import lifecycle_projection, mode_adapter_contract
from src.data_provider.jobs import attach_job_summary, list_build_jobs


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
        self.assertEqual(lifecycle["schema_version"], 2)
        self.assertEqual(lifecycle["mode"], "offline")
        self.assertEqual(lifecycle["adapters"]["execution"], "none")
        self.assertTrue(
            next(row for row in lifecycle["commands"] if row["command"] == "resume")[
                "enabled"
            ]
        )

    def test_modes_publish_explicit_clock_source_and_execution_adapters(self) -> None:
        paper = mode_adapter_contract("paper")
        replay = mode_adapter_contract("replay")
        debug = mode_adapter_contract("backtest_debug")

        self.assertEqual(paper["clock"], "wall_exchange_clock")
        self.assertEqual(paper["execution"], "ibkr_cpapi_paper")
        self.assertEqual(replay["observation_source"], "qmd_history")
        self.assertEqual(debug["observation_source"], "content_hashed_fixture")
        with self.assertRaisesRegex(ValueError, "Unsupported lifecycle mode"):
            mode_adapter_contract("mystery")

    def test_market_data_job_listing_is_bounded_before_payload_reads(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            jobs_root = Path(directory) / "jobs"
            for index in range(3):
                path = jobs_root / f"job-{index}"
                path.mkdir(parents=True)
                (path / "job.json").write_text(
                    json.dumps(
                        {
                            "job_id": f"job-{index}",
                            "status": "complete",
                            "created_at": f"2026-08-11T10:0{index}:00Z",
                            "updated_at": f"2026-08-11T10:0{index}:00Z",
                            "request": {"processed_root": directory},
                        }
                    ),
                    encoding="utf-8",
                )

            rows = list_build_jobs(Path(directory), limit=2)

        self.assertEqual(len(rows), 2)
        with self.assertRaisesRegex(ValueError, "between 1 and 500"):
            list_build_jobs(Path(directory), limit=0)


if __name__ == "__main__":
    unittest.main()
