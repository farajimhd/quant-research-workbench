from __future__ import annotations

from pathlib import Path

import pytest

from .sol_teacher_evaluation import write_json_atomic
from .sol_teacher_forecast_reviewed_gold import create_reviewed_audit_gold


def test_reviewed_gold_applies_only_wrong_and_retains_uncertain(tmp_path: Path) -> None:
    split = tmp_path / "split"
    review = tmp_path / "review"
    output = tmp_path / "output"
    split.mkdir()
    review.mkdir()
    units = [
        _unit("S1::A", "positive"),
        _unit("S2::B", "mixed"),
        _unit("S3::C", "neutral"),
    ]
    audit = {
        "version": "split-v1",
        "partition": "audit",
        "prediction_blind": True,
        "article_ids": ["S1", "S2", "S3"],
        "articles": [],
        "units": units,
        "balance": {},
    }
    write_json_atomic(split / "audit_set.json", audit)
    write_json_atomic(split / "split_manifest.json", {"version": "split-v1"})
    decisions = [
        _decision("S1::A", "positive", "correct"),
        _decision("S2::B", "negative", "wrong"),
        _decision("S3::C", "mixed", "policy_uncertain"),
    ]
    write_json_atomic(review / "consolidated_reviews.json", decisions)
    write_json_atomic(review / "review_progress.json", {"complete": True})

    manifest = create_reviewed_audit_gold(split, review, output)

    reviewed = __import__("json").loads(
        (output / "reviewed_audit_set.json").read_text(encoding="utf-8")
    )
    assert [row["gold_sentiment"] for row in reviewed["units"]] == [
        "positive", "negative", "neutral"
    ]
    assert manifest["resolution_counts"] == {
        "policy_uncertain_original_retained": 1,
        "review_confirmed": 1,
        "reviewed_correction": 1,
    }
    assert manifest["correction_transitions"] == [
        {"from": "mixed", "to": "negative", "units": 1}
    ]


def test_reviewed_gold_rejects_identity_mismatch(tmp_path: Path) -> None:
    split = tmp_path / "split"
    review = tmp_path / "review"
    split.mkdir()
    review.mkdir()
    write_json_atomic(split / "audit_set.json", {
        "article_ids": [], "articles": [], "units": [_unit("S1::A", "positive")]
    })
    write_json_atomic(split / "split_manifest.json", {"version": "split-v1"})
    write_json_atomic(review / "consolidated_reviews.json", [
        _decision("S2::B", "positive", "correct")
    ])
    write_json_atomic(review / "review_progress.json", {"complete": True})
    with pytest.raises(RuntimeError, match="identities"):
        create_reviewed_audit_gold(split, review, tmp_path / "output")


def _unit(unit_id: str, direction: str) -> dict[str, object]:
    sample_id, ticker = unit_id.split("::", 1)
    return {
        "unit_id": unit_id,
        "sample_id": sample_id,
        "ticker": ticker,
        "gold_sentiment": direction,
        "provider": "benzinga",
        "year": "2020",
    }


def _decision(unit_id: str, direction: str, verdict: str) -> dict[str, object]:
    positive = 1 if direction in {"positive", "mixed"} else 0
    negative = 1 if direction in {"negative", "mixed"} else 0
    return {
        "unit_id": unit_id,
        "reviewed_direction": direction,
        "gold_verdict": verdict,
        "positive_strength": positive,
        "negative_strength": negative,
        "dominant_evidence": "source evidence",
        "countervailing_evidence": "",
        "issuer_attribution": "supported",
        "confidence": "high",
        "rationale": "source-grounded rationale",
    }
