from __future__ import annotations

from .structured_rf_priority_funnel import _dominant_channel, _packetize, _wilson_lower


def test_dominant_channel_prioritizes_event_channels() -> None:
    assert _dominant_channel({"news", "earnings misses"}) == "earnings_misses"
    assert _dominant_channel(set()) == "none"


def test_packetizer_respects_article_limit() -> None:
    rows = [{"preview_text": "x", "review_id": str(index)} for index in range(181)]
    packets = _packetize(rows)
    assert [len(packet) for packet in packets] == [180, 1]


def test_wilson_lower_is_fail_closed_for_empty_and_improves_with_sample() -> None:
    assert _wilson_lower(0, 0) == 0.0
    assert _wilson_lower(99, 100) < _wilson_lower(990, 1000)
