from __future__ import annotations

from .structured_metadata_rf_forward import EXPERIMENT_VERSION


def test_forward_experiment_version_names_direction_and_final_labels() -> None:
    assert "2025_to_2026" in EXPERIMENT_VERSION
    assert "final_labels" in EXPERIMENT_VERSION
