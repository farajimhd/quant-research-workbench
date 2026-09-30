import torch
from datetime import date
from pathlib import Path
import numpy as np
from research.rl_trading.v1.common import bounds
from research.rl_trading.v6.oms import Quote
from research.rl_trading.v6.environment import BracketEnvironment
from research.rl_trading.v6.environment_source import ExecutionBucket
from research.rl_trading.v6.bank import SessionBank
from research.rl_trading.v6.session_data import PackedSession
from research.rl_trading.v6.ranked_policy import RankedBracketActorCritic
from research.rl_trading.v6.market_attention import MarketAttentionConfig
from research.rl_trading.v6.rollout import collect_session, update_session


class Evidence:
    def __init__(self, origin):
        self.origin=origin
        self.requests=[]
    def buckets(self,start,end,tickers):
        self.requests.append((start,end,tuple(tickers)))
        return tuple(ExecutionBucket(t,c,Quote(c,c-1,9.99,10.,10000.,10000.,True),10.1,9.9)
            for c in range(start+100_000,end+1,100_000) for t in tickers)
    def target_capacity(self,ticker,clock,target):
        return 2.


def test_same_boundary_target_batch_preserves_serial_fills():
    class Batched(Evidence):
        def __init__(self,origin):
            super().__init__(origin)
            self.target_reads=[]
        def target_capacities(self,clock,targets):
            self.target_reads.append((clock,dict(targets)))
            return {t:2. for t in targets}
    def run(source):
        env=BracketEnvironment(('A','B'),source)
        env.marks={'A':(10.,1_000_000),'B':(10.,1_000_000)}
        env.advance(1_000_000)
        for i in range(2):
            env.submit(i+1,.2,clock_us=1_000_000,order_index=i,holdings=np.empty(0,dtype=np.int64))
        env.advance(2_000_000)
        for t in ('A','B'):
            env.account.set_target(t,price=10.05,clock_us=2_000_000)
        outcomes=env.advance(3_000_000)
        return env,outcomes
    serial,outcomes=run(Evidence(0))
    source=Batched(0)
    batch,actual=run(source)
    assert actual==outcomes
    assert batch.account.orders==serial.account.orders
    assert batch.account.closed==serial.account.closed
    assert batch.account.cash==serial.account.cash
    assert len(source.target_reads)==10
    assert all(set(targets)=={'A','B'} for _,targets in source.target_reads)


def test_oms_entries_after_decision_brackets_then_partial_liquidation():
    source=Evidence(0)
    env=BracketEnvironment(('A',),source)
    env.marks={'A':(10.,1_000_000)}
    env.advance(1_000_000)
    env.submit(1,.5,clock_us=1_000_000,order_index=0,holdings=np.empty(0,dtype=np.int64))
    assert not env.account.positions and env.reserved_cash==5000
    outcomes=env.advance(2_000_000)
    assert outcomes[0].action==1 and env.account.positions['A'].entry_us>1_000_000
    env.marks={'A':(10.,2_000_000)}
    env.submit(3,.1,clock_us=2_000_000,order_index=0,holdings=np.array([0]))
    env.submit(4,.2,clock_us=2_000_000,order_index=1,holdings=np.array([0]))
    assert env.account.positions['A'].stop is not None
    assert env.account.positions['A'].target is not None
    env.force_exit('A',2_000_000)
    assert env.account.positions['A'].target is None
    env.advance(3_000_000)
    assert not env.account.positions
    assert env.account.closed[0]['exit_us']>2_000_000


def test_missing_arrival_quote_is_explicit_nonfill():
    class Missing(Evidence):
        def buckets(self,*args): return ()
    env=BracketEnvironment(('A',),Missing(0))
    env.marks={'A':(10.,1_000_000)}
    env.advance(1_000_000)
    env.submit(1,.5,clock_us=1_000_000,order_index=0,holdings=[])
    outcome=env.advance(2_000_000)[0]
    assert outcome.filled_fraction==0 and not env.account.positions
    assert env.account.orders[-1]['reason']=='missing_or_stale_quote'


def test_same_displayed_bid_cannot_be_reused_across_buckets():
    class Repeated(Evidence):
        def buckets(self,start,end,tickers):
            return tuple(ExecutionBucket(t,c,Quote(c,start+1,9.99,10.,1.,10000.,True),10.1,9.9)
                for c in range(start+100_000,end+1,100_000) for t in tickers)
    env=BracketEnvironment(('A',),Repeated(0))
    env.marks={'A':(10.,1_000_000)}
    env.advance(1_000_000)
    env.submit(1,.5,clock_us=1_000_000,order_index=0,holdings=[])
    env.advance(2_000_000)
    original=env.account.positions['A'].shares
    env.force_exit('A',2_000_000)
    env.advance(3_000_000)
    assert env.account.positions['A'].shares==original-1
    assert any(r['reason']=='displayed_bid_already_consumed_at_quote_timestamp' for r in env.account.orders)


def test_collection_and_actual_ppo_reconstruction_optimizer_path():
    day=date(2026,7,31)
    origin=bounds(day)[0]
    clocks=np.arange(origin+1_000_000,origin+7_000_000,1_000_000,dtype=np.int64)
    scalar=np.zeros((6,37),dtype=np.float32)
    scalar[:,3]=np.log(10.)
    scalar[:,8]=np.log1p(100.)
    scalar[:,35]=1
    bank=SessionBank(Path('unused'),{'offsets':{'idA':[0,6]}},clocks,scalar,np.zeros((6,2,5,11),dtype=np.float32))
    session=PackedSession(day,'train',Path('unused'),'hash',bank,None,('idA',))
    torch.manual_seed(9)
    policy=RankedBracketActorCritic(8,config=MarketAttentionConfig(top_r=1,heads=2))
    env=BracketEnvironment(('A',),Evidence(origin))
    collected=[]
    frames,steps,metrics=collect_session(policy,session,env,device=torch.device('cpu'),max_clocks=5,
        max_orders_per_second=4,progress_callback=collected.append)
    assert collected and collected[-1]['policy_steps']>0
    assert steps[-1].terminal
    assert sum(s.elapsed for s in steps)==5
    assert metrics['equity_mark_count']==6
    old=policy.decoder.enter_head.weight.detach().clone()
    reconstructed=[]
    result=update_session(policy,torch.optim.Adam(policy.parameters(),lr=1e-4),session,frames,steps,
        device=torch.device('cpu'),epochs=2,clocks_per_chunk=2,progress_callback=reconstructed.append)
    assert reconstructed[-1]['processed_policy_steps']==len(steps)
    assert result['update_epochs']>=1
    assert np.isfinite(result['loss'])
    assert not torch.equal(old,policy.decoder.enter_head.weight)
