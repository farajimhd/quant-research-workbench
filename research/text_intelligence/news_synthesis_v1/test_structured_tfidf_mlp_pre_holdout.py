from __future__ import annotations

import numpy as np
from scipy import sparse

from .structured_tfidf_mlp_pre_holdout import _max_abs_scale, _scaled


def test_sparse_max_abs_scaling_uses_training_columns() -> None:
    matrix = sparse.csr_matrix([[0.0, -2.0, 4.0], [0.0, 1.0, 2.0]])
    scale = _max_abs_scale(matrix)
    assert scale.tolist() == [1.0, 0.5, 0.25]
    assert np.max(np.abs(_scaled(matrix, scale).toarray()), axis=0).tolist() == [0.0, 1.0, 1.0]
