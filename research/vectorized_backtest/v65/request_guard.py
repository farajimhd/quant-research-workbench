"""Strategy-side request contract; never a neural input or GPU recurrence.

The chronological adapter checks its filled-add count before submitting another
add. Existing partial orders continue filling independently. The adapter's count
is updated once on first fill and reset when the position closes.
"""
import numpy as np
from numba import njit
from .authority import MAXIMUM_ADDS


@njit(inline='always')
def applicable_signals(signals,held):
    """Flat inventory cannot submit exit/reduce orders; inference stays stateless."""
    return signals if held else signals & ~(2 | 8)


@njit(inline='always')
def may_request_add(filled_add_orders):
    return filled_add_orders < MAXIMUM_ADDS


@njit(inline='always')
def entry_quantity(dollars,limit):
    return np.floor(dollars/limit) if np.isfinite(limit) and limit>0 else 0.


@njit(inline='always')
def whole_quantity(value):
    return np.floor(value) if np.isfinite(value) else value


@njit(inline='always')
def reduction_quantity(held,ratio):
    """A positive reduction maps to an executable whole-share request."""
    if not np.isfinite(ratio) or ratio<=0 or held<1:return 0.
    return min(np.floor(held),max(1.,np.floor(held*min(1.,ratio))))


@njit(inline='always')
def valid_request_size(limit,quantity):
    return np.isfinite(limit) and limit>0 and np.isfinite(quantity) and quantity>=1
