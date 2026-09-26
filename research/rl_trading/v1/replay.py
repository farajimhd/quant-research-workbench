"""Closed-loop long-only replay over a certified shard execution sidecar.

Each action changes cash and lots before the next second. The model never sees
Phase 2 future values, the Phase 3 path, or teacher account state in replay.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Callable

import numpy as np

from research.rl_trading.v1.data import SessionShard
from research.rl_trading.v1.common import bounds
from research.rl_trading.v1.features import FEATURE_NAMES

TRADES = FEATURE_NAMES.index('log_trades_60s')


@dataclass(frozen=True)
class Lot:
    ticker_index: int
    quantity: float
    entry_price: float
    entry_us: int


def _rank_chunk(volumes: np.ndarray, tickers: list[str], positions: np.ndarray) -> np.ndarray:
    """Stable ticker-tie ordering in a bounded vectorized market window."""
    lexical = np.argsort(np.asarray(tickers,dtype='U'),kind='stable')
    selected = np.asarray(volumes[lexical[:,None],positions[None,:]],dtype=np.float64).T
    if np.any(~np.isfinite(selected)) or np.any(selected < 0):
        raise ValueError('Completed-volume ranking is invalid')
    return lexical[np.argsort(-selected,axis=1,kind='stable')]


def replay_session(shard: SessionShard, prepare: Callable, *, max_seconds: int = 0) -> dict:
    """Replay the entire session, unless a bounded smoke limit is explicit.

    ``prepare(state)`` returns ``select(step, mask, previous_token)``. The
    selector may use only the supplied causal state. Token 0 stops, 1..N buys
    one allocation unit, and N+1..N+max_lots closes a starting lot.
    """
    arrays = shard.arrays
    plan = shard.plan
    tickers = plan['tickers']
    top_n = int(plan['top_n'])
    max_lots = int(plan['max_lots'])
    max_orders = int(plan['max_orders'])
    initial = float(plan['initial_cash'])
    step_cash = float(plan['allocation_step'])
    minimum_volume = float(plan['liquidity_filter']['min_volume_60s'])
    minimum_trades = int(plan['liquidity_filter']['min_trades_60s'])
    left_us = bounds(date.fromisoformat(plan['date']))[0]
    cash = initial
    lots: list[Lot] = []
    previous_equity = initial
    peak = initial
    max_drawdown = 0.
    buys = sells = forced = 0
    rows = shard.complete['rows'] if not max_seconds else min(max_seconds,shard.complete['rows'])
    if rows < 1:
        raise ValueError('Replay has no decision seconds')
    rank_start = rank_stop = 0
    ranked = np.empty((0,len(tickers)),dtype=np.int32)
    for index in range(rows):
        time_us = int(arrays['time_us'][index])
        second = (time_us-left_us)//1_000_000
        if second < 0 or second >= arrays['features'].shape[1]:
            raise ValueError('Replay time lies outside the market session')
        terminal = bool(arrays['done'][index])
        if terminal and index != rows-1:
            raise ValueError('Terminal second precedes replay end')
        if index >= rank_stop:
            rank_start = index
            rank_stop = min(rows,index+256)
            positions = ((np.asarray(arrays['time_us'][rank_start:rank_stop])-left_us)//1_000_000).astype(np.int64)
            ranked = _rank_chunk(arrays['volume_60s'],tickers,positions)
        ranking = ranked[index-rank_start]
        held = {lot.ticker_index for lot in lots}
        visible = sorted(held,key=lambda i:tickers[i])
        visible.extend(i for i in ranking if i not in held and len(visible) < top_n)
        visible = visible[:top_n]
        if len(visible) != min(top_n,len(tickers)) or not held <= set(visible):
            raise ValueError('Held ticker disappeared from the market observation')
        ranks = {ticker:rank for rank,ticker in enumerate(ranking)}
        prices = arrays['execution'][:,second]
        can_close = arrays['closeable'][:,second]
        if any(not can_close[lot.ticker_index] for lot in lots):
            raise ValueError('Open lot has no certified current liquidation price')
        equity_before = cash+sum(lot.quantity*float(prices[lot.ticker_index,1]) for lot in lots)
        state = dict(index=index,time_us=time_us,second=second,visible=visible,
            rank=[ranks[i] for i in visible],held=[i in held for i in visible],
            starting_lots=tuple(lots),cash=cash,equity=equity_before,terminal=terminal)
        starting = list(lots)
        used_sells: set[int] = set()
        previous_token = 0
        if terminal:
            if len(starting) > max_orders:
                raise ValueError('Terminal liquidation exceeds the action grid')
            for lot in starting:
                cash += lot.quantity*float(prices[lot.ticker_index,1])
                sells += 1
                forced += 1
            lots.clear()
        else:
            select = prepare(state)
            for action_step in range(max_orders):
                mask = np.zeros(1+top_n+max_lots,dtype=np.bool_)
                mask[0] = True
                if cash+1e-7 >= step_cash and len(lots) < max_lots:
                    for slot,ticker in enumerate(visible):
                        current = prices[ticker]
                        volume = float(arrays['volume_60s'][ticker,second])
                        trades = np.expm1(float(arrays['features'][ticker,second,TRADES]))
                        mask[1+slot] = bool(can_close[ticker] and current[0] > 0 and current[2] > 0
                            and volume >= minimum_volume and trades+1e-3 >= minimum_trades)
                for lot_index,lot in enumerate(starting):
                    mask[1+top_n+lot_index] = lot_index not in used_sells and bool(can_close[lot.ticker_index])
                token = int(select(action_step,mask,previous_token))
                if token < 0 or token >= len(mask) or not mask[token]:
                    raise ValueError('Policy selected an infeasible order')
                if token == 0:
                    break
                if token <= top_n:
                    ticker = visible[token-1]
                    current = prices[ticker]
                    quantity = step_cash/float(current[2])
                    cash -= step_cash
                    lots.append(Lot(ticker,quantity,float(current[0]),time_us))
                    lots.sort(key=lambda lot:(tickers[lot.ticker_index],lot.entry_us,
                        lot.entry_price,lot.quantity))
                    buys += 1
                else:
                    lot_index = token-top_n-1
                    lot = starting[lot_index]
                    lots.remove(lot)
                    cash += lot.quantity*float(prices[lot.ticker_index,1])
                    used_sells.add(lot_index)
                    sells += 1
                previous_token = token
        equity = cash+sum(lot.quantity*float(prices[lot.ticker_index,1]) for lot in lots)
        if not np.isfinite(equity) or cash < -1e-5:
            raise ValueError('Replay account became invalid')
        peak = max(peak,equity)
        max_drawdown = max(max_drawdown,(peak-equity)/peak)
        previous_equity = equity
    complete = bool(arrays['done'][rows-1])
    if complete and lots:
        raise ValueError('Terminal replay has open positions')
    return dict(seconds=rows,complete=complete,initial_cash=initial,
        terminal_cash=cash if complete else None,terminal_equity=previous_equity,
        profit=previous_equity-initial,profit_to_cash=(previous_equity-initial)/initial,
        max_drawdown=max_drawdown,buys=buys,sells=sells,forced_liquidations=forced,
        open_lots=len(lots))
