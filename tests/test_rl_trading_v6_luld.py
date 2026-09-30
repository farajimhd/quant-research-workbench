from datetime import date
from types import MethodType
import numpy as np
import polars as pl
import pytest
from research.rl_trading.v6.luld import project, LuldBook, STEP, RiskPenalty
from research.rl_trading.v6.environment import BracketEnvironment
from research.rl_trading.v6.environment_source import ArteExecutionSource, ExecutionBucket
from research.rl_trading.v6.oms import Quote
from research.rl_trading.v6.entry_source import _midnight_us


def evidence(start, seconds=40, *, price=11, bid=11, ask=11.01):
    clocks=np.arange(start+STEP,start+seconds*1_000_000+1,STEP,dtype=np.int64)
    trades=pl.DataFrame({'bucket_us':clocks,'price_sum':np.full(len(clocks),price),
                         'count':np.ones(len(clocks),dtype=np.int64)})
    quotes=pl.DataFrame({'bucket_us':clocks,'quote_us':clocks-1,
        'bid':np.full(len(clocks),bid),'ask':np.full(len(clocks),ask)})
    return trades,quotes


def test_trade_arithmetic_reference_prefix_and_tier_width():
    start=_midnight_us(date(2026,7,31))+34200_000_000
    trades,quotes=evidence(start,price=11,bid=10,ask=10.01)
    full,report=project(start,start+40_000_000,trades,quotes,previous_close=10,tier=2)
    prefix,_=project(start,start+20_000_000,trades.head(40),quotes.head(40),previous_close=10,tier=2)
    assert full.head(1).equals(prefix)
    assert full['upper'][0]==11 and full['lower'][0]==9
    assert full.filter(pl.col('available_us')==start+30_000_000)['upper'][0]==12.1
    assert report['eligible_trades']==80


def test_quote_limit_not_price_touch_and_stale_breaks_timer():
    start=0
    trades,quotes=evidence(start,price=10,bid=11,ask=11.01)
    frame,report=project(start,600_000_000,trades,quotes,previous_close=10,tier=2)
    assert report['modeled_pauses']==1
    first=frame.filter('paused')['available_us'][0]
    assert first==15_500_000  # 15 seconds of observed consecutive limit state.
    # A crossed bid is not equality and must not fabricate a limit pause.
    crossed=quotes.with_columns(pl.lit(11.1).alias('bid'),pl.lit(11.2).alias('ask'))
    _,other=project(start,600_000_000,trades,crossed,previous_close=10,tier=2)
    assert other['modeled_pauses']==0
    missing=quotes.with_columns(pl.when(pl.col('bucket_us')==10_000_000)
        .then(0).otherwise(pl.col('quote_us')).alias('quote_us'))
    _,broken=project(start,20_000_000,trades.head(40),missing.head(40),previous_close=10,tier=2)
    assert broken['modeled_pauses']==0
    book=LuldBook({'A':frame},end_us=600_000_000)
    assert not book.blocked('A',first-1) and book.blocked('A',first)
    assert book.state('A',600_000_000) is None


def test_halt_blocks_exit_cost_once_and_terminal_cost_not_pnl():
    class Source:
        def buckets(self,start,end,tickers):
            return tuple(ExecutionBucket(t,c,Quote(c,c-1,10,10,10000,10000,True),10,10)
                for c in range(start+100_000,end+1,100_000) for t in tickers)
    frame=pl.DataFrame({'available_us':[2_000_000,4_000_000],
        'lower':[9.,9.],'upper':[11.,11.],'paused':[True,False],
        'pause_start_us':[2_000_000,0]})
    env=BracketEnvironment(('A',),Source(),luld=LuldBook({'A':frame},end_us=10_000_000))
    env.marks={'A':(10.,1_000_000)}
    env.advance(1_000_000)
    env.submit(1,.5,clock_us=1_000_000,order_index=0,holdings=[])
    env.advance(2_000_000)
    env.force_exit('A',2_000_000)
    env.advance(3_000_000)
    assert 'A' in env.account.positions
    assert env.observation(3_000_000).held_features[0,9]==1
    assert env.risk_metrics['trapped_position_events']==1
    pnl=env.account.marked_equity({'A':10.})
    env.terminal_cost(); env.terminal_cost()
    assert env.risk_metrics['terminal_exposure_penalty']>0
    assert pnl==env.account.marked_equity({'A':10.})
    env.advance(4_000_000)
    assert not env.account.positions and env.risk_metrics['trapped_position_events']==1


def test_execution_prefetch_filters_future_and_reuses_batch():
    provider=object.__new__(ArteExecutionSource)
    provider.origin=0; provider.end_us=30_000_000
    provider.attempts={'A':'proof'}; provider.bucket_cache={}
    calls=[]
    def read(self,start,end,tickers):
        calls.append((start,end,tickers))
        return tuple(ExecutionBucket('A',c,None,None,None)
                     for c in range(start+100_000,end+1,100_000))
    provider._read_buckets=MethodType(read,provider)
    assert max(row.close_us for row in provider.buckets(0,1_000_000,['A']))==1_000_000
    assert min(row.close_us for row in provider.buckets(1_000_000,2_000_000,['A']))>1_000_000
    assert len(calls)==1


def test_risk_coefficients_are_explicit_finite():
    with pytest.raises(ValueError): RiskPenalty(halt_entry=float('nan'))
