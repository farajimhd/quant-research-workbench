"""Causal cash-funded execution with next-second IOC orders and sticky forced exits."""
from collections import deque
import math
import numpy as np

from research.rl_trading.v2.config import Config, share_cap
from research.rl_trading.v2.fees import charges
from research.rl_trading.v2.market_status import TRADING, HALTED

ACCOUNT_FEATURES = 6
POSITION_FEATURES = 12


class TradingEnv:
    def __init__(self, session, config: Config, *, initial_cash=None):
        self.session, self.config = session, config
        self.initial = float(initial_cash if initial_cash is not None else config.initial_cash)
        if not math.isfinite(self.initial) or self.initial <= 0:
            raise ValueError('Initial capital must be positive and finite')
        self.reset()

    def reset(self):
        self.t = 0
        self.cash = self.initial
        self.quantity = np.zeros(self.session.n)
        self.basis = np.zeros(self.session.n)
        self.forced = np.zeros(self.session.n, dtype=bool)
        self.stop_price = np.zeros(self.session.n)
        self.target_price = np.zeros(self.session.n)
        self.exit_reason = np.zeros(self.session.n,dtype=np.int8)  # rank/session=1,stop=2,target=3
        self.entry_second = np.full(self.session.n,-1,dtype=np.int64)
        self.last_halt_second = np.full(self.session.n,-1,dtype=np.int64)
        self.recent = deque()
        self.recent_shares = np.zeros(self.session.n)
        self.peak = self.initial
        self.drawdown = self.reward_sum = 0.
        self.metrics = dict(fees=0., slippage_dollars=0., traded_notional=0.,
                            filled_orders=0, partial_orders=0, unfilled_orders=0,
                            forced_fills=0, requested_shares=0., filled_shares=0.,
                            commission=0.,sec=0.,taf=0.,cat=0.,venue=0.,
                            stop_fills=0,target_fills=0,halt_blocked_orders=0,
                            halted_position_seconds=0,unknown_position_seconds=0)
        self.last_fills = []
        self.done = False
        return self.observe()

    @property
    def equity(self):
        return float(self.cash + self.quantity @ self.session.arrays['prices'][:,self.t])

    def _membership(self):
        order = self.session.ranking(self.t, self.config)
        allowed = np.zeros(self.session.n, dtype=bool)
        allowed[order[:self.config.hold_rank]] = True
        self.forced |= (self.quantity > 0) & ~allowed
        cutoff = self.session.seconds-1-self.config.liquidation_buffer_seconds
        if self.t >= cutoff:
            self.forced |= self.quantity > 0
        self.forced &= self.quantity > 0
        self.exit_reason[self.forced & (self.exit_reason == 0)] = 1
        a = self.session.arrays
        self.last_halt_second[a['status'][:,self.t] == HALTED] = self.t
        # An armed threshold uses only the current completed price. Triggered
        # instructions execute in the next transition, never retroactively.
        executable_observation = a['fresh'][:,self.t] & (a['status'][:,self.t] == TRADING)
        stop = (self.quantity>0) & (self.stop_price>0) & (a['prices'][:,self.t] <= self.stop_price) & executable_observation
        target = (self.quantity>0) & (self.target_price>0) & (a['prices'][:,self.t] >= self.target_price) & executable_observation & ~stop
        self.exit_reason[stop & (self.exit_reason != 2)] = 2
        self.exit_reason[target & ~self.forced] = 3
        self.forced |= stop | target
        return order, cutoff

    def observe(self):
        order, cutoff = self._membership()
        visible = list(order[:self.config.hold_rank])
        present = set(visible)
        visible += [int(i) for i in self.session.lexical if self.quantity[i] > 0 and i not in present]
        # One masked placeholder keeps attention well-defined in an empty market.
        ids = np.asarray(visible or [0], dtype=np.int64)
        valid = np.full(len(ids), bool(visible))
        rank = np.full(self.session.n, self.session.n+1, dtype=float)
        rank[order] = np.arange(1,len(order)+1)
        a, equity = self.session.arrays, self.equity
        if equity <= 0 or not math.isfinite(equity):
            raise ValueError('Account equity is nonpositive or nonfinite')
        prices = a['prices'][ids,self.t]
        weights = self.quantity[ids]*prices/equity
        unrealized = np.divide(prices, self.basis[ids], out=np.ones(len(ids)), where=self.basis[ids]>0)-1
        position = np.column_stack((weights, unrealized,
            rank[ids]/self.config.hold_rank, self.forced[ids].astype(float),
            self.quantity[ids]/np.maximum(a['volume_60s'][ids,self.t],1),
            np.log1p(equity/np.maximum(prices*a['volume_60s'][ids,self.t],1)),
            self.recent_shares[ids]/np.maximum(a['volume_60s'][ids,self.t],1),
            np.where(self.quantity[ids]>0,(prices-self.stop_price[ids])/np.maximum(prices,1e-12),0),
            np.where(self.quantity[ids]>0,(self.target_price[ids]-prices)/np.maximum(prices,1e-12),0),
            a['status'][ids,self.t]/2.,
            np.where(self.last_halt_second[ids]>=0,np.minimum(self.t-self.last_halt_second[ids],3600)/3600.,-1.),
            np.where(self.entry_second[ids]>=0,(self.t-self.entry_second[ids])/(self.session.seconds-1),0))).astype(np.float32)
        position[~valid] = 0
        time = np.arange(self.t-self.config.history_seconds+1,self.t+1)
        market = np.asarray(a['features'][ids[:,None],np.maximum(time,0)[None,:]], dtype=np.float32).copy()
        market[:,time<0] = 0
        market[~valid] = 0
        # hold / buy / reduce / close; forced positions are execution-owned.
        mask = np.zeros((len(ids),4), dtype=bool)
        mask[:,0] = True
        mask[:,1] = (valid & (rank[ids] <= self.config.entry_rank) & ~self.forced[ids]
                      & (self.cash > 0) & (weights < self.config.max_ticker_weight)
                      & (self.t < cutoff) & (self.t < self.session.seconds-2))
        mask[:,2] = mask[:,3] = valid & (self.quantity[ids]>0) & ~self.forced[ids]
        return dict(ids=ids, valid=valid, market=market, position=position,
                    account=np.asarray([self.cash/equity, 1-self.cash/equity,
                        equity/self.initial-1, (self.peak-equity)/self.peak,
                        (self.session.seconds-1-self.t)/(self.session.seconds-1),
                        float((self.quantity*self.forced) @ a['prices'][:,self.t])/equity],dtype=np.float32),
                    action_mask=mask)

    def _slippage(self, ticker, quantity, previous):
        a, c = self.session.arrays, self.config
        volume = max(float(a['volume_60s'][ticker,previous]), 1.)
        left = max(0,previous-60)
        prices = a['prices'][ticker,left:previous+1]
        usable = prices > 0
        changes = np.diff(np.log(prices[usable]))
        volatility = float(np.std(changes)) if len(changes)>1 else 0.
        participation = (self.recent_shares[ticker]+quantity)/volume
        return c.base_slippage_ratio + c.impact_ratio*math.sqrt(participation) + c.volatility_slippage_ratio*volatility

    def _cost(self, ticker, quantity, side, previous):
        price = float(self.session.arrays['prices'][ticker,self.t])
        slip = self._slippage(ticker,quantity,previous)
        if not math.isfinite(slip) or slip >= 1:
            raise ValueError('Execution model outside calibrated domain: slippage >= 100%')
        fill = price*(1+side*slip)
        fee = sum(charges(quantity,fill,side,self.config).values())
        return fill, fee, slip

    def _execute(self, ticker, requested, side, previous, budget=None, bracket=None):
        a, c = self.session.arrays, self.config
        self.metrics['requested_shares'] += requested
        if a['execution_status'][ticker,self.t] != TRADING:
            self.metrics['halt_blocked_orders'] += 1
            self.metrics['unfilled_orders'] += 1
            return
        if not a['fresh'][ticker,self.t] or a['volume'][ticker,self.t] <= 0:
            self.metrics['unfilled_orders'] += 1
            return
        maximum = min(share_cap(float(a['prices'][ticker,previous])),
                      share_cap(float(a['prices'][ticker,self.t])))
        quantity = math.floor(min(requested, maximum,
                            c.max_volume_participation*float(a['volume'][ticker,self.t])))
        if side == 1 and quantity > 0:
            # Monotone total purchase cost, including minimum fees and size impact.
            available = min(self.cash, float(budget))
            lo, hi = 0, quantity
            while lo < hi:
                mid = (lo+hi+1)//2
                fill, fee, _ = self._cost(ticker,mid,side,previous)
                if mid*fill+fee <= available+1e-9:
                    lo = mid
                else:
                    hi = mid-1
            quantity = lo
        if quantity <= 0:
            self.metrics['unfilled_orders'] += 1
            return
        fill, fee, slip = self._cost(ticker,quantity,side,previous)
        if side == -1 and quantity*fill-fee+self.cash < -1e-9:
            self.metrics['unfilled_orders'] += 1
            return
        if side == 1:
            if self.quantity[ticker] == 0:
                if bracket is None:
                    raise ValueError('New positions require sampled stop and target distances')
                stop_ratio,target_ratio = bracket
                self.stop_price[ticker] = fill*(1-stop_ratio)
                self.target_price[ticker] = fill*(1+target_ratio)
                self.entry_second[ticker] = self.t
            self.basis[ticker] = (self.basis[ticker]*self.quantity[ticker]+quantity*fill+fee)/(self.quantity[ticker]+quantity)
            self.cash -= quantity*fill+fee
            self.quantity[ticker] += quantity
        else:
            self.quantity[ticker] -= quantity
            self.cash += quantity*fill-fee
            if self.quantity[ticker] == 0:
                self.basis[ticker] = 0
                self.stop_price[ticker] = self.target_price[ticker] = 0
                self.entry_second[ticker] = -1
        components = charges(quantity,fill,side,c)
        for name,cost in components.items():
            self.metrics[name] += cost
        self.metrics['fees'] += fee
        self.metrics['slippage_dollars'] += quantity*float(a['prices'][ticker,self.t])*slip
        self.metrics['traded_notional'] += quantity*float(a['prices'][ticker,self.t])
        self.metrics['filled_orders'] += 1
        self.metrics['filled_shares'] += quantity
        self.metrics['partial_orders'] += int(quantity < requested)
        self.metrics['forced_fills'] += int(self.forced[ticker])
        self.metrics['stop_fills'] += int(self.exit_reason[ticker] == 2)
        self.metrics['target_fills'] += int(self.exit_reason[ticker] == 3)
        self.recent_shares[ticker] += quantity
        self.recent.append((self.t,int(ticker),quantity))
        self.last_fills.append(dict(listing_id=self.session.ids[ticker],side=side,shares=quantity,
            price=fill,fee=fee,slippage_ratio=slip,fee_ratio=fee/(quantity*fill),
            fee_components=components,exit_reason=int(self.exit_reason[ticker]),
            decision_second=previous,fill_second=self.t,forced=bool(self.forced[ticker])))
        if self.quantity[ticker] == 0:
            self.exit_reason[ticker] = 0

    def step(self, modes, sizes):
        if self.done:
            raise ValueError('Cannot step a terminated session')
        obs = self.observe()
        modes, sizes = np.asarray(modes), np.asarray(sizes)
        if (modes.shape != obs['ids'].shape or sizes.shape != (*modes.shape,3)
                or modes.dtype.kind not in 'iu' or np.any(modes<0) or np.any(modes>3)
                or not np.isfinite(sizes).all() or np.any(sizes<0) or np.any(sizes>1)
                or not obs['action_mask'][np.arange(len(modes)),modes].all()):
            raise ValueError('Invalid sampled policy action')
        before, previous = self.equity, self.t
        a, c = self.session.arrays, self.config
        sells, buys, brackets = {}, {}, {}
        for slot,ticker in enumerate(obs['ids']):
            mode = int(modes[slot])
            if mode == 1:
                room = max(0., c.max_ticker_weight*before-self.quantity[ticker]*a['prices'][ticker,previous])
                buys[int(ticker)] = sizes[slot,0]*room
                brackets[int(ticker)] = (
                    c.minimum_stop_ratio+sizes[slot,1]*(c.maximum_stop_ratio-c.minimum_stop_ratio),
                    c.minimum_target_ratio+sizes[slot,2]*(c.maximum_target_ratio-c.minimum_target_ratio))
            elif mode in (2,3):
                sells[int(ticker)] = self.quantity[ticker]*(sizes[slot,0] if mode == 2 else 1.)
        self.forced |= (self.quantity>0) & (self.t == self.session.seconds-2)
        sells.update({int(i):self.quantity[i] for i in np.flatnonzero(self.forced)})
        # Joint budget transform of sampled buy demands. The trainer stores the
        # original latent action probability, never a clipped-action probability.
        total = sum(buys.values())
        scale = min(1., self.cash/total) if total else 1.
        buys = {i:budget*scale for i,budget in buys.items()}
        self.t += 1
        self.metrics['halted_position_seconds'] += int(np.count_nonzero((self.quantity>0) & (a['execution_status'][:,self.t] == HALTED)))
        self.metrics['unknown_position_seconds'] += int(np.count_nonzero((self.quantity>0) & (a['execution_status'][:,self.t] == 0)))
        self.last_fills = []
        while self.recent and self.recent[0][0] <= self.t-60:
            _,ticker,quantity = self.recent.popleft()
            self.recent_shares[ticker] -= quantity
        for ticker,quantity in sorted(sells.items()):
            if quantity > 0:
                self._execute(ticker,quantity,-1,previous)
        for ticker,budget in sorted(buys.items()):
            if budget > 0:
                # Revalue cap at arrival; price jumps cannot authorize extra exposure.
                room = max(0., c.max_ticker_weight*self.equity-self.quantity[ticker]*a['prices'][ticker,self.t])
                budget = min(budget,room)
                requested = math.floor(budget/max(float(a['prices'][ticker,previous]),1e-12))
                if requested > 0:
                    self._execute(ticker,requested,1,previous,budget,brackets[ticker])
        after = self.equity
        if self.cash < -1e-6 or np.any(self.quantity<0) or not math.isfinite(after):
            raise ValueError('Account conservation failed')
        reward = (after-before)/self.initial
        self.reward_sum += reward
        self.peak = max(self.peak,after)
        self.drawdown = max(self.drawdown,(self.peak-after)/self.peak)
        self.done = self.t == self.session.seconds-1
        return self.observe(), reward, self.done, self.summary()

    def summary(self):
        notional = self.metrics['traded_notional']
        return dict(**self.metrics, equity=self.equity, cash=self.cash,
            net_profit=self.equity-self.initial, net_return=self.equity/self.initial-1,
            reward_sum=self.reward_sum,max_drawdown=self.drawdown,
            realized_fee_ratio=self.metrics['fees']/notional if notional else 0.,
            realized_slippage_ratio=self.metrics['slippage_dollars']/notional if notional else 0.,
            open_positions=int(np.count_nonzero(self.quantity)),
            valid_terminal=bool(self.done and not np.any(self.quantity)),
            seconds=self.t, terminated=self.done)

    def state_dict(self):
        return {k:v for k,v in self.__dict__.items() if k not in ('session','config')}

    def load_state_dict(self, state):
        if state['quantity'].shape != (self.session.n,):
            raise ValueError('Restart account population changed')
        self.__dict__.update(state)
