from __future__ import annotations

from .structured_rf_disagreement_audit import (
    _allocate_strata,
    _confidence_band,
    _dominant_channel,
    _wilson_interval,
)


def test_stratified_allocation_is_exact_and_represents_every_stratum() -> None:
    counts = {"a": 100, "b": 10, "c": 2}
    allocation = _allocate_strata(counts, 20)
    assert sum(allocation.values()) == 20
    assert all(1 <= allocation[key] <= counts[key] for key in counts)
    assert allocation["a"] > allocation["b"] > allocation["c"]


def test_confidence_band_is_symmetric_by_predicted_class() -> None:
    assert _confidence_band("eligible", 0.91) == "extreme_gte_0_90"
    assert _confidence_band("ineligible", 0.09) == "extreme_gte_0_90"
    assert _confidence_band("eligible", 0.55) == "boundary_lt_0_60"


def test_dominant_channel_uses_stable_priority() -> None:
    assert _dominant_channel(["News", "Trading Ideas"]) == "trading_ideas"
    assert _dominant_channel([]) == "none"


def test_wilson_interval_contains_observed_rate() -> None:
    lower, upper = _wilson_interval(0.8, 100)
    assert lower < 0.8 < upper
