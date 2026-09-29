"""Project certified ARTE seconds into a V5 next-second replay grid.

The existing V4 feature bank is causal, but its ``execution.npy`` contains
Phase 2 label prices rather than observed next-second bar opens. Replay must
read the pinned ARTE one-second bars and prior close instead. This module has
no query or output side effects; the session builder owns certification and
restart-safe files.
"""
from __future__ import annotations

import numpy as np
import polars as pl

from research.rl_trading.v1.features import SECONDS
from research.rl_trading.v2.build_data import execution_arrays
from research.rl_trading.v2.estimated_luld import reference_series

VERSION = 'rl-trading-v5-arte-next-open-grid-v1'


def project_certified_seconds(bars: pl.DataFrame, prior_close: float) -> dict[str, np.ndarray]:
    """Vectorize one pinned listing into [57_601] completed and arrival arrays.

    Index ``t`` represents the bar completed at second ``t`` from 04:00 ET.
    A decision after second ``t`` can submit an IOC proxy for the *actual*
    bar opening at ``t+1``. Neither the bar open nor its volume is visible to
    the decision policy. We store them separately as execution-only data.
    """
    if not np.isfinite(prior_close) or prior_close < 0:
        raise ValueError('Prior regular-session close is invalid')
    values = execution_arrays(bars)
    if any(np.asarray(values[name]).shape != (SECONDS,) for name in
           ('prices', 'execution_open', 'volume', 'fresh')):
        raise ValueError('Certified execution projection has the wrong session length')
    volume = np.asarray(values['volume'], dtype=np.float32)
    opening = np.asarray(values['execution_open'], dtype=np.float32)
    price_valid = np.asarray(values['fresh'], dtype=np.bool_)
    if not (np.isfinite(volume).all() and np.isfinite(opening).all()
            and np.all(volume >= 0) and np.all(opening >= 0)):
        raise ValueError('Certified execution bars have invalid price or volume')
    close = np.asarray(values['prices'], dtype=np.float32)
    reference = reference_series(close, volume, price_valid, prior_close)
    return dict(close=close, next_open=opening, volume=volume,
                fresh=price_valid & (volume > 0) & (opening > 0),
                prior_close=np.float32(prior_close),
                estimated_reference=np.asarray(reference, dtype=np.float32))
