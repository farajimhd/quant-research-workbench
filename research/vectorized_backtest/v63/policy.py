from dataclasses import dataclass,asdict
import math
from .authority import SWING_WINDOWS

@dataclass(frozen=True)
class Policy:
    stop_fraction:float=.03
    target_fraction:float=.05
    reduce_fraction:float=.25
    trail_fraction:float=.03
    add_minimum_profit:float=.01
    reduce_minimum_profit:float=.02
    maximum_adds:int=2
    cooldown:int=30
    swing_left:int=0
    swing_right:int=0
    buy_limit_offset:float=0.
    sell_limit_offset:float=0.
    entry_stop_rule:str='explicit-prices-v1'
    price_program:dict|None=None
    def payload(self):
        result=asdict(self)
        if self.entry_stop_rule=='strict-v1':result.pop('entry_stop_rule')
        return result
    def validate(self):
        from .authority import ENTRY_STOP_RULE,STOP_MARGIN
        from .price_program import Prices
        if self.price_program is None:raise ValueError('Candidate price program required')
        Prices.restore(self.price_program)
        if self.entry_stop_rule not in ('strict-v1',ENTRY_STOP_RULE):raise ValueError('Unknown entry-stop construction rule')
        if any(not math.isfinite(v) or not -.1<=v<=.1 for v in (self.buy_limit_offset,self.sell_limit_offset)):raise ValueError("Limit offset outside authority")
        for name in ('stop_fraction','target_fraction','reduce_fraction','trail_fraction','add_minimum_profit','reduce_minimum_profit'):
            value=getattr(self,name)
            if not math.isfinite(value) or not 0<=value<=1:raise ValueError('Invalid position fraction')
        if self.stop_fraction>=1:raise ValueError('Initial stop must be below entry and positive')
        if min(self.stop_fraction,self.target_fraction,self.reduce_fraction,self.trail_fraction)<=0:raise ValueError('Positive position fractions required')
        if (self.swing_left==0 or self.entry_stop_rule==ENTRY_STOP_RULE) and self.stop_fraction+self.buy_limit_offset<STOP_MARGIN-1e-12:
            raise ValueError('Fixed stop and buy limit violate joint construction authority')
        if type(self.maximum_adds) is not int or not 0<=self.maximum_adds<=20 or type(self.cooldown) is not int or not 1<=self.cooldown<=300:
            raise ValueError('Invalid bounded adds/cooldown')
        if (self.swing_left,self.swing_right)!=(0,0) and (self.swing_left not in SWING_WINDOWS or self.swing_right not in SWING_WINDOWS):
            raise ValueError('Unsupported persisted swing pair')
        return self

def random_policy_gene(rng,name):
    from .authority import fraction
    if name=='price_program':
        from .price_sampling import sample_prices
        return sample_prices(rng).payload()
    if name in ('buy_limit_offset','sell_limit_offset'):return float(rng.uniform(-.01,.01))
    if name=='maximum_adds':return int(rng.integers(21))
    if name=='cooldown':return int(rng.integers(1,301))
    return fraction(rng,name)
