from __future__ import annotations

from pathlib import Path

import numpy as np
import torch

from research.temporal_event_model.v3.data import TemporalBatch
from research.temporal_event_model.v3.run_profile_exact_training_loader import _batch_coverage, _coverage_required_keys
from research.temporal_event_model.v3.run_profile_exact_training_loader_batch_tests import _command


def test_batch_coverage_uses_target_masks_and_xbrl_mask() -> None:
    batch = TemporalBatch(
        x={
            "input_availability": {
                "event_context_available": torch.ones(4, dtype=torch.bool),
                "xbrl_available": torch.zeros(4, dtype=torch.bool),
            },
            "xbrl_inputs": {
                "mask": torch.tensor(
                    [
                        [False, False, False],
                        [True, False, False],
                        [False, False, False],
                        [True, True, False],
                    ],
                    dtype=torch.bool,
                )
            },
        },
        y={
            "intraday_labels": {
                "available": torch.tensor(
                    [
                        [True, False],
                        [False, False],
                        [True, True],
                        [False, True],
                    ],
                    dtype=torch.bool,
                )
            },
            "corporate_action_labels": {
                "future_split_flag": torch.zeros(4, 5, dtype=torch.bool),
            },
        },
        identity={
            "ticker": np.asarray(["A", "B", "C", "D"], dtype=object),
            "origin_ordinal": np.arange(4, dtype=np.int64),
            "origin_timestamp_us": np.arange(4, dtype=np.int64),
            "source_part_key": np.asarray(["p"] * 4, dtype=object),
        },
        profile={},
        sample_count=4,
    )
    coverage = _batch_coverage(batch)
    assert coverage["event_context_available"] == 1.0
    assert coverage["intraday_labels_available"] == 0.75
    assert coverage["xbrl_available"] == 0.5
    assert coverage["corporate_action_labels_available"] == 1.0


def test_auto_required_keys_include_label_and_sparse_modalities() -> None:
    keys = _coverage_required_keys(
        "auto",
        (
            "events",
            "intraday_labels",
            "corporate_action_labels",
            "xbrl",
            "sec_filing_embeddings",
        ),
    )
    assert "event_context_available" in keys
    assert "intraday_labels_available" in keys
    assert "corporate_action_labels_available" in keys
    assert "xbrl_available" in keys
    assert "sec_filings_available" in keys


def test_batch_test_commands_set_chunk_size_to_batch_size() -> None:
    for batch_size in (1024, 2048):
        command = _command(
            output_root=Path("D:/tmp/out"),
            cache_root=Path("D:/tmp/cache"),
            run_name=f"bs{batch_size}",
            batch_size=batch_size,
            batches=1,
            warmup_batches=0,
            overrides=[],
        )
        assert "--batch-size" in command
        assert command[command.index("--batch-size") + 1] == str(batch_size)
        assert "--materialize-chunk-size" in command
        assert command[command.index("--materialize-chunk-size") + 1] == str(batch_size)
