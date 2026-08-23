from __future__ import annotations

import numpy as np

from .structured_metadata_rf_reverse import _labels_for


def test_labels_for_preserves_index_order() -> None:
    rows = [{"source_id": "b"}, {"source_id": "a"}]
    result = _labels_for(rows, {"a": "ineligible", "b": "eligible"})
    np.testing.assert_array_equal(result, np.asarray([1, 0], dtype=np.int8))
