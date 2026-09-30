"""Causal research LULD bands and quote-observed pause scenarios.

This is not official SIP band/halt evidence. Opening uses a pinned prior
regular-session last sale (not an auction), tier is supplied explicitly, and
reopening uses a modeled five-minute timer. Raw source arrays never enter the
actor. Only changes whose availability clock has passed may be read.
"""
from dataclasses import dataclass
import numpy as np
import polars as pl

VERSION = 'rl-v6-modeled-luld-100ms-v2'
STEP = 100_000


def project(start_us, end_us, trades, quotes, *, previous_close, tier):
    """Vectorized rolling trade mean on [T] 100ms slots; sparse output.

    trades: bucket_us, price_sum (dollars), count. quotes: bucket_us,
    quote_us, bid, ask. Inputs contain completed buckets, with unique keys.
    Stateful reference refresh/pause timers are intentionally small ordered
    loops; never loop through transactions or dataframe rows.
    """
    if tier not in (1, 2) or not np.isfinite(previous_close) or previous_close <= 0:
        raise ValueError('Explicit tier and causal positive prior close required')
    if end_us <= start_us or (end_us-start_us) % STEP:
        raise ValueError('Invalid regular-session grid')
    clocks = np.arange(start_us+STEP, end_us+1, STEP, dtype=np.int64)
    n = len(clocks)
    sums, counts = np.zeros(n), np.zeros(n, dtype=np.int64)
    bid, ask, fresh = np.zeros(n), np.zeros(n), np.zeros(n, dtype=bool)
    for frame, names in ((trades, ('price_sum', 'count')),
                         (quotes, ('bid', 'ask'))):
        if frame.height == 0:
            continue
        positions = np.searchsorted(clocks, frame['bucket_us'].to_numpy())
        if (np.any(positions >= n) or np.any(clocks[positions] != frame['bucket_us'].to_numpy())
                or len(np.unique(positions)) != len(positions)):
            raise ValueError('Duplicate or off-grid evidence')
        if frame is trades:
            sums[positions] = frame[names[0]].to_numpy()
            counts[positions] = frame[names[1]].to_numpy()
            if np.any(sums < 0) or np.any(counts < 0) or not np.isfinite(sums).all():
                raise ValueError('Malformed eligible trade aggregate')
        else:
            bid[positions], ask[positions] = (frame[name].to_numpy() for name in names)
            quote_us = frame['quote_us'].to_numpy()
            fresh[positions] = ((quote_us > 0) & (quote_us <= clocks[positions]) &
                (clocks[positions]-quote_us <= STEP) & (bid[positions] > 0) &
                (ask[positions] >= bid[positions]))
    cs, cc = np.r_[0., np.cumsum(sums)], np.r_[0, np.cumsum(counts)]
    left = np.maximum(np.arange(n)+1-300_000_000//STEP, 0)
    rolling_count = cc[1:]-cc[left]
    means = np.divide(cs[1:]-cs[left], rolling_count,
                      out=np.full(n, np.nan), where=rolling_count > 0)
    reference = np.empty(n)
    current, refreshed = float(previous_close), start_us
    frozen = False
    # 30s reference checks, 1% refresh threshold; no future prices.
    refresh_slots = 30_000_000//STEP
    for begin in range(0, n, refresh_slots):
        stop = min(begin+refresh_slots, n)
        for i in range(begin, stop):
            if not frozen and clocks[i]-refreshed >= 30_000_000 and np.isfinite(means[i]) and abs(means[i]/current-1) >= .01:
                current, refreshed = float(means[i]), int(clocks[i])
            reference[i] = current
            width = (.20 if previous_close >= .75 else .75) if previous_close <= 3 else (.05 if tier == 1 else .10)
            distance = min(current*width,.15) if previous_close < .75 else current*width
            if (tier == 1 or previous_close <= 3) and clocks[i] > end_us-25*60_000_000:
                distance *= 2
            low,high=np.floor((current-distance)*100+.5)/100,np.floor((current+distance)*100+.5)/100
            frozen=bool(fresh[i] and (abs(ask[i]-low)<.00005 or abs(bid[i]-high)<.00005))
    width = np.full(n, .05 if tier == 1 else .10)
    if previous_close <= 3:
        width[:] = .20 if previous_close >= .75 else .75
    distance = reference*width
    if previous_close < .75:
        distance = np.minimum(distance, .15)
    if tier == 1 or previous_close <= 3:
        distance[clocks > end_us-25*60_000_000] *= 2
    lower = np.floor((reference-distance)*100+.5)/100
    upper = np.floor((reference+distance)*100+.5)/100
    # Equality identifies limit state. Crossing alone is a straddle state,
    # not evidence of an exchange-declared pause.
    limit = fresh & ((np.abs(ask-lower) < .00005) | (np.abs(bid-upper) < .00005))
    side = np.where(fresh & (np.abs(ask-lower)<.00005),1,
                    np.where(fresh & (np.abs(bid-upper)<.00005),2,0))
    reset = np.r_[True, (side[1:] != side[:-1]) | (lower[1:] != lower[:-1]) | (upper[1:] != upper[:-1])]
    run_starts = np.maximum.accumulate(np.where(~limit,np.arange(n)+1,
                                  np.where(reset,np.arange(n),0)))
    duration = (np.arange(n)-run_starts)*STEP
    candidates = np.flatnonzero(limit & (duration >= 15_000_000))
    paused = np.zeros(n, dtype=bool)
    pause_start = np.zeros(n, dtype=np.int64)
    intervals, last_end = [], start_us
    for index in candidates:
        begin = int(clocks[index])
        if begin < last_end or begin >= end_us:
            continue
        finish = min(begin+300_000_000, end_us)
        if begin >= end_us-600_000_000:
            finish = end_us
        right = np.searchsorted(clocks, finish)
        paused[index:right] = True
        pause_start[index:right] = begin
        intervals.append((begin, finish))
        last_end = finish
    # Keep bandwidth/pause changes only; no redundant dense grid saved.
    changed = np.r_[True, (lower[1:] != lower[:-1]) | (upper[1:] != upper[:-1]) |
                    (paused[1:] != paused[:-1])]
    changes = pl.DataFrame({'available_us': clocks[changed], 'lower': lower[changed],
        'upper': upper[changed], 'paused': paused[changed], 'pause_start_us': pause_start[changed]})
    # Counts in the onset bucket describe trades *before* the newly available
    # pause state. Audit only buckets whose opening boundary was already
    # paused, retaining the reopening bucket because it includes prior trades.
    prior_paused = np.r_[False, paused[:-1]]
    return changes, {'eligible_trades': int(counts.sum()), 'quote_slots': int(fresh.sum()),
        'modeled_pauses': len(intervals), 'trade_buckets_during_modeled_pause': int(np.count_nonzero(counts[prior_paused])),
        'prior_close_kind': 'pinned_previous_regular_last_sale', 'tier': tier,
        'official_halt_evidence': False}


class LuldBook:
    """Sparse per-listing arrays, O(log changes) as-of lookup, no SQL in replay."""
    def __init__(self, frames, *, end_us=None):
        self.end_us = end_us
        self.rows = {ticker: (frame['available_us'].to_numpy(), frame)
                     for ticker, frame in frames.items()}
        events=[]
        for ticker,(_,frame) in self.rows.items():
            # Only pause transitions participate in the entry-mask index.
            last=False
            for clock,paused in frame.select('available_us','paused').iter_rows():
                if paused != last:
                    events.append((clock,ticker,paused)); last=paused
        self.pause_events=sorted(events)
        self.pause_cursor=0
        self.pause_clock=0
        self.active_pauses=set()

    def paused_tickers(self, clock):
        if clock < self.pause_clock:
            raise ValueError('Pause-mask index requires chronological clocks')
        self.pause_clock=clock
        if self.end_us is not None and clock >= self.end_us:
            self.active_pauses.clear()
            return frozenset()
        while self.pause_cursor < len(self.pause_events) and self.pause_events[self.pause_cursor][0]<=clock:
            _,ticker,paused=self.pause_events[self.pause_cursor]
            if paused: self.active_pauses.add(ticker)
            else: self.active_pauses.discard(ticker)
            self.pause_cursor+=1
        return frozenset(self.active_pauses)

    def state(self, ticker, clock):
        if self.end_us is not None and clock >= self.end_us:
            return None  # LULD bands do not apply in after-hours trading.
        item = self.rows.get(ticker)
        if item is None:
            return None
        clocks, frame = item
        index = int(np.searchsorted(clocks, clock, side='right'))-1
        return None if index < 0 else frame.row(index, named=True)

    def blocked(self, ticker, clock):
        state = self.state(ticker, clock)
        return bool(state and state['paused'])

    def executable(self, ticker, clock, price):
        state = self.state(ticker, clock)
        return state is None or (not state['paused'] and state['lower'] <= price <= state['upper'])

    def trapped_intervals(self, ticker, begin, end):
        item = self.rows.get(ticker)
        if item is None or end <= begin:
            return ()
        clocks, frame = item
        end = min(end, self.end_us) if self.end_us is not None else end
        left = max(0, int(np.searchsorted(clocks, begin, side='right'))-1)
        right = int(np.searchsorted(clocks, end, side='left'))
        intervals = []
        for index in range(left, right):
            row = frame.row(index, named=True)
            stop = min(end, int(clocks[index+1]) if index+1 < len(clocks) else end)
            start = max(begin, int(clocks[index]))
            if row['paused'] and stop > start:
                intervals.append((row['pause_start_us'], (stop-start)/1_000_000))
        return intervals


@dataclass(frozen=True)
class RiskPenalty:
    # Research shaping coefficients, independently logged, never commissions.
    halt_entry: float = .10
    halt_per_minute: float = .01
    terminal_exposure: float = .25

    def __post_init__(self):
        if any(not np.isfinite(v) or v < 0 for v in self.__dict__.values()):
            raise ValueError('Finite nonnegative risk penalties required')
