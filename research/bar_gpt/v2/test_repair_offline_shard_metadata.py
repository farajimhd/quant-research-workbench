from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import torch

from research.bar_gpt.v2.config import DataConfig
from research.bar_gpt.v2.offline_shards import (
    OFFLINE_SHARD_CONTRACT_VERSION,
    condition_positive_counts,
    write_unit,
)
from research.bar_gpt.v2.repair_offline_shard_metadata import (
    RepairCandidate,
    discover_candidates,
    repair_candidate,
)


def _payload(*, include_embedded_counts: bool = True) -> dict[str, object]:
    data = DataConfig()
    targets = torch.zeros((2, 1, 6), dtype=torch.float32)
    targets[0, 0, -4:] = torch.tensor([1.0, 0.0, -1.0, 2.0])
    targets[1, 0, -4:] = torch.tensor([0.0, 3.0, 4.0, 5.0])
    mask = torch.ones_like(targets, dtype=torch.bool)
    mask[1, 0, -2] = False
    sessions = [{"blocks": [{
        "origin_indices": torch.tensor([0, 1]),
        "horizon_targets": targets,
        "horizon_mask": mask,
    }]}]
    counts: dict[str, object] = {
        "sessions": 1,
        "blocks": 1,
        "origins": 2,
    }
    if include_embedded_counts:
        counts["condition_positive_counts"] = [1, 1, 0, 2]
    return {
        "contract_version": OFFLINE_SHARD_CONTRACT_VERSION,
        "config_hash": "a" * 64,
        "unit_key": "AAA:2019-01",
        "context_contract": {
            "intraday_context_bars": data.intraday_context_by_name,
            "calendar_context_bars": data.calendar_context_by_name,
            "attention_windows": data.attention_window_by_name,
            "intraday_warmup_bars_1s": data.intraday_warmup_bars_1s,
        },
        "sessions": sessions,
        "counts": counts,
    }


class OfflineShardMetadataRepairTest(unittest.TestCase):
    def test_condition_counts_use_only_valid_positive_targets(self) -> None:
        self.assertEqual(condition_positive_counts(_payload()["sessions"]), (1, 1, 0, 2))

    def test_repair_is_atomic_and_restart_safe(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence = write_unit(root, _payload(include_embedded_counts=False), certify_hash=True)
            sidecar_path = Path(evidence["path"]).with_suffix(".json")
            self.assertNotIn(
                "condition_positive_counts",
                json.loads(sidecar_path.read_text(encoding="utf-8")),
            )
            candidates, summary = discover_candidates(
                root,
                tickers=("AAA",),
                start_date="2019-01-01",
                end_date="2019-02-01",
            )
            self.assertEqual(summary["complete"], 1)
            self.assertEqual(len(candidates), 1)
            candidate = candidates[0]

            planned = repair_candidate(candidate, execute=False, verify_sha256=True)
            self.assertEqual(planned.status, "would_repair")
            self.assertNotIn(
                "condition_positive_counts",
                json.loads(sidecar_path.read_text(encoding="utf-8")),
            )

            repaired = repair_candidate(candidate, execute=True, verify_sha256=True)
            self.assertEqual(repaired.condition_positive_counts, (1, 1, 0, 2))
            updated = json.loads(sidecar_path.read_text(encoding="utf-8"))
            self.assertEqual(updated["condition_positive_counts"], [1, 1, 0, 2])
            self.assertEqual(
                updated["condition_positive_counts_source"],
                "horizon_targets_and_mask_v1",
            )
            self.assertEqual(
                repair_candidate(candidate, execute=True, verify_sha256=True).status,
                "already_repaired",
            )

    def test_repair_refuses_sidecar_tensor_size_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence = write_unit(root, _payload(include_embedded_counts=False), certify_hash=True)
            sidecar_path = Path(evidence["path"]).with_suffix(".json")
            value = json.loads(sidecar_path.read_text(encoding="utf-8"))
            value["bytes"] += 1
            sidecar_path.write_text(json.dumps(value), encoding="utf-8")
            candidate = RepairCandidate("AAA:2019-01", sidecar_path, sidecar_path.with_suffix(".pt"))
            with self.assertRaisesRegex(RuntimeError, "byte-size mismatch"):
                repair_candidate(candidate, execute=True, verify_sha256=False)


if __name__ == "__main__":
    unittest.main()
