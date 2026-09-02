from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from research.bar_gpt.v3.compare_full_training_checkpoints import (
    CheckpointSelection,
    _verify_manifest_and_population,
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

    def test_manifest_verification_loads_entire_held_out_index(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "manifest.json"
            manifest = {
                "contract_version": 9,
                "ranges": {"held_out": ["2026-01-01", "2026-08-01"]},
                "cohorts": {"held_out_available_tickers": ["A", "B"]},
                "panels": {
                    name: []
                    for name in ("monitor", "monitor_pool", "validation", "locked_test")
                },
            }
            from research.bar_gpt.v3.compare_full_training_checkpoints import (
                _canonical_json_hash,
            )

            manifest_hash = _canonical_json_hash(manifest)
            manifest["manifest_hash"] = manifest_hash
            path.write_text(json.dumps(manifest), encoding="utf-8")
            index_root = root / "full_catalog_index_v1"
            index_root.mkdir()
            unit_keys = ("A:2026-01", "B:2026-01")
            import hashlib

            rows = [
                {
                    "contract_version": 9,
                    "label": "full-held-out",
                    "units": 2,
                    "unit_keys_hash": hashlib.sha256(
                        "\n".join(unit_keys).encode("utf-8")
                    ).hexdigest(),
                },
                {
                    "unit_key": "A:2026-01",
                    "refs": [{
                        "unit_key": "A:2026-01", "session_index": 0,
                        "block_index": 0, "ticker": "A", "local_date": "2026-01-02",
                        "unit_index": 0, "block_offset": 0, "origins": 11, "activity_regime": 0,
                        "session_phase": "regular_midday", "has_condition_target": False,
                    }],
                },
                {
                    "unit_key": "B:2026-01",
                    "refs": [{
                        "unit_key": "B:2026-01", "session_index": 0,
                        "block_index": 0, "ticker": "B", "local_date": "2026-01-02",
                        "unit_index": 1, "block_offset": 0, "origins": 13, "activity_regime": 0,
                        "session_phase": "regular_midday", "has_condition_target": False,
                    }],
                },
            ]
            index_path = index_root / "held_out.jsonl"
            index_path.write_text(
                "\n".join(json.dumps(row) for row in rows) + "\n",
                encoding="utf-8",
            )
            result = _verify_manifest_and_population(
                path,
                expected_hash=manifest_hash,
                ticker_order=("A", "B"),
            )
        self.assertEqual(result[0], manifest_hash)
        self.assertEqual(result[2:], (24, 2))

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
        self.assertIn("--entire-held-out-population", command)
        self.assertNotIn("--target-training-origins", command)
        self.assertNotIn("--max-batches", command)


if __name__ == "__main__":
    unittest.main()
