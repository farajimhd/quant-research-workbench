"""Causal policy-driven V6 research environment with explicit fill scenarios."""
from dataclasses import dataclass
import math
from research.rl_trading.v6.oms import BracketAccount, Quote
from research.rl_trading.v6.account_observation import observe_account
from research.rl_trading.v6.replay_metrics import ReplayJournal


@dataclass(frozen=True)
class ObservedOutcome:
    listing: int
    action: int
    requested_fraction: float
    filled_fraction: float
    net_over_equity: float


class BracketEnvironment:
    """Only source evidence updates holdings. Actor receives completed marks.

    `source` is an execution-only provider, never passed to the actor. Entry
    is a first-following-100ms IOC attempt; no fresh quote means non-fill.
    Manual/stop exits retry across observed quotes, preserving remainders.
    """
    def __init__(self, tickers, source, *, config=None, price_increment=.0001,
                 luld=None, risk_penalty=None):
        from research.rl_trading.v2.config import Config
        self.account = BracketAccount(config=config or Config())
        self.source, self.tickers = source, tuple(tickers)
        self.by_ticker = {t:i for i,t in enumerate(tickers)}
        if len(self.by_ticker)!=len(tickers) or price_increment<=0:
            raise ValueError('Ambiguous market identity or research price grid')
        self.price_increment = price_increment
        self.entries, self.exits = {}, {}
        self.clock_us = None
        self.marks = {}
        self.journal = ReplayJournal(self.account)
        self.missing_bracket_extrema = 0
        self.consumed_display = {}
        from research.rl_trading.v6.luld import RiskPenalty
        self.luld = luld
        self.risk_penalty = risk_penalty or RiskPenalty()
        self.risk_metrics = {'halt_onset_penalty':0., 'halt_duration_penalty':0.,
            'terminal_exposure_penalty':0., 'trapped_position_seconds':0.,
            'trapped_position_events':0, 'luld_blocked_execution_buckets':0}
        self._charged_halts = set()
        self._terminal_charged = False
        self.journal.risk_metrics = self.risk_metrics

    @property
    def shaping_penalty(self):
        return sum(self.risk_metrics[key] for key in
            ('halt_onset_penalty','halt_duration_penalty','terminal_exposure_penalty'))

    def _halt_cost(self, begin, end):
        if self.luld is None:
            return
        for ticker,position in self.account.positions.items():
            price=self.marks.get(ticker,(position.entry_price,begin))[0]
            exposure=position.shares*price/self.account.initial_cash
            for pause_start,seconds in self.luld.trapped_intervals(ticker,begin,end):
                key=(ticker,position.entry_us,pause_start)
                if key not in self._charged_halts:
                    self.risk_metrics['halt_onset_penalty'] += self.risk_penalty.halt_entry*exposure
                    self.risk_metrics['trapped_position_events'] += 1
                    self._charged_halts.add(key)
                self.risk_metrics['halt_duration_penalty'] += self.risk_penalty.halt_per_minute*exposure*seconds/60
                self.risk_metrics['trapped_position_seconds'] += seconds

    def terminal_cost(self):
        if not self._terminal_charged:
            exposure=sum(p.shares*self.marks.get(t,(p.entry_price,0))[0]
                         for t,p in self.account.positions.items())/self.account.initial_cash
            self.risk_metrics['terminal_exposure_penalty'] += self.risk_penalty.terminal_exposure*exposure
            self._terminal_charged = True

    def _quote_capacity(self, ticker, quote, side):
        if not quote.fresh():
            return quote,None
        price = quote.ask if side=='ask' else quote.bid
        key = (ticker,quote.quote_us,side,price)
        consumed = self.consumed_display.get(key,0)
        if side=='ask':
            return Quote(quote.bucket_end_us,quote.quote_us,quote.bid,quote.ask,
                         quote.bid_size,max(0.,quote.ask_size-consumed),quote.valid),key
        return Quote(quote.bucket_end_us,quote.quote_us,quote.bid,quote.ask,
                     max(0.,quote.bid_size-consumed),quote.ask_size,quote.valid),key

    @property
    def pending_entries(self):
        return frozenset(self.entries)

    @property
    def pending_exits(self):
        return frozenset(self.exits)

    @property
    def reserved_cash(self):
        return math.fsum(order[2] for order in self.entries.values())

    def observation(self, clock_us):
        return observe_account(self.account,self.tickers,self.marks,
                               close_us=clock_us,queue=self)

    def _equity(self):
        return self.account.marked_equity({t:p for t,(p,_) in self.marks.items()})

    def advance(self, clock_us):
        if self.clock_us is None:
            self.clock_us = clock_us
            return ()
        if clock_us <= self.clock_us:
            raise ValueError('Environment clocks must advance')
        self._halt_cost(self.clock_us,clock_us)
        touched = set(self.entries)|set(self.account.positions)
        buckets = self.source.buckets(self.clock_us,clock_us,touched) if touched else ()
        outcomes = []
        def outcome(ticker, action, fraction, before_orders, before_closed, equity):
            new_orders = self.account.orders[before_orders:]
            fills = sum(r['filled_shares'] for r in new_orders)
            requested = sum(r['requested_shares'] for r in new_orders)
            net = sum(r['net_pnl'] for r in self.account.closed[before_closed:])
            outcomes.append(ObservedOutcome(self.by_ticker[ticker],action,fraction,
                fills/max(requested,1),net/max(equity,1e-9)))
        # First-bucket IOC attempts also exist when that bucket has no row.
        arrivals = {(item.close_us,item.ticker):item for item in buckets}
        for ticker,(decision,close,budget,fraction) in self.entries.items():
            arrival = ((decision//100_000)+1)*100_000
            if arrival <= clock_us and (arrival,ticker) not in arrivals:
                from research.rl_trading.v6.environment_source import ExecutionBucket
                arrivals[(arrival,ticker)] = ExecutionBucket(ticker,arrival,None,None,None)
        by_clock = {}
        for (boundary,t),item in arrivals.items():
            by_clock.setdefault(boundary, []).append(item)
        capacities = {}
        for (clock,ticker), bucket in sorted(arrivals.items()):
            before_orders,before_closed = len(self.account.orders),len(self.account.closed)
            equity = self._equity()
            if self.luld is not None:
                # Never use stale quotes through a modeled pause or permit a
                # fill outside its causal band. Pending exits keep remainders.
                order=self.entries.get(ticker)
                price=(bucket.quote.ask if order is not None else bucket.quote.bid) if bucket.quote else None
                blocked=self.luld.blocked(ticker,clock) or (
                    price is not None and not self.luld.executable(ticker,clock,price))
                if blocked:
                    self.risk_metrics['luld_blocked_execution_buckets'] += 1
                    if order is not None and clock == ((order[0]//100_000)+1)*100_000:
                        self.entries.pop(ticker)
                        self.account._record(action='enter_long',ticker=ticker,clock=clock,
                            requested=0,filled=0,price=None,fee=0.,reason='modeled_luld_restriction')
                        outcome(ticker,1,order[3],before_orders,before_closed,equity)
                    continue
            order = self.entries.get(ticker)
            if order is not None and clock == ((order[0]//100_000)+1)*100_000:
                decision,close,budget,fraction = self.entries.pop(ticker)
                quote = bucket.quote or Quote(clock,0,0,0,0,0,False)
                quote,capacity_key = self._quote_capacity(ticker,quote,'ask')
                filled = self.account.enter_long(ticker,decision_us=decision,decision_close=close,
                                        budget=budget,quote=quote)
                if capacity_key is not None:
                    self.consumed_display[capacity_key] = self.consumed_display.get(capacity_key,0)+filled
                outcome(ticker,1,fraction,before_orders,before_closed,equity)
                continue  # Cannot trigger a child in entry bucket.
            if ticker not in self.account.positions:
                continue
            position = self.account.positions[ticker]
            exit_order = self.exits.get(ticker)
            if exit_order is not None and bucket.quote is not None and clock>exit_order[0]:
                quote,capacity_key = self._quote_capacity(ticker,bucket.quote,'bid')
                if capacity_key is not None and quote.bid_size<=0:
                    filled = 0
                    self.account._record(action=exit_order[1],ticker=ticker,clock=clock,
                        requested=position.shares,filled=0,price=None,fee=0.,
                        reason='displayed_bid_already_consumed_at_quote_timestamp')
                else:
                    filled = self.account.exit_long(ticker,decision_us=max(exit_order[0],position.last_action_us),
                        quote=quote,action=exit_order[1])
                if capacity_key is not None:
                    self.consumed_display[capacity_key] = self.consumed_display.get(capacity_key,0)+filled
                outcome(ticker,2,1.,before_orders,before_closed,equity)
                if ticker not in self.account.positions:
                    self.exits.pop(ticker)
                continue
            if exit_order is not None or clock<=position.last_action_us:
                continue
            if bucket.high is None or bucket.low is None:
                if position.stop is not None or position.target is not None:
                    self.missing_bracket_extrema += 1
                continue
            stop = position.stop is not None and bucket.low <= position.stop
            target = position.target is not None and bucket.high >= position.target
            if target and stop:
                self.account.target_bucket(ticker,clock_us=clock,price_level_volume_cap=0,stop_touched=True)
                self.exits[ticker] = (clock,'stop_market')
                outcome(ticker,2,1.,before_orders,before_closed,equity)
            elif stop:
                self.account.stop_trigger(ticker,clock_us=clock)
                self.exits[ticker] = (clock,'stop_market')
                outcome(ticker,2,1.,before_orders,before_closed,equity)
            elif target:
                if self.luld is not None and not self.luld.executable(ticker,clock,position.target):
                    self.risk_metrics['luld_blocked_execution_buckets'] += 1
                    continue
                if hasattr(self.source, 'target_capacities'):
                    if clock not in capacities:
                        # Read only actual unambiguous touches at this boundary.
                        # Other tickers' fills cannot change these price levels;
                        # account mutations below retain their original order.
                        targets = {}
                        for item in by_clock[clock]:
                            p = self.account.positions.get(item.ticker)
                            if (p is None or item.ticker in self.exits or
                                clock<=p.last_action_us or item.high is None or item.low is None or
                                p.target is None or item.high<p.target or
                                (p.stop is not None and item.low<=p.stop)):
                                continue
                            if self.luld is not None:
                                quote_price=item.quote.bid if item.quote else None
                                if (self.luld.blocked(item.ticker,clock) or
                                    (quote_price is not None and not self.luld.executable(item.ticker,clock,quote_price)) or
                                    not self.luld.executable(item.ticker,clock,p.target)):
                                    continue
                            targets[item.ticker]=p.target
                        capacities[clock] = self.source.target_capacities(clock, targets)
                    capacity = capacities[clock][ticker]
                else:
                    capacity = self.source.target_capacity(ticker,clock,position.target)
                self.account.target_bucket(ticker,clock_us=clock,price_level_volume_cap=capacity)
                outcome(ticker,2,1.,before_orders,before_closed,equity)
        self.clock_us = clock_us
        self.consumed_display = {k:v for k,v in self.consumed_display.items() if k[1]>=clock_us-1_000_000}
        return tuple(outcomes)

    def submit(self, token, parameter, *, clock_us, order_index, holdings, wait_hold=False):
        n,h = len(self.tickers),len(holdings)
        if not 0 <= token < 1+n+(4 if wait_hold else 3)*h:
            raise ValueError('Policy token outside environment action contract')
        if token==0:
            return None
        if wait_hold and token >= 1+n+3*h:
            slot = token-(1+n+3*h)
            ticker = self.tickers[int(holdings[slot])]
            if ticker not in self.account.positions:
                raise ValueError('HOLD requires an open position')
            return None
        if token<=n:
            ticker = self.tickers[token-1]
            if ticker in self.entries or ticker in self.account.positions:
                raise ValueError('Policy entry bypassed held/pending mask')
            budget = max(0.,self.account.cash-self.reserved_cash)*parameter
            self.entries[ticker] = (clock_us,self.marks[ticker][0],budget,parameter)
            return None
        action,slot = divmod(token-1-n,h)
        ticker = self.tickers[int(holdings[slot])]
        if action==0:
            self.force_exit(ticker,clock_us)
            return None
        position = self.account.positions[ticker]
        try:
            raw = position.entry_price*math.exp(-parameter if action==1 else parameter)
            price = math.floor(raw/self.price_increment+1e-9)*self.price_increment
            if action==1:
                self.account.set_stop(ticker,price=price,clock_us=clock_us)
            else:
                self.account.set_target(ticker,price=price,clock_us=clock_us)
            filled = 1.
        except (ValueError,OverflowError):
            self.account._record(action='rejected_bracket',ticker=ticker,clock=clock_us,
                requested=0,filled=0,price=None,fee=0.,reason='invalid_predicted_price_geometry')
            filled = 0.
        return ObservedOutcome(self.by_ticker[ticker],action+2,1.,filled,0.)

    def force_exit(self, ticker, clock_us):
        if ticker not in self.exits:
            self.exits[ticker] = (clock_us,'exit_long')
            self.account.positions[ticker].target = None
            self.account.positions[ticker].stop_pending = True
