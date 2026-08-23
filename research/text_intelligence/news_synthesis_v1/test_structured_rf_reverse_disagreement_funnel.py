from __future__ import annotations

from .structured_rf_reverse_disagreement_funnel import (
    EXPECTED_POPULATION,
    FAST_QA_MAX_NEEDS_FULL,
    FAST_QA_MIN_AGREEMENT,
    FAST_QA_MIN_WILSON_LOWER,
    FULL_AGENT_REVIEWERS,
    LEDGER_NAMES,
    QA_REVIEWERS,
)


def test_reverse_disagreement_population_is_frozen() -> None:
    assert EXPECTED_POPULATION == 16_680


def test_fast_lane_requires_high_certification() -> None:
    assert FAST_QA_MIN_AGREEMENT == 0.985
    assert FAST_QA_MIN_WILSON_LOWER == 0.975
    assert FAST_QA_MAX_NEEDS_FULL == 0.005


def test_reuse_is_limited_to_correction_grade_ledgers() -> None:
    assert set(LEDGER_NAMES) == {
        "provider_filter_correction_ledger.jsonl",
        "provider_path_exception_correction_ledger.jsonl",
        "provider_path_exception_refinement_ledger.jsonl",
        "structured_rf_disagreement_audit_ledger.jsonl",
        "structured_rf_priority_blind_review_ledger.jsonl",
        "trading_ideas_correction_ledger.jsonl",
    }


def test_reviewer_pools_are_bounded_and_distinct() -> None:
    assert QA_REVIEWERS == ("Q1", "Q2")
    assert FULL_AGENT_REVIEWERS == ("A1", "A2", "A3", "A4", "A5", "A6")
    assert set(QA_REVIEWERS).isdisjoint(FULL_AGENT_REVIEWERS)
