from __future__ import annotations

import pytest

from scripts.prepare_news_v59_training_mismatch_audit import (
    metadata_cell,
    packetize,
    validate_review_row,
)


def test_packetize_never_exceeds_human_audit_limit() -> None:
    packets = packetize([{"review_id": str(index)} for index in range(205)])

    assert [len(packet) for packet in packets] == [100, 100, 5]


def test_validate_review_row_enforces_closed_schema() -> None:
    valid = {
        "review_id": "V591",
        "label": "ineligible",
        "confidence": "high",
        "policy": "price_reaction_why_moving",
        "title_pattern": "X trades higher; what is going on?",
        "justification": "The title reports a price reaction rather than a new issuer event.",
        "exception_flag": False,
    }

    validate_review_row(valid, context="test")

    with pytest.raises(ValueError, match="invalid label"):
        validate_review_row({**valid, "label": "maybe"}, context="test")
    with pytest.raises(ValueError, match="exception_flag"):
        validate_review_row({**valid, "exception_flag": "false"}, context="test")
    with pytest.raises(ValueError, match="schema drift"):
        validate_review_row({**valid, "gold_label": "ineligible"}, context="test")


def test_metadata_cell_contains_only_requested_audit_metadata() -> None:
    value = metadata_cell({
        "source_id": "abc",
        "published_at_utc": "2025-01-02 03:04:05",
        "author": "News Desk",
        "channels": ["news"],
        "provider_tags": ["earnings"],
        "tickers": ["XYZ"],
        "ticker_count": 1,
        "synthesis_path": "single_subject > report > issuer",
    })

    assert "source_id=abc" in value
    assert "date=2025-01-02" in value
    assert "tags=earnings" in value
    assert "tickers=XYZ" in value
    assert "gold" not in value
    assert "v59" not in value
