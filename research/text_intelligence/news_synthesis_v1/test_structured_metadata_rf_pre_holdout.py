from __future__ import annotations

from .structured_metadata_rf_pre_holdout import (
    TRAIN_END_UTC,
    VALIDATION_START_UTC,
    _training_validation_mask,
)


def test_training_validation_mask_is_strictly_time_based() -> None:
    rows = [
        {"published_at_utc": "2025-01-01T00:00:00+00:00"},
        {"published_at_utc": VALIDATION_START_UTC},
        {"published_at_utc": TRAIN_END_UTC},
    ]
    assert _training_validation_mask(rows).tolist() == [False, True, True]
