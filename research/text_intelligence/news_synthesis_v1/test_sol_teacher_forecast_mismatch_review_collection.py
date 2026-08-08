from __future__ import annotations

import pytest

from .sol_teacher_forecast_mismatch_review_collection import _validate


def test_validate_mismatch_review() -> None:
    row = _row()
    assert _validate(row)["issue_family"] == "below_consensus_missed"


def test_validate_mismatch_review_rejects_free_form_family() -> None:
    row = _row()
    row["issue_family"] = "Below consensus missed"
    with pytest.raises(RuntimeError, match="family"):
        _validate(row)


def _row() -> dict[str, object]:
    return {
        "unit_id": "S1::ABC",
        "mismatch_verdict": "engine_error",
        "failure_stage": "numeric_comparison",
        "issue_family": "below_consensus_missed",
        "systematic_probability": "high",
        "dominant_source_evidence": "EPS guidance is below consensus.",
        "engine_failure_evidence": "Engine assigned the comparison positive.",
        "root_cause_hypothesis": "Comparator scope was lost.",
        "fundamental_fix_candidate": "Preserve row-level comparator scope.",
        "confidence": "high",
        "rationale": "The adverse benchmark comparison should dominate.",
    }
