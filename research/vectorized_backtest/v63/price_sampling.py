"""Typed price-program proposals shared by founder and mutation construction."""
from .price_program import Prices,Expression,RESOLUTIONS,SWINGS


def sample_prices(rng):
    family=int(rng.integers(4))
    seconds=int(rng.choice(RESOLUTIONS))
    if family==0: low,high='ask','ask'
    elif family==1:low,high=f'candle.low.{seconds}',f'candle.high.{seconds}'
    elif family==2:low,high=f'rolling.low.{seconds}',f'rolling.high.{seconds}'
    else:
        left,right=int(rng.choice(SWINGS)),int(rng.choice(SWINGS))
        low,high=f'swing.low.{left}.{right}',f'swing.high.{left}.{right}'
    stop_offset=-float(rng.uniform(.001,.15));target_offset=float(rng.uniform(.001,.30))
    operation=('reference','min','blend','available')[int(rng.integers(4))]
    stop=Expression(low,'bid',operation,float(rng.random()),stop_offset)
    return Prices(buy=Expression('ask',offset=float(rng.uniform(0,.01))),
                  sell=Expression('bid',offset=-float(rng.uniform(0,.01))),stop=stop,
                  target=Expression(high,'ask','max',offset=target_offset),
                  update=Expression(low,'bid',operation,stop.weight,stop_offset)).validate()
