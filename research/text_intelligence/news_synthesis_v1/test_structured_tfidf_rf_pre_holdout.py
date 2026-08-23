from __future__ import annotations

import json

from scipy import sparse

from .structured_tfidf_rf_pre_holdout import _aligned_training_texts, _combined_matrix


def test_aligned_training_texts_follows_feature_order(tmp_path) -> None:
    path = tmp_path / "texts.jsonl"
    rows = [
        {"source_id": "b", "rendered_text": "second"},
        {"source_id": "a", "rendered_text": "first"},
        {"source_id": "unused", "rendered_text": "ignored"},
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    assert _aligned_training_texts(
        path, [{"source_id": "a"}, {"source_id": "b"}],
    ) == ["first", "second"]


def test_combined_matrix_preserves_rows_and_adds_columns() -> None:
    structured = sparse.csr_matrix([[1.0, 0.0], [0.0, 1.0]])
    text = sparse.csr_matrix([[0.0, 2.0, 0.0], [3.0, 0.0, 4.0]])
    combined = _combined_matrix(structured, text)
    assert combined.shape == (2, 5)
    assert combined.toarray().tolist() == [
        [1.0, 0.0, 0.0, 2.0, 0.0],
        [0.0, 1.0, 3.0, 0.0, 4.0],
    ]
