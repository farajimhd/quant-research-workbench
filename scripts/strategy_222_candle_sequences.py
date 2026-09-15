"""Past-only sequence features from the shared structural detector's rows.

Labels describe evidence, not predicted returns. This extractor does not accept
hindsight outcomes, ticker identity, entry times, or trading-rule parameters.
The caller must verify the detector stream's source/configuration provenance.
"""
from collections import Counter, deque
from math import isfinite

from src.market_engine.structural_detector import VERSION


FAMILIES = ('movement', 'regime', 'geometry', 'interaction', 'break_lifecycle',
    'retest_lifecycle', 'structural_progression', 'correction_recovery',
    'pressure', 'volume', 'momentum')


class CandleSequences:
    def __init__(self, *, candle_seconds, windows=(3, 5, 10)):
        if not isfinite(candle_seconds) or candle_seconds <= 0:
            raise ValueError('Candle duration must be finite and positive')
        if not windows or any(type(n) is not int or not 2 <= n <= 100 for n in windows):
            raise ValueError('Sequence windows must contain 2 to 100 candles')
        self.seconds = candle_seconds
        self.windows = tuple(sorted(set(windows)))
        self.rows = deque(maxlen=max(self.windows))
        self.session = None
        self.last_end = None

    def observe(self, row, *, session, observed_at):
        bar = row['candle']
        if row['contract'] != VERSION or not session:
            raise ValueError('Missing session or unsupported detector contract')
        values = [observed_at, row['effective_at'], *[bar[k] for k in
            ('time', 'end', 'open', 'high', 'low', 'close')]]
        if not all(type(v) in (int, float) and isfinite(v) for v in values):
            raise ValueError('Nonfinite sequence input')
        if (bar['end'] != row['effective_at'] or bar['end'] > observed_at
                or abs(bar['end']-bar['time']-self.seconds) > 1e-7):
            raise ValueError('Incomplete candle or wrong timeframe')
        if not 0 < bar['low'] <= min(bar['open'], bar['close']) <= max(bar['open'], bar['close']) <= bar['high']:
            raise ValueError('Invalid candle geometry')
        if self.last_end is not None and bar['time'] < self.last_end:
            raise ValueError('Duplicate or overlapping sequence candle')
        reset = (session != self.session or row['gap_before']
            or self.last_end is not None and bar['time'] != self.last_end)
        if reset:
            self.rows.clear()
        tags = {f'{family}:{label}' for family in FAMILIES
            for label in row['labels'][family]}
        shape = row['candle_shape']
        red_tail = bar['close'] < bar['open'] and 'upper_tail' in shape['tags']
        # Store detached, bounded values. Later detector mutations cannot alter history.
        self.rows.append(dict(tags=tags, state=row['state'], red_tail=red_tail,
            close=bar['close'], low=bar['low'], high=bar['high'],
            upper_tail=shape['upper_tail_fraction'], lower_tail=shape['lower_tail_fraction'],
            close_location=shape['close_location']))
        self.session, self.last_end = session, bar['end']
        history = list(self.rows)
        output = {}
        for n in self.windows:
            if len(history) < n:
                continue
            window = history[-n:]
            prefix = f'candles_{n}.'
            counts = Counter(tag for item in window for tag in item['tags'])
            for tag, count in sorted(counts.items()):
                output[prefix+'label_fraction.'+tag] = count/n
            transitions = Counter(f"{a['state']}->{b['state']}" for a,b in zip(window,window[1:]))
            for transition, count in sorted(transitions.items()):
                output[prefix+'movement_transition_fraction.'+transition] = count/(n-1)
            output[prefix+'red_upper_tail_fraction'] = sum(r['red_tail'] for r in window)/n
            run = 0
            for item in reversed(window):
                if not item['red_tail']:
                    break
                run += 1
            output[prefix+'red_upper_tail_run'] = run
            for field in ('upper_tail', 'lower_tail', 'close_location'):
                output[prefix+field+'_mean'] = sum(r[field] for r in window)/n
            output[prefix+'higher_low_fraction'] = sum(b['low']>a['low'] for a,b in zip(window,window[1:]))/(n-1)
            output[prefix+'lower_high_fraction'] = sum(b['high']<a['high'] for a,b in zip(window,window[1:]))/(n-1)
            path = sum(abs(b['close']-a['close']) for a,b in zip(window,window[1:]))
            output[prefix+'signed_close_efficiency'] = (window[-1]['close']-window[0]['close'])/path if path else 0.
        return dict(features=output, evidence=dict(contract=VERSION, candle_seconds=self.seconds,
            observed_at=observed_at, through=bar['end'], source_age_seconds=observed_at-bar['end'],
            available_candles=len(history), reset=bool(reset),
            complete_windows=[n for n in self.windows if len(history)>=n],
            sparse_semantics='Absent labels and transitions mean zero only within a complete window.'))
