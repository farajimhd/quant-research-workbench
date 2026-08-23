from __future__ import annotations

from .forecast_holdout_audit import _packetize, _wilson_lower


def test_packetize_respects_article_and_character_limits() -> None:
    rows = [
        {"review_id": "a", "preview_text": "x" * 7},
        {"review_id": "b", "preview_text": "x" * 7},
        {"review_id": "c", "preview_text": "x" * 7},
    ]
    packets = _packetize(rows, article_limit=2, character_limit=10)
    assert [[row["review_id"] for row in packet] for packet in packets] == [["a"], ["b"], ["c"]]


def test_wilson_lower_is_bounded_and_increases_with_successes() -> None:
    assert _wilson_lower(0, 0) == 0.0
    assert 0.0 < _wilson_lower(9, 10) < _wilson_lower(10, 10) < 1.0
