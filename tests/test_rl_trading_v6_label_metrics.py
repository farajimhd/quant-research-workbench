import numpy as np
import pytest
from research.rl_trading.v6.label_metrics import classification_metrics


def test_confusion_orientation_and_missing_labels():
    result = classification_metrics(np.array([[2,1,0,0],[0,3,0,0],[0,0,0,0],[1,0,0,1]]))
    assert result['labels']['ENTRY']['precision'] == pytest.approx(2/3)
    assert result['labels']['ENTRY']['recall'] == pytest.approx(2/3)
    assert result['labels']['EXIT']['precision'] == 1
    assert result['labels']['EXIT']['recall'] == .5
    assert result['labels']['HOLD']['count'] == 0
    assert result['accuracy'] == .75


def test_empty_horizon_is_not_perfect_accuracy():
    assert classification_metrics(np.zeros((4,4),int))['accuracy'] is None
