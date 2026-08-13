from __future__ import annotations

import numpy as np

from .train_v4_tfidf import _metrics, _roc_auc, _screening, _trusted


def test_trusted_uses_explicit_conversion_warning() -> None:
    assert _trusted({"conversion_lineage": {"eligibility_authority_warning": None}})
    assert not _trusted(
        {"conversion_lineage": {"eligibility_authority_warning": "inherited"}}
    )


def test_metrics_and_screening_reconcile() -> None:
    target = np.asarray([1, 1, 0, 0], dtype=np.float32)
    probability = np.asarray([0.9, 0.4, 0.6, 0.1], dtype=np.float32)
    metrics = _metrics(target, probability)
    assert metrics["confusion"] == {"TP": 1, "FN": 1, "FP": 1, "TN": 1}
    assert metrics["accuracy"] == 0.5
    screening = _screening(target, probability, 0.2)
    assert screening["rejected"] == 1
    assert screening["true_ineligible_rejected"] == 1
    assert screening["eligible_false_rejections"] == 0
    assert _roc_auc(target, probability) == 0.75
