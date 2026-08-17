from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import torch

from research.bar_gpt.v3.config import DataConfig
from research.bar_gpt.v3.grouped_checkpoint_evaluation import (
    ReturnDiagnosticAccumulator,
    classification_diagnostics,
    group_labels,
    load_ticker_metadata,
    load_portable_manifest,
    parse_units,
    select_panel_refs,
)
from research.bar_gpt.v3.inference import _install_pathlib_pickle_compat
from research.bar_gpt.v3.metrics import ValidationAccumulator
from research.bar_gpt.v3.model_discovery import (
    DISCOVERY_CONTRACT_VERSION,
    discovery_shard_compatibility_hash,
)
from research.bar_gpt.v3.targets import CONTINUOUS_TARGET_COUNT, RETURN_TARGET_COUNT


class GroupedCheckpointEvaluationTest(unittest.TestCase):
    def test_installs_cross_python_pathlib_pickle_alias(self) -> None:
        import sys

        _install_pathlib_pickle_compat()
        self.assertIn("pathlib._local", sys.modules)

    def test_accepts_arbitrary_unique_units(self) -> None:
        units = parse_units("SPY:2026-07,NVDA:2026-03,SOFI:2026-03,XBIO:2026-03,LIQT:2026-07")
        self.assertEqual(len(units), 5)
        self.assertEqual(parse_units("SPY:2026-07,NVDA:2026-03"), ("SPY:2026-07", "NVDA:2026-03"))
        with self.assertRaisesRegex(ValueError, "at least one"):
            parse_units("")
        with self.assertRaisesRegex(ValueError, "unique"):
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
            local_date="2026-07-02",
            session_phase="regular_midday",
        )
        labels = group_labels(
            block,
            ticker_metadata={
                "SPY": {"instrument": "ETF"},
                "SPY|2026-07-02": {"price_bucket": "high"},
            },
        )
        self.assertIn("activity/active", labels)
        self.assertIn("metadata_instrument/ETF", labels)
        self.assertIn("metadata_price_bucket/high", labels)

    def test_point_in_time_metadata_rejects_ticker_only_market_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "metadata.json"
            path.write_text(
                json.dumps({"SPY": {"market_cap_bucket": "mega"}}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "TICKER\\|YYYY-MM-DD"):
                load_ticker_metadata(str(path))

    def test_return_diagnostics_report_scale_and_zero_baseline(self) -> None:
        def transformed(percent: float) -> float:
            return float(torch.asinh(torch.log1p(torch.tensor(percent / 100.0)) * 100.0))

        targets = torch.zeros(1, 2, 1, CONTINUOUS_TARGET_COUNT)
        targets[0, 0, 0, 0] = transformed(1.0)
        targets[0, 1, 0, 0] = transformed(2.0)
        quantiles = torch.zeros(1, 2, 1, RETURN_TARGET_COUNT, 3)
        quantiles[..., 1] = targets[..., :RETURN_TARGET_COUNT]
        output = SimpleNamespace(horizon_quantiles=quantiles)
        batch = SimpleNamespace(
            horizon_targets=targets,
            horizon_mask=torch.ones_like(targets, dtype=torch.bool),
            origin_mask=torch.ones(1, 2, dtype=torch.bool),
        )
        accumulator = ReturnDiagnosticAccumulator((5_000_000,), (0.1, 0.5, 0.9))
        accumulator.update(output, batch)
        diagnostic = accumulator.finalize()["trade_open_return/5s"]
        self.assertAlmostEqual(diagnostic["mae_bps"], 0.0, places=6)
        self.assertAlmostEqual(diagnostic["zero_baseline_mae_bps"], 150.0, places=4)
        self.assertAlmostEqual(diagnostic["mean_target_bps"], 150.0, places=4)
        self.assertAlmostEqual(diagnostic["correlation"], 1.0, places=6)
        weighted = accumulator.finalize()["support_weighted"]
        self.assertAlmostEqual(weighted["mae_bps"], 0.0, places=6)
        self.assertAlmostEqual(weighted["mae_improvement_vs_zero"], 1.0, places=6)

    def test_classification_diagnostics_include_raw_accuracy_and_support(self) -> None:
        accumulator = ValidationAccumulator((5_000_000,), (0.1, 0.5, 0.9))
        perfect = torch.diag(torch.tensor([3.0, 4.0, 5.0]))
        accumulator.horizon_return_confusion = perfect.repeat(1, 3, 1, 1)
        accumulator.autoregressive_return_confusion = {"5s": perfect.repeat(3, 1, 1)}
        result = classification_diagnostics(accumulator)
        self.assertEqual(result["physical_close_macro"]["accuracy"], 1.0)
        self.assertEqual(result["physical_close_macro"]["macro_f1"], 1.0)
        self.assertEqual(result["physical_close_support_weighted"]["accuracy"], 1.0)
        self.assertEqual(
            result["physical_close"]["trade_close_return/5s"]["class_support"],
            {"negative": 3, "neutral": 4, "positive": 5},
        )


if __name__ == "__main__":
    unittest.main()
