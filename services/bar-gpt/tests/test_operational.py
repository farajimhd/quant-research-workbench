from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from bar_gpt_service.config import ServiceConfig
from bar_gpt_service.contracts import OperationalConfigurationUpdate
from bar_gpt_service.models import release_summary
from bar_gpt_service.operational import configuration_snapshot, update_configuration


class OperationalConfigurationTests(unittest.TestCase):
    def test_release_summary_exposes_artifact_identity_without_checkpoint_path(self) -> None:
        release = SimpleNamespace(
            config=SimpleNamespace(
                model_id="bar_gpt_v2",
                version="v2",
                role="champion",
                checkpoint=Path(r"D:\private\checkpoints\approved.pt"),
            ),
            model=SimpleNamespace(parameters=lambda: []),
            checkpoint_hash="checkpoint-hash",
            contract_hash="contract-hash",
            device="cpu",
            dtype="float32",
            data_config=SimpleNamespace(horizons_us=(5_000_000,), attention_window_by_name={"1s": 64}),
        )
        summary = release_summary(release)
        self.assertEqual(summary["artifact_name"], "approved.pt")
        self.assertNotIn("checkpoint", summary)

    def test_update_persists_only_registered_release_ids_and_requires_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checkpoint = root / "checkpoint.pt"
            checkpoint.touch()
            environment = {
                "BAR_GPT_RUNTIME_ROOT": str(root / "runtime"),
                "BAR_GPT_V2_CHECKPOINT": str(checkpoint),
                "BAR_GPT_DEVICE": "cpu",
            }
            with patch.dict(os.environ, environment, clear=True):
                config = ServiceConfig.from_env()
            runtime = SimpleNamespace(
                config=config,
                releases={},
                caches={"live": object()},
                active_scopes=lambda: {},
                active_tickers=lambda: set(),
                health=lambda: {"status": "blocked", "queue": {"active": 0, "capacity": 4096}},
            )
            request = OperationalConfigurationUpdate(
                expected_revision=0,
                selected_release_ids=["bar_gpt_v2"],
                release_roles={"bar_gpt_v2": "champion"},
                device="cuda",
                dtype="bfloat16",
                maximum_tickers=123,
                maximum_batch_size=32,
                maximum_batch_delay_ms=10,
                queue_capacity=1024,
                warm_concurrency=3,
                minimum_warm_1s_bars=64,
                prediction_history=2048,
                connect_qmd=True,
            )
            updated = update_configuration(runtime, request)
            self.assertEqual(updated["revision"], 1)
            self.assertTrue(updated["restart_required"])
            self.assertEqual(updated["desired"]["maximum_tickers"], 123)
            self.assertNotIn("checkpoint", updated["releases"][0])
            with patch.dict(os.environ, environment, clear=True):
                restarted = ServiceConfig.from_env()
            self.assertEqual(restarted.maximum_tickers, 123)
            self.assertEqual(restarted.device, "cuda")
            self.assertEqual(restarted.releases[0].role, "champion")

    def test_update_rejects_unknown_release_and_stale_revision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checkpoint = root / "checkpoint.pt"
            checkpoint.touch()
            with patch.dict(os.environ, {
                "BAR_GPT_RUNTIME_ROOT": str(root / "runtime"),
                "BAR_GPT_V2_CHECKPOINT": str(checkpoint),
            }, clear=True):
                config = ServiceConfig.from_env()
            runtime = SimpleNamespace(
                config=config, releases={}, caches={}, active_scopes=lambda: {}, active_tickers=lambda: set(),
                health=lambda: {"status": "blocked", "queue": {}},
            )
            base = dict(
                expected_revision=0, release_roles={}, device="auto", dtype="bfloat16",
                maximum_tickers=500, maximum_batch_size=64, maximum_batch_delay_ms=20,
                queue_capacity=4096, warm_concurrency=4, minimum_warm_1s_bars=64,
                prediction_history=2048, connect_qmd=True,
            )
            with self.assertRaisesRegex(ValueError, "unknown promoted"):
                update_configuration(runtime, OperationalConfigurationUpdate(
                    **base, selected_release_ids=["missing"]
                ))
            update_configuration(runtime, OperationalConfigurationUpdate(
                **base, selected_release_ids=["bar_gpt_v2"]
            ))
            with self.assertRaisesRegex(ValueError, "revision changed"):
                update_configuration(runtime, OperationalConfigurationUpdate(
                    **base, selected_release_ids=["bar_gpt_v2"]
                ))
            self.assertEqual(configuration_snapshot(runtime)["revision"], 1)


if __name__ == "__main__":
    unittest.main()
