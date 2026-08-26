from __future__ import annotations

import unittest

import numpy as np
from scipy import sparse

from .structured_tfidf_mlp_pre_holdout import apply_column_multipliers


class ColumnMultiplierTest(unittest.TestCase):
    def test_applies_persisted_values_as_multipliers(self) -> None:
        matrix = sparse.csr_matrix([[2.0, 4.0]], dtype=np.float32)
        result = apply_column_multipliers(
            matrix, np.asarray([0.5, 0.25], dtype=np.float32),
        )
        np.testing.assert_allclose(result.toarray(), [[1.0, 1.0]])

    def test_rejects_invalid_multiplier_contract(self) -> None:
        matrix = sparse.csr_matrix([[1.0, 2.0]], dtype=np.float32)
        with self.assertRaisesRegex(ValueError, "finite and positive"):
            apply_column_multipliers(
                matrix, np.asarray([1.0, 0.0], dtype=np.float32),
            )
        with self.assertRaisesRegex(ValueError, "dimensionality"):
            apply_column_multipliers(
                matrix, np.asarray([1.0], dtype=np.float32),
            )


if __name__ == "__main__":
    unittest.main()
