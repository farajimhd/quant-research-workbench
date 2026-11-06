"""Typed price-program proposals shared by founder and mutation construction."""
from .price_program import Prices,Expression,RESOLUTIONS,SWINGS


def sample_prices(rng):
    family=int(rng.integers(5))
    seconds=int(rng.choice(RESOLUTIONS))
    if family==0: low,high='ask','ask'
    elif family==1:low,high=f'candle.low.{seconds}',f'candle.high.{seconds}'
    elif family==2:low,high=f'rolling.low.{seconds}',f'rolling.high.{seconds}'
    elif family==3:low,high=f'previous.low.{seconds}',f'previous.high.{seconds}'
    else:
        left,right=int(rng.choice(SWINGS)),int(rng.choice(SWINGS))
        low,high=f'swing.low.{left}.{right}',f'swing.high.{left}.{right}'
    stop_offset=-float(rng.uniform(.001,.15));target_offset=float(rng.uniform(.001,.30))
    operation=('reference','min','blend','available')[int(rng.integers(4))]
    stop=Expression(low,'bid',operation,float(rng.random()),stop_offset)
    # Price references are candidate programs, not execution instructions.
    # Include direct quotes, level-based limits and explicit quote combinations.
    buy=Expression('ask',offset=float(rng.uniform(0,.01)))
    sell=Expression('bid',offset=-float(rng.uniform(0,.01)))
    if int(rng.integers(3))==0:
        buy=Expression(high,'ask',('max','blend','available')[int(rng.integers(3))],float(rng.random()),buy.offset)
        sell=Expression(low,'bid',('min','blend','available')[int(rng.integers(3))],float(rng.random()),sell.offset)
    return Prices(buy=buy,
                  sell=sell,stop=stop,
                  target=Expression(high,'ask','max',offset=target_offset),
                  update=Expression(low,'bid',operation,stop.weight,stop_offset)).validate()
