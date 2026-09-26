"""Pack one Phase 3 teacher path against the identical causal market slots."""
from __future__ import annotations

import json

import numpy as np

from research.rl_trading.v1.features import FEATURE_NAMES, SECONDS

PRICE_AVAILABLE = FEATURE_NAMES.index('price_available')
TRADES_60S = FEATURE_NAMES.index('log_trades_60s')


def pack(trajectory: list[dict], bank: np.ndarray, volumes: np.ndarray,
         execution: np.ndarray,
         tickers: list[str], *, left_us: int, top_n: int, max_lots: int,
         max_orders: int, allocation_step: float, initial_cash: float,
         min_volume: float, min_trades: int) -> dict[str,np.ndarray]:
    """Use original ticker identity for history; encode each order as a slot or lot.

    Action token 0 stops. 1..N buys one allocation unit in a ticker slot.
    N+1..N+max_lots sells one held lot. Orders after stop are stop padding.
    """
    count = len(trajectory)
    universe = len(tickers)
    if (count == 0 or bank.shape != (universe,SECONDS,len(FEATURE_NAMES))
            or volumes.shape != (universe,SECONDS)
            or execution.shape != (universe,SECONDS,3) or top_n < max_lots
            or len(tickers) != len(set(tickers)) or max_orders < 1):
        raise ValueError('Teacher packing requires a complete identified feature bank')
    lookup = {ticker:i for i,ticker in enumerate(tickers)}
    lexical = np.argsort(np.asarray(tickers,dtype='U'),kind='stable')
    action_count = 1+top_n+max_lots
    result = dict(
        time_us=np.empty(count,dtype=np.int64),
        slots=np.full((count,top_n),-1,dtype=np.int32),
        rank=np.full((count,top_n),universe,dtype=np.int32),
        held_slots=np.zeros((count,top_n),dtype=np.bool_),
        actions=np.zeros((count,max_orders),dtype=np.int16),
        action_mask=np.zeros((count,max_orders,action_count),dtype=np.bool_),
        lots=np.zeros((count,max_lots,3),dtype=np.float32),
        lot_slots=np.full((count,max_lots),-1,dtype=np.int16),
        account=np.zeros((count,3),dtype=np.float32),
        reward=np.empty(count,dtype=np.float32),
        return_to_go=np.empty(count,dtype=np.float32),
        done=np.empty(count,dtype=np.bool_),
    )
    prior: list[dict] = []
    previous_time = None
    for start in range(0,count,256):
        stop = min(count,start+256)
        positions = np.asarray([(int(row['time_us'])-left_us)//1_000_000
                                for row in trajectory[start:stop]],dtype=np.int64)
        if np.any(positions < 0) or np.any(positions >= SECONDS):
            raise ValueError('Teacher time lies outside the one-second market session')
        ranked = lexical[np.argsort(-np.asarray(volumes[lexical[:,None],positions[None,:]]).T,
                                    axis=1,kind='stable')]
        for relative,row in enumerate(trajectory[start:stop]):
            index = start+relative
            time_us = int(row['time_us'])
            if previous_time is not None and time_us != previous_time+1_000_000:
                raise ValueError('Teacher trajectory must have every consecutive second')
            previous_time = time_us
            result['time_us'][index] = time_us
            second = int(positions[relative])
            if len(prior) > max_lots:
                raise ValueError('Teacher has too many held lots')
            held = {lookup[item['ticker']] for item in prior}
            visible = sorted(held,key=lambda value:tickers[value])
            visible.extend(value for value in ranked[relative] if value not in held
                           and len(visible) < top_n)
            visible = visible[:top_n]
            result['slots'][index,:len(visible)] = visible
            full_rank = np.empty(universe,dtype=np.int32)
            full_rank[ranked[relative]] = np.arange(universe,dtype=np.int32)
            result['rank'][index,:len(visible)] = full_rank[visible]
            location = {tickers[value]:slot for slot,value in enumerate(visible)}
            for slot,value in enumerate(visible):
                result['held_slots'][index,slot] = value in held
            current_equity = float(row['cash_before'])+sum(
                float(lot['quantity'])*float(execution[lookup[lot['ticker']],second,1])
                for lot in prior)
            if not np.isfinite(current_equity) or current_equity < 0:
                raise ValueError('Teacher has no valid current account mark')
            result['account'][index] = (float(row['cash_before'])/initial_cash,
                current_equity/initial_cash,second/(SECONDS-1))
            for lot_index,lot in enumerate(prior):
                result['lot_slots'][index,lot_index] = location[lot['ticker']]
                result['lots'][index,lot_index] = (float(lot['quantity']),
                    float(lot['entry_price']),max(0.,(time_us-int(lot['entry_us']))/1e6)/3600)
            available = [bool(bank[value,second,PRICE_AVAILABLE]) for value in visible]
            liquid = [float(volumes[value,second]) >= min_volume and
                      np.expm1(float(bank[value,second,TRADES_60S]))+1e-3 >= min_trades
                      for value in visible]
            legs = json.loads(row['action_legs_json'])
            if len(legs) > max_orders:
                raise ValueError('Teacher exceeds the configured order grid')
            working = [dict(item) for item in prior]
            cash = float(row['cash_before'])
            used_sell_lots: set[int] = set()
            for step in range(max_orders):
                mask = result['action_mask'][index,step]
                mask[0] = True
                if not row['done'] and cash+1e-6 >= allocation_step and len(working) < max_lots:
                    for slot in range(len(visible)):
                        mask[slot+1] = available[slot] and liquid[slot]
                for lot_index,lot in enumerate(prior):
                    slot = location[lot['ticker']]
                    mask[1+top_n+lot_index] = (lot_index not in used_sell_lots
                        and available[slot])
                if step >= len(legs):
                    continue
                leg = legs[step]
                ticker = leg['ticker']
                if ticker not in location:
                    raise ValueError('Teacher action is outside causal top-N-plus-held market')
                if leg['action'] == 'buy':
                    token = 1+location[ticker]
                    working.append(dict(ticker=ticker,quantity=leg['quantity'],
                        entry_price=leg['price'],entry_us=time_us))
                    cash -= float(leg['capital'])
                elif leg['action'] == 'sell':
                    matches = [j for j,lot in enumerate(prior) if j not in used_sell_lots
                        and lot['ticker'] == ticker and int(lot['entry_us']) == int(leg['entry_us'])
                        and abs(float(lot['quantity'])-float(leg['quantity'])) < 1e-6]
                    if not matches:
                        raise ValueError('Teacher sale does not identify an existing lot')
                    lot_index = matches[0]
                    used_sell_lots.add(lot_index)
                    token = 1+top_n+lot_index
                    working.remove(prior[lot_index])
                    cash += float(leg['quantity'])*float(leg['price'])
                else:
                    raise ValueError('Unknown teacher action')
                if not mask[token]:
                    raise ValueError('Teacher action violates its causal feasibility mask')
                result['actions'][index,step] = token
            after = json.loads(row['positions_after_json'])
            if (len(after) != len(working) or
                    sorted((x['ticker'],int(x['entry_us']),round(float(x['quantity']),8)) for x in after)
                    != sorted((x['ticker'],int(x['entry_us']),round(float(x['quantity']),8)) for x in working)):
                raise ValueError('Teacher actions do not reproduce held positions')
            prior = after
            result['reward'][index] = float(row['reward'])/initial_cash
            result['return_to_go'][index] = float(row['return_to_go'])/initial_cash
            result['done'][index] = bool(row['done'])
    if not result['done'][-1] or np.any(result['done'][:-1]) or prior:
        raise ValueError('Teacher must end with exactly one flat terminal state')
    return result
