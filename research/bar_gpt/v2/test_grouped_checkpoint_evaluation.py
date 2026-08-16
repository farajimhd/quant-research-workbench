from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from research.bar_gpt.v2.config import DataConfig
from research.bar_gpt.v2.grouped_checkpoint_evaluation import (
    group_labels,
    load_portable_manifest,
    parse_units,
    select_panel_refs,
)
from research.bar_gpt.v2.inference import _install_pathlib_pickle_compat
from research.bar_gpt.v2.model_discovery import (
    DISCOVERY_CONTRACT_VERSION,
    discovery_shard_compatibility_hash,
)


class GroupedCheckpointEvaluationTest(unittest.TestCase):
    def test_installs_cross_python_pathlib_pickle_alias(self) -> None:
        import sys

        _install_pathlib_pickle_compat()
        self.assertIn("pathlib._local", sys.modules)

    def test_requires_exactly_five_unique_units(self) -> None:
        units = parse_units("SPY:2026-07,NVDA:2026-03,SOFI:2026-03,XBIO:2026-03,LIQT:2026-07")
        self.assertEqual(len(units), 5)
        with self.assertRaisesRegex(ValueError, "exactly 5"):
            parse_units("SPY:2026-07,NVDA:2026-03")
        with self.assertRaisesRegex(ValueError, "exactly 5"):
            parse_units("SPY:2026-07," * 5)

    def test_portable_manifest_verifies_hash_and_checkpoint_contract(self) -> None:
        config = DataConfig()
        value = {
            "contract_version": DISCOVERY_CONTRACT_VERSION,
            "shard_root": r"D:\source-only",
            "shard_config_hash": discovery_shard_compatibility_hash(config),
            "panels": {"validation": []},
        }
        canonical = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
        value["manifest_hash"] = hashlib.sha256(canonical).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            loaded = load_portable_manifest(path, data_config=config)
            self.assertEqual(loaded["shard_root"], r"D:\source-only")
            value["panels"]["validation"].append({"tampered": True})
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "content hash"):
                load_portable_manifest(path, data_config=config)

    def test_panel_selection_fails_closed_on_missing_unit(self) -> None:
        row = {
            "unit_key": "SPY:2026-07",
            "session_index": 0,
            "block_index": 1,
            "origins": 10,
            "ticker": "SPY",
            "local_date": "2026-07-02",
            "activity_regime": 2,
            "session_phase": "regular_midday",
            "has_condition_target": False,
            "unit_index": 1,
            "block_offset": 1,
        }
        manifest = {"panels": {"validation": [row]}}
        refs = select_panel_refs(manifest, panel="validation", units=("SPY:2026-07",))
        self.assertEqual(refs[0].ticker, "SPY")
        with self.assertRaisesRegex(RuntimeError, "absent"):
            select_panel_refs(manifest, panel="validation", units=("NVDA:2026-03",))

    def test_groups_embedded_dimensions_and_external_metadata(self) -> None:
        block = SimpleNamespace(
            activity_regime=2,
            ticker="SPY",
            session_phase="regular_midday",
        )
        labels = group_labels(
            block,
            ticker_metadata={"SPY": {"instrument": "ETF", "price_bucket": "high"}},
        )
        self.assertIn("activity/active", labels)
        self.assertIn("metadata_instrument/ETF", labels)
        self.assertIn("metadata_price_bucket/high", labels)


if __name__ == "__main__":
    unittest.main()
