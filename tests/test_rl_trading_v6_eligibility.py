import numpy as np
import pytest

from research.rl_trading.v6.eligibility import (PRICE_VALID,
                                                 causal_enter_mask)


def test_enter_space_is_current_valid_candles_not_future_teacher_winners():
    rows = np.zeros((4, 37), dtype=np.float32)
    rows[:, PRICE_VALID] = [1., 1., 1., 0.]
    mask = causal_enter_mask(5, np.array([0, 1, 2, 4]), rows,
        cash=1_000., reserved_cash=100.,
        held_index=np.array([1]), pending_index=np.array([2]))
    assert mask.tolist() == [True, False, False, False, False]


def test_no_free_cash_or_duplicate_identity_fails_closed():
    rows = np.zeros((1, 37), dtype=np.float32)
    rows[0, PRICE_VALID] = 1.
    assert not causal_enter_mask(1, np.array([0]), rows,
        cash=100., reserved_cash=100., held_index=np.array([],dtype=int),
        pending_index=np.array([],dtype=int)).any()
    with pytest.raises(ValueError, match='Invalid or duplicate'):
        causal_enter_mask(1, np.array([0, 0]), np.repeat(rows,2,axis=0),
            cash=100., reserved_cash=0., held_index=np.array([],dtype=int),
            pending_index=np.array([],dtype=int))
