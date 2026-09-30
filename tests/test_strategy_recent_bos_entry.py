"""Causal completed-clock and scalar/columnar parity for recent BOS entries."""
import numpy as np
import pytest

from src.trading_runtime.strategy_recent_bos_entry import (
    MAX_BOS_ENTRY_AGE_MS, recent_bos_entry, recent_bos_entry_mask,
)


@pytest.mark.parametrize("boundary,broken,allowed", [
    (1_000, 1_000, True), (31_000, 1_000, True),
    (31_100, 1_000, False), (43_231_000, 43_201_000, True),
    (43_231_100, 43_201_000, False), (57_600_000, 57_570_000, True),
    (31_000, None, False),
])
def test_endpoint_missing_and_extended_session_clocks(boundary, broken, allowed):
    assert MAX_BOS_ENTRY_AGE_MS == 30_000
    assert recent_bos_entry(boundary_ms=boundary,
                            bos_break_boundary_ms=broken) is allowed
    result = recent_bos_entry_mask(np.array([boundary]), np.array([broken or 0]))
    assert result.tolist() == [allowed]


@pytest.mark.parametrize("boundary,broken", [
    (0, 1_000), (57_600_100, 1_000), (31_001, 1_000),
    (31_000, 32_000), (31_000, 1_100), (31_000, -1_000),
])
def test_malformed_and_future_source_are_integrity_errors(boundary, broken):
    with pytest.raises(ValueError):
        recent_bos_entry(boundary_ms=boundary, bos_break_boundary_ms=broken)
    with pytest.raises(ValueError):
        recent_bos_entry_mask(np.array([boundary]), np.array([broken]))


@pytest.mark.parametrize("boundary,broken", [(True, 1_000), (31_000, True),
                                            (31_000.0, 1_000), (31_000, 0)])
def test_scalar_rejects_untyped_or_zero_source(boundary, broken):
    with pytest.raises(ValueError):
        recent_bos_entry(boundary_ms=boundary, bos_break_boundary_ms=broken)


def test_native_batch_parity_and_future_tail_independence():
    boundaries = np.arange(1_000, 61_100, 100, dtype=np.int64)
    breaks = np.full(len(boundaries), 1_000, dtype=np.int64)
    expected = [recent_bos_entry(boundary_ms=int(at), bos_break_boundary_ms=1_000)
                for at in boundaries]
    full = recent_bos_entry_mask(boundaries, breaks)
    assert full.tolist() == expected
    assert np.array_equal(recent_bos_entry_mask(boundaries[:50], breaks[:50]), full[:50])
    assert np.array_equal(recent_bos_entry_mask(boundaries.astype(np.uint64),
                                             breaks.astype(np.uint64)), full)
    assert recent_bos_entry_mask(np.array([], dtype=np.int64),
                                 np.array([], dtype=np.int64)).shape == (0,)


@pytest.mark.parametrize("boundaries,breaks", [
    (np.array([31_000]), np.array([1_000, 2_000])),
    (np.array([[31_000]]), np.array([[1_000]])),
    (np.array([31_000.0]), np.array([1_000])),
    (np.array([31_000]), np.array([1_000.0])),
    (np.array([True]), np.array([False])),
])
def test_batch_shape_and_types_are_not_coerced(boundaries, breaks):
    with pytest.raises(ValueError):
        recent_bos_entry_mask(boundaries, breaks)
