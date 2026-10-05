import numpy as np
import pytest

from src.trading_runtime.squeeze_ladder_cross import (
    REJECT_ADMISSION, REJECT_CONTINUITY, REJECT_PRICE, REJECT_REFERENCE,
    completed_crossings,
)


def columns():
    return dict(
        boundary_ms=np.array([100, 200, 300, 400], dtype=np.int64),
        close_int=np.array([99900, 100100, 99900, 100200], dtype=np.int64),
        price_valid=np.ones(4, dtype=np.uint8),
        reference_int=np.full(4, 100000, dtype=np.int64),
        reference_available_ms=np.array([100, 200, 300, 400], dtype=np.int64),
        reference_valid=np.ones(4, dtype=np.uint8),
        admitted_at_ms=100, buffer_int=100, maximum_reference_age_ms=100,
    )


def test_strict_tick_buffer_and_admission_clock():
    args = columns()
    result = completed_crossings(**args)
    assert result.crossing.tolist() == [False, False, False, True]
    args["admitted_at_ms"] = 350
    result = completed_crossings(**args)
    assert not result.crossing.any()
    assert result.rejection[3] & REJECT_ADMISSION


def test_missing_price_or_quote_only_bucket_cannot_confirm_cross():
    args = columns()
    args["price_valid"][2] = 0
    result = completed_crossings(**args)
    assert not result.crossing.any()
    assert result.rejection[3] & REJECT_PRICE


def test_sparse_clock_cannot_bridge_missing_observation():
    args = columns()
    args["boundary_ms"][3] = 500
    result = completed_crossings(**args)
    assert not result.crossing.any()
    assert result.rejection[3] & REJECT_CONTINUITY


@pytest.mark.parametrize("at", [-1, 199, 401])
def test_absent_stale_or_future_reference_fails_closed(at):
    args = columns()
    args["reference_available_ms"][3] = at
    result = completed_crossings(**args)
    assert not result.crossing.any()
    assert result.rejection[3] & REJECT_REFERENCE


def test_empty_batch_is_valid_without_invented_prior_observation():
    args = columns()
    for key, value in list(args.items()):
        if isinstance(value, np.ndarray):
            args[key] = value[:0]
    assert len(completed_crossings(**args).crossing) == 0


def test_unsigned_price_and_float_clock_cannot_be_silently_coerced():
    for key, dtype in [("close_int", np.uint64), ("boundary_ms", np.float64)]:
        args = columns()
        args[key] = args[key].astype(dtype)
        with pytest.raises(ValueError):
            completed_crossings(**args)


def test_vectorized_result_matches_scalar_completed_observations():
    rng = np.random.default_rng(23)
    n = 5000
    clocks = np.arange(1, n + 1, dtype=np.int64) * 100
    prices = rng.integers(99800, 100300, n, dtype=np.int64)
    valid = rng.integers(0, 2, n, dtype=np.uint8)
    refs = rng.integers(99950, 100050, n, dtype=np.int64)
    args = dict(boundary_ms=clocks, close_int=prices, price_valid=valid,
                reference_int=refs, reference_available_ms=clocks.copy(),
                reference_valid=np.ones(n, dtype=np.uint8), admitted_at_ms=1000,
                buffer_int=100, maximum_reference_age_ms=0)
    expected = [False] + [bool(valid[j - 1] and valid[j]
        and clocks[j - 1] >= 1000 and prices[j - 1] <= refs[j - 1]
        and prices[j] > refs[j] + 100) for j in range(1, n)]
    assert completed_crossings(**args).crossing.tolist() == expected
