from __future__ import annotations

import numpy as np

from .structured_metadata_rf_reverse import EXPERIMENT_VERSION, _labels_for


def test_labels_for_preserves_index_order() -> None:
    rows = [{"source_id": "b"}, {"source_id": "a"}]
    result = _labels_for(rows, {"a": "ineligible", "b": "eligible"})
    np.testing.assert_array_equal(result, np.asarray([1, 0], dtype=np.int8))


def test_reverse_experiment_version_names_direction_and_final_labels() -> None:
    assert "2026_to_2025" in EXPERIMENT_VERSION
    assert "final_labels" in EXPERIMENT_VERSION
