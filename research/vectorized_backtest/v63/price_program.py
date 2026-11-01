"""Candidate-owned, market-only price expressions. Execution receives prices."""
from dataclasses import dataclass, asdict
import math
import numpy as np

RESOLUTIONS = tuple(range(5, 61, 5))
SWINGS = (1, 2, 3, 4, 5, 10, 15, 20, 30, 45, 60)
REFERENCES = ('ask', 'bid', 'close') + tuple(
    f'{kind}.{side}.{seconds}'
    for kind in ('candle', 'previous', 'rolling')
    for side in ('low', 'high') for seconds in RESOLUTIONS
) + tuple(f'swing.{side}.{left}.{right}'
          for side in ('low', 'high') for left in SWINGS for right in SWINGS)


def entry_guard(buy,stop,target,ask,*,xp=np):
    from .authority import FIXED_ENTRY_DOLLARS
    return (xp.isfinite(buy)&xp.isfinite(stop)&xp.isfinite(target)&xp.isfinite(ask)&
            (buy>0)&(buy<=FIXED_ENTRY_DOLLARS)&(stop>0)&(stop<buy)&(stop<ask)&(target>buy))


@dataclass(frozen=True)
class Expression:
    reference: str = 'ask'
    other: str = 'bid'
    operation: str = 'reference'
    weight: float = .5
    offset: float = 0.
    volatility_offset: float = 0.

    def validate(self):
        if self.reference not in REFERENCES or self.other not in REFERENCES:
            raise ValueError('Unknown or unsupported price reference')
        if self.operation not in ('reference', 'min', 'max', 'blend', 'available'):
            raise ValueError('Unsupported price operation')
        if not all(math.isfinite(x) for x in (self.weight,self.offset,self.volatility_offset)):
            raise ValueError('Nonfinite price expression')
        if not 0 <= self.weight <= 1 or not -.99 <= self.offset <= 1 or not -10 <= self.volatility_offset <= 10:
            raise ValueError('Price parameters outside creation authority')
        return self

    def payload(self):
        return asdict(self)

    def evaluate(self, values, *, xp=np):
        a = values[self.reference]
        # Replay packs coefficients as float32, then applies them to float64 prices.
        weight=np.float32(self.weight);other=np.float32(1)-weight
        if self.operation == 'reference':
            result = a
        else:
            b = values[self.other]
            if self.operation == 'min': result = xp.minimum(a,b)
            elif self.operation == 'max': result = xp.maximum(a,b)
            elif self.operation == 'blend': result = weight*a+other*b
            else: result = xp.where(xp.isfinite(a)&(a>0),a,b)
        result = result*np.float32(np.float32(1)+np.float32(self.offset))
        if self.volatility_offset:
            result = result+np.float32(self.volatility_offset)*values['volatility']
        return result


@dataclass(frozen=True)
class Prices:
    buy: Expression = Expression('ask')
    sell: Expression = Expression('bid')
    stop: Expression = Expression('ask',offset=-.03)
    target: Expression = Expression('ask',offset=.05)
    update: Expression = Expression('close',offset=-.03)

    def validate(self):
        for value in (self.buy,self.sell,self.stop,self.target,self.update): value.validate()
        # Static relationships can be proved only for identical expressions.
        for lower,upper in ((self.stop,self.buy),(self.buy,self.target)):
            a,b=asdict(lower),asdict(upper)
            oa,ob=a.pop('offset'),b.pop('offset')
            if a==b and oa>=ob:
                raise ValueError('Static price relationship is impossible')
        return self

    def payload(self):
        return {k:v.payload() for k,v in vars(self).items()}

    @classmethod
    def restore(cls, value):
        if isinstance(value,cls): return value.validate()
        return cls(**{k:Expression(**v) for k,v in value.items()}).validate()

    def evaluate(self, values, *, xp=np):
        buy,sell,stop,target,update=(x.evaluate(values,xp=xp) for x in vars(self).values())
        # Guards belong to the candidate; never replace or clamp a missing stop.
        entry=entry_guard(buy,stop,target,values['ask'],xp=xp)
        return (buy,sell,stop,target,update),entry
