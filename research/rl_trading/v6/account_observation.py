"""One causal account/holding observation shared by V6 teacher and replay.

Only confirmed OMS positions enter the held axis. Submitted entry intents do
not create holdings or make stop/target child actions admissible. This adapter
contains no oracle outcome, later quote, or portfolio sizing forecast.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np

from research.rl_trading.v6.model import HELD_FEATURE_WIDTH
from research.rl_trading.v6.oms import BracketAccount
from research.rl_trading.v6.causal_order_queue import CausalOrderQueue


@dataclass(frozen=True)
class AccountObservation:
    account: np.ndarray  # [7] cash, equity, realized, exposure, age, pending.
    held_index: np.ndarray  # [H] listing-axis indices, ascending.
    held_features: np.ndarray  # [H,9] causal position/bracket state.
    exit_allowed: np.ndarray  # [H], manual exit before stop is pending.
    stop_allowed: np.ndarray  # [H], exactly one stop setting after fill.
    target_allowed: np.ndarray  # [H], exactly one target setting after fill.


def observe_account(account: BracketAccount, tickers: tuple[str, ...],
                    marks: dict[str, tuple[float, int]], *,
                    close_us: int, queue: CausalOrderQueue) -> AccountObservation:
    """Produce shape-stable inputs from completed marks at ``close_us``."""
    if (queue.account is not account or
            type(close_us) is not int or close_us <= 0 or not tickers or
            len(set(tickers)) != len(tickers)):
        raise ValueError('Invalid V6 market identity or completed close')
    by_ticker = {ticker: index for index, ticker in enumerate(tickers)}
    if not set(account.positions) <= set(by_ticker):
        raise ValueError('OMS position has no certified listing identity')
    held = sorted(account.positions, key=by_ticker.__getitem__)
    mark_prices = {}
    for ticker in held:
        if ticker not in marks:
            raise ValueError('Held position lacks a causal positive mark')
        price, observed_us = marks[ticker]
        if (not math.isfinite(price) or price <= 0 or
                type(observed_us) is not int or
                not 0 < observed_us <= close_us):
            raise ValueError('Held position lacks a causal positive mark')
        mark_prices[ticker] = price
    equity = account.marked_equity(mark_prices)
    realized = sum(row['net_pnl'] for row in account.closed)
    exposure = sum(account.positions[ticker].shares*mark_prices[ticker]
                   for ticker in held)
    last_action_us = max((row['bucket_end_us'] for row in account.orders),
                         default=close_us)
    if last_action_us > close_us:
        raise ValueError('OMS order lies after observation close')
    state = np.asarray((account.cash, equity, realized,
                        exposure/equity if equity > 0 else 0.,
                        (close_us-last_action_us)/1_000_000,
                        queue.reserved_cash, len(queue.pending_entries)),
                       dtype=np.float32)
    features = np.zeros((len(held), HELD_FEATURE_WIDTH), dtype=np.float32)
    exit_mask = np.zeros(len(held), dtype=np.bool_)
    stop_mask = np.zeros(len(held), dtype=np.bool_)
    target_mask = np.zeros(len(held), dtype=np.bool_)
    for row, ticker in enumerate(held):
        position = account.positions[ticker]
        price = mark_prices[ticker]
        exit_pending = ticker in queue.pending_exits
        features[row] = (position.shares, position.entry_price,
            (close_us-position.entry_us)/1_000_000,
            (price-position.entry_price)/position.entry_price,
            (price-position.stop)/price if position.stop is not None else 0.,
            (position.target-price)/price if position.target is not None else 0.,
            float(position.stop is not None),
            float(position.target is not None),
            float(position.stop_pending), 0., 0.)
        book = getattr(queue, 'luld', None)
        restriction = book.state(ticker, close_us) if book is not None else None
        if restriction and restriction['paused']:
            features[row,9] = 1.
            features[row,10] = math.log1p((close_us-restriction['pause_start_us'])/1_000_000)/10
        exit_mask[row] = not position.stop_pending and not exit_pending
        stop_mask[row] = (position.stop is None and
                          not position.stop_pending and not exit_pending)
        target_mask[row] = (position.target is None and
                            not position.stop_pending and not exit_pending)
    if not np.isfinite(state).all() or not np.isfinite(features).all():
        raise ValueError('Nonfinite causal account observation')
    return AccountObservation(state,
        np.asarray([by_ticker[ticker] for ticker in held], dtype=np.int64),
        features, exit_mask, stop_mask, target_mask)
