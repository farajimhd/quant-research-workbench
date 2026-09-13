"""Causal setup entry and position-owned consolidation breakout phase."""
from copy import deepcopy


def observe(state, market, settings, fresh):
    if state.get('session') != market.get('session'):
        state.clear()
        state.update(session=market.get('session'), bars=[])
    if not fresh or not market.get('bar'):
        return
    bar = market['bar']
    if bar['end'] <= state.get('at', 0):
        return
    bars = state['bars']
    if bars and bars[-1]['end'] != bar['time']:
        bars = []
    bars = [b for b in bars if b['end'] > bar['time'] - settings['setup_range_seconds']]
    state['range'] = (dict(high=max(b['high'] for b in bars), low=min(b['low'] for b in bars),
        start=bars[0]['time'], end=bars[-1]['end'], count=len(bars))
        if len(bars) >= settings['setup_minimum_bars'] else None)
    state.update(at=bar['end'], bars=[*bars, deepcopy(bar)])
    if state.get('episode') != market.get('episode'):
        state.update(episode=market.get('episode'), episode_high=None, episode_low=None, body_high=None)
    if market.get('episode') is not None:
        state['episode_high'] = max(state.get('episode_high') or bar['high'], bar['high'])
        state['episode_low'] = min(state.get('episode_low') or bar['low'], bar['low'])
        state['body_high'] = max(state.get('body_high') or 0, bar['open'], bar['close'])


def phase(entry, market, fresh):
    setup = entry.get('setup')
    if not setup or not fresh or setup['phase'] == 'post_breakout':
        return False
    bar = market['bar']
    if (bar['end'] > entry['confirmed_at'] and bar['close'] >= bar['open']
            and bar['close'] > setup['breakout_threshold']):
        setup.update(phase='post_breakout', breakout_at=bar['end'])
        return True
    return False


def evidence(state, entry):
    return dict(**{k:state.get(k) for k in ('episode', 'episode_high', 'episode_low', 'body_high')},
        **deepcopy(entry.get('setup') or dict(phase='seeking_setup', range=state.get('range'))))
