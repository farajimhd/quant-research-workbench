from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from research.bar_gpt.v3.compare_full_training_checkpoints import (
    CheckpointSelection,
    _verify_manifest,
    evaluation_command,
    select_checkpoints,
)


def _record(samples: int, close: float, range_mae: float, mcc: float) -> dict:
    name = f"checkpoint_global_validation_origins_{samples:012d}.pt"
    return {
        "samples_seen": samples,
        "checkpoint": str(Path("D:/runtime/checkpoints") / name),
        "checkpoint_sha256": f"sha-{samples}",
        "promotion_quality": {
            "trade_close_mae_bps": close,
            "trade_range_mae_bps": range_mae,
            "close_mcc": mcc,
            "trade_calibration": 0.01,
        },
    }


class FullTrainingCheckpointComparisonTests(unittest.TestCase):
    def test_selects_first_two_metric_leaders_and_final_epoch(self) -> None:
        records = [
            _record(500, 2.0, 1.5, 0.1),
            _record(1_000, 1.8, 1.4, 0.2),
            _record(1_500, 1.9, 1.2, 0.15),
            _record(2_000, 2.1, 1.6, 0.12),
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checkpoints = root / "checkpoints"
            checkpoints.mkdir()
            for record in records:
                (checkpoints / Path(record["checkpoint"]).name).write_bytes(b"x")
            (checkpoints / "checkpoint_epoch_0001.pt").write_bytes(b"one")
            final = checkpoints / "checkpoint_epoch_0002.pt"
            final.write_bytes(b"two")
            selected = select_checkpoints(
                run_root=root,
                records=records,
                model_card={"completed_normally": True, "samples_seen": 2_500},
            )
        self.assertEqual(
            [item.role for item in selected],
            ["first_global", "best_trade_close", "best_trade_range", "final_epoch"],
        )
        self.assertEqual([item.samples_seen for item in selected], [500, 1_000, 1_500, 2_500])
        self.assertEqual(selected[-1].checkpoint.name, "checkpoint_epoch_0002.pt")

    def test_manifest_verification_requires_exact_membership_summary_and_hash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            manifest = {
                "panels": {
                    "validation": [
                        {"origins": 11, "ticker": "A", "local_date": "2026-01-02"},
                        {"origins": 13, "ticker": "B", "local_date": "2026-01-02"},
                    ]
                },
                "summaries": {"validation": {"origins": 24, "blocks": 2}},
            }
            from research.bar_gpt.v3.compare_full_training_checkpoints import (
                _canonical_json_hash,
            )

            manifest_hash = _canonical_json_hash(manifest)
            manifest["manifest_hash"] = manifest_hash
            path.write_text(json.dumps(manifest), encoding="utf-8")
            result = _verify_manifest(path, expected_hash=manifest_hash)
        self.assertEqual(result, (manifest_hash, 24, 2))

    def test_evaluation_command_uses_complete_validation_panel(self) -> None:
        selection = CheckpointSelection(
            role="first_global",
            rationale="first",
            checkpoint=Path("D:/run/checkpoint.pt"),
            samples_seen=500,
            recorded_sha256="abc",
        )
        command = evaluation_command(
            selection,
            manifest=Path("D:/manifest.json"),
            shard_root=Path("D:/shards"),
            output_root=Path("D:/output"),
            run_name="first-abc",
            batch_size=8,
            loader_workers=4,
            wandb_project="test",
            wandb_entity="entity",
            wandb_mode="disabled",
        )
        self.assertEqual(command[command.index("--panel") + 1], "validation")
        self.assertNotIn("--target-training-origins", command)
        self.assertNotIn("--max-batches", command)


if __name__ == "__main__":
    unittest.main()
