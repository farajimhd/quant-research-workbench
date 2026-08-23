from __future__ import annotations

import numpy as np
import torch
from scipy import sparse

from .structured_tfidf_deepfm_pre_holdout import SparseDeepFM, _field_scale


def test_field_scale_scales_structured_and_preserves_tfidf() -> None:
    matrix = sparse.csr_matrix([[2.0, 4.0, 0.2, 0.8], [1.0, 2.0, 0.7, 0.1]])
    assert _field_scale(matrix, 2).tolist() == [0.5, 0.25, 1.0, 1.0]


def test_deepfm_forward_accepts_sparse_csr() -> None:
    matrix = torch.tensor([[1.0, 0.0, 0.5], [0.0, 1.0, 0.25]]).to_sparse_csr()
    model = SparseDeepFM(3)
    output = model(matrix)
    assert output.shape == (2,)
    assert np.isfinite(output.detach().numpy()).all()
