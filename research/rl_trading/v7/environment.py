"""Single-account next-second quote execution, with causal fixed exit tail."""
import math
import numpy as np
from research.rl_trading.v2.fees import charges
from .config import Config


class Environment:
    def __init__(self, session, config=Config()):
        self.session, self.config = session, config
        self.e = session.execution
        self.reset()

    def reset(self):
        self.t = 0
        self.cash = self.config.initial_cash
        self.ticker = -1
        self.quantity = 0
        self.basis = 0.
        self.entry = 0
        self.pending_exit = False
        self.done = False
        self.fees = self.fills = self.entries = self.exits = 0
        self.peak = self.config.initial_cash
        self.drawdown = 0.
        self.last_fills = []
        self.used = {}
        return self.observe()

    @property
    def equity(self):
        if not self.quantity:
            return self.cash
        price = float(self.e['mark'][self.t, self.ticker])
        if not math.isfinite(price) or price <= 0:
            raise ValueError('Held position lacks causal positive mark')
        return self.cash+self.quantity*price

    def observe(self):
        n = len(self.session.selection)
        features = self.session.features[self.t].copy() if self.t<n else np.zeros((5,98),np.float32)
        ids = self.session.selection[self.t] if self.t<n else np.full(5,-1)
        valid = ids>=0
        actions = np.zeros(7,bool); actions[0] = True
        if self.t<n-1 and not self.quantity:
            for k,j in enumerate(ids):
                if j>=0 and self.e['quote_valid'][self.t,j] and self.e['mark'][self.t,j]>0:
                    ask = self.e['ask'][self.t,j]
                    actions[k+1] = self.cash*self.config.allocation >= ask*2 and self.e['ask_size'][self.t,j]>=10
        actions[6] = bool(self.quantity and self.t<n-1 and not self.pending_exit)
        eq = self.equity
        account = np.array([self.cash/self.config.initial_cash, eq/self.config.initial_cash-1,
                            self.drawdown, self.t/len(self.session.clocks), self.fees/self.config.initial_cash,
                            bool(self.quantity), min(self.entries/100,1), min(self.exits/100,1)],np.float32)
        holding = np.zeros(8,np.float32)
        if self.quantity:
            j=self.ticker; mark=self.e['mark'][self.t,j]; bid=self.e['bid'][self.t,j]; ask=self.e['ask'][self.t,j]
            holding[:] = [1, self.quantity*mark/self.config.initial_cash, mark/self.basis-1,
                          (self.t-self.entry)/self.config.max_hold_seconds, bool(self.pending_exit),
                          (ask-bid)/mark, bool(self.e['quote_valid'][self.t,j]), j in ids]
        return dict(features=features,account=account,holding=holding,
                    candidate_valid=valid,action_mask=actions)

    def _fill(self, j, buy):
        if not self.e['quote_valid'][self.t,j]:
            return
        stamp = int(self.e['quote_us'][self.t,j])
        side = 1 if buy else -1
        key = (j,side)
        previous_stamp, consumed = self.used.get(key,(-1,0))
        if stamp != previous_stamp:
            consumed=0
        size=float(self.e['ask_size' if buy else 'bid_size'][self.t,j])
        capacity=max(0,math.floor(size*self.config.displayed_participation)-consumed)
        price=float(self.e['ask' if buy else 'bid'][self.t,j])*(1+side*self.config.extra_slippage)
        if capacity<1 or price<=0 or not math.isfinite(price):
            return
        q=min(capacity,math.floor(self.cash*self.config.allocation/price)) if buy else min(capacity,self.quantity)
        def fee(q):return sum(charges(q,price,side,self.config).values())
        while q>0 and buy and q*price+fee(q)>self.cash:
            q-=1
        if not q:return
        costs=fee(q)
        if buy:
            self.cash-=q*price+costs;self.quantity=q;self.ticker=j;self.basis=price;self.entry=self.t
            self.entries+=1
        else:
            self.cash+=q*price-costs;self.quantity-=q
            if not self.quantity:
                self.ticker=-1;self.pending_exit=False;self.exits+=1
        self.used[key]=(stamp,consumed+q)
        self.fees+=costs;self.fills+=1
        self.last_fills.append(dict(second=self.t,ticker=self.session.tickers[j],side=side,
                                    quantity=q,price=price,fees=costs,quote_us=stamp))

    def step(self, action):
        if self.done:raise ValueError('Episode already complete')
        obs=self.observe()
        if not 0<=int(action)<7 or not obs['action_mask'][action]:
            raise ValueError('Invalid action')
        before=self.equity
        j=-1
        if 1<=action<=5:j=int(self.session.selection[self.t,action-1])
        if self.quantity:
            mark=self.e['mark'][self.t,self.ticker]
            self.pending_exit |= bool(action==6 or mark<=self.basis*(1-self.config.stop_ratio)
                                      or mark>=self.basis*(1+self.config.target_ratio)
                                      or self.t-self.entry>=self.config.max_hold_seconds
                                      or self.t>=len(self.session.clocks)-901)
        self.t+=1
        self.last_fills=[]
        if self.pending_exit and self.quantity:self._fill(self.ticker,False)
        elif j>=0 and not self.quantity:self._fill(j,True)
        eq=self.equity
        self.peak=max(self.peak,eq);self.drawdown=max(self.drawdown,1-eq/self.peak)
        # Flat after the decision interval is terminal; otherwise fixed exits continue.
        self.done = self.t>=len(self.session.clocks)-1 or (self.t>=len(self.session.selection)-1 and not self.quantity)
        reward=100*(eq-before)/self.config.initial_cash
        return self.observe(),reward,self.done,self.result() if self.done else {}

    def result(self):
        return dict(day=self.session.day,return_ratio=self.equity/self.config.initial_cash-1,
                    fees=self.fees,fills=self.fills,entries=self.entries,exits=self.exits,
                    max_drawdown=self.drawdown,valid_terminal=self.done and self.quantity==0,
                    terminal_quantity=self.quantity,second=self.t)
