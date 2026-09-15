"""Lossless restart contract for replay's causal liquidity accumulator."""
from copy import deepcopy
from datetime import datetime
from math import isfinite

CONTRACT = 'historical-liquidity-1'
FIELDS = {'dollar_volume', 'share_volume', 'trade_buckets', 'trade_bucket_head',
          'volume_buckets', 'raw_authority', 'spread_bps', 'previous_bar_volume'}


def restore(payload):
    if not isinstance(payload, dict) or payload.get('contract') != CONTRACT:
        raise ValueError('Restart checkpoint lacks historical liquidity history; start a new run')
    if not isinstance(payload.get('tickers'), dict):
        raise ValueError('Invalid historical liquidity ticker states')
    result = deepcopy(payload['tickers'])
    for ticker, state in result.items():
        if not isinstance(ticker, str) or not ticker or not isinstance(state, dict) or set(state) - FIELDS:
            raise ValueError('Invalid historical liquidity state')
        for key in ('dollar_volume', 'share_volume', 'previous_bar_volume', 'spread_bps'):
            value = state.get(key)
            if key in ('dollar_volume', 'share_volume') or value is not None:
                if type(value) not in (float, int) or not isfinite(value) or value < 0:
                    raise ValueError('Invalid historical liquidity total or quote')
        if 'raw_authority' in state and type(state['raw_authority']) is not bool:
            raise ValueError('Invalid historical liquidity authority')
        buckets = []
        previous = None
        for stamp, count in state.get('trade_buckets', []):
            clock = datetime.fromisoformat(stamp)
            if clock.tzinfo is None or (previous is not None and clock < previous):
                raise ValueError('Invalid historical liquidity trade clock')
            if type(count) is not int or count < 0:
                raise ValueError('Invalid historical liquidity trade count')
            buckets.append((clock, count))
            previous = clock
        head = state.get('trade_bucket_head', 0)
        if type(head) is not int or not 0 <= head <= len(buckets):
            raise ValueError('Invalid historical liquidity bucket cursor')
        state['trade_buckets'] = buckets
        if 'volume_buckets' in state:
            volumes = []
            previous = None
            for second, volume in state['volume_buckets']:
                if (type(second) is not int or (previous is not None and second <= previous)
                        or type(volume) not in (float, int) or not isfinite(volume) or volume < 0):
                    raise ValueError('Invalid historical liquidity volume bucket')
                volumes.append((second, volume))
                previous = second
            state['volume_buckets'] = volumes
    return result


def checkpoint(states):
    tickers = deepcopy(states)
    for state in tickers.values():
        state['trade_buckets'] = [(stamp.isoformat(), count)
                                  for stamp, count in state.get('trade_buckets', [])]
    payload = {'contract': CONTRACT, 'tickers': tickers}
    restore(payload)
    return payload
