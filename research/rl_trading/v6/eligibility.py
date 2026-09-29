"""Causal entry-action mask shared by V6 teacher and environment replay.

Hindsight episode membership and future score never appear in this mask.
The teacher may *choose* an oracle opportunity, but the decoder must see the
same contemporaneously valid action space it has when trading itself.
"""
from __future__ import annotations

import math

import numpy as np

from research.rl_trading.v6.features import SCALAR_NAMES


PRICE_VALID = SCALAR_NAMES.index('bar_price_valid')


def causal_enter_mask(listings: int, changed_index: np.ndarray,
                      changed_scalar: np.ndarray, *,
                      cash: float, reserved_cash: float,
                      held_index: np.ndarray,
                      pending_index: np.ndarray) -> np.ndarray:
    """Allow current valid candles except held or already pending listings.

    This is an admissibility mask, not a fill promise: the later quote, fee,
    and share-size rule may still leave the model order unfilled.
    """
    if (listings < 1 or changed_index.ndim != 1 or
            changed_scalar.shape != (len(changed_index), len(SCALAR_NAMES)) or
            any(index.ndim != 1 for index in
                (held_index, pending_index)) or
            not all(math.isfinite(value) for value in
                    (cash, reserved_cash)) or
            cash < 0 or reserved_cash < 0 or
            reserved_cash > cash + 1e-6):
        raise ValueError('Invalid causal entry mask state')
    for index in (changed_index, held_index, pending_index):
        if (np.any(index < 0) or np.any(index >= listings) or
                len(np.unique(index)) != len(index)):
            raise ValueError('Invalid or duplicate listing identity')
    if len(set(held_index.tolist()) & set(pending_index.tolist())):
        raise ValueError('A pending entry cannot already be held')
    valid = changed_scalar[:, PRICE_VALID]
    if not np.all((valid == 0.) | (valid == 1.)):
        raise ValueError('Malformed certified bar-price mask')
    result = np.zeros(listings, dtype=np.bool_)
    if cash-reserved_cash > 0:
        result[changed_index[valid == 1.]] = True
        result[held_index] = False
        result[pending_index] = False
    return result
