from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from research.bar_gpt.v1.model_comparison_final_validation import (
    CORRECTED_VALIDATION_CONTRACT,
    _summary_matches,
    evaluation_command,
    resolve_comparison_checkpoints,
)
from research.bar_gpt.v1.run_train_model_comparison import COMPARISON_RUNS


class ModelComparisonFinalValidationTest(unittest.TestCase):
    def _source_run(
        self,
        root: Path,
        *,
        model_size: str,
        stamp: str,
        manifest_path: Path,
        wandb_run_id: str,
    ) -> Path:
        profile = COMPARISON_RUNS[model_size]
        run_name = (
            f"bar-gpt-v1-epoch1-{model_size}-micro{profile.microbatch}-"
            f"accum{profile.accumulation}-bucket{profile.length_bucket_batches}-{stamp}"
        )
        run_root = root / "runs" / run_name
        checkpoint = run_root / "checkpoints" / "checkpoint_latest.pt"
        checkpoint.parent.mkdir(parents=True)
        checkpoint.write_bytes(b"checkpoint")
        (run_root / "run_manifest.json").write_text(json.dumps({
            "model_family": "bar_gpt",
            "version": "v1",
            "args": {"experiment_manifest": str(manifest_path)},
            "wandb": {
                "project": "bar gpt model comparison",
                "entity": "entity",
                "run_id": wandb_run_id,
            },
        }), encoding="utf-8")
        (run_root / "model_card.json").write_text(
            json.dumps({"samples_seen": 100_003_578}), encoding="utf-8"
        )
        return run_root

    def test_resolver_selects_requested_complete_set_and_durable_wandb_ids(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = root / "fixed_panels_v2.json"
            manifest_path.touch()
            for stamp, suffix in (("old", "old"), ("new", "new")):
                for model_size in COMPARISON_RUNS:
                    self._source_run(
                        root,
                        model_size=model_size,
                        stamp=stamp,
                        manifest_path=manifest_path,
                        wandb_run_id=f"{model_size}-{suffix}",
                    )
            resolved = resolve_comparison_checkpoints(
                comparison_root=root,
                manifest_path=manifest_path,
                run_stamp="new",
                model_sizes=("current", "medium", "large"),
            )
        self.assertEqual([item.model_size for item in resolved], ["current", "medium", "large"])
        self.assertEqual([item.wandb_run_id for item in resolved], [
            "current-new", "medium-new", "large-new",
        ])
        self.assertTrue(all(item.training_origins == 100_003_578 for item in resolved))

    def test_evaluation_command_appends_corrected_validation_to_original_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = root / "fixed_panels_v2.json"
            manifest_path.touch()
            self._source_run(
                root,
                model_size="current",
                stamp="fixed",
                manifest_path=manifest_path,
                wandb_run_id="source-run-id",
            )
            item = resolve_comparison_checkpoints(
                comparison_root=root,
                manifest_path=manifest_path,
                run_stamp="fixed",
                model_sizes=("current",),
            )[0]
            command = evaluation_command(
                item,
                manifest_path=manifest_path,
                shard_root=Path("shards"),
                output_root=Path("evaluations"),
                local_run_name="corrected-current",
                workers=12,
                wandb_mode="online",
                wandb_log_step=100_003_579,
            )
        self.assertEqual(command[command.index("--panel") + 1], "validation")
        self.assertEqual(command[command.index("--namespace") + 1], "validation")
        self.assertEqual(command[command.index("--wandb-run-id") + 1], "source-run-id")
        self.assertEqual(command[command.index("--wandb-log-step") + 1], "100003579")
        self.assertIn("--corrected-final-record", command)
        self.assertEqual(
            command[command.index("--evaluation-contract") + 1],
            CORRECTED_VALIDATION_CONTRACT,
        )

    def test_cached_summary_is_bound_to_checkpoint_manifest_and_wandb_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = root / "fixed_panels_v2.json"
            manifest_path.touch()
            self._source_run(
                root,
                model_size="current",
                stamp="fixed",
                manifest_path=manifest_path,
                wandb_run_id="source-run-id",
            )
            item = resolve_comparison_checkpoints(
                comparison_root=root,
                manifest_path=manifest_path,
                run_stamp="fixed",
                model_sizes=("current",),
            )[0]
            summary = {
                "evaluation_contract": CORRECTED_VALIDATION_CONTRACT,
                "corrected_final_record": True,
                "panel": "validation",
                "namespace": "validation",
                "manifest_hash": "manifest-hash",
                "wandb_run_id": item.wandb_run_id,
                "checkpoint": str(item.checkpoint),
                "checkpoint_size": item.checkpoint.stat().st_size,
                "checkpoint_mtime_ns": item.checkpoint.stat().st_mtime_ns,
            }
            self.assertTrue(_summary_matches(summary, item=item, manifest_hash="manifest-hash"))
            summary["wandb_run_id"] = "wrong-run"
            self.assertFalse(_summary_matches(summary, item=item, manifest_hash="manifest-hash"))


if __name__ == "__main__":
    unittest.main()
