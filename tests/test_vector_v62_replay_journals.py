import torch
import os
import pytest


class TestReplayJournals:
    def run_path(self,device,backend,candidates=2,active_only=False,repeated_quote=False,fast_orders=False,record_capacity=None,episode_capacity=32):
        from types import SimpleNamespace
        from unittest.mock import patch
        import numpy as np
        from research.vectorized_backtest.v62.order_replay import replay
        from research.vectorized_backtest.v62.evaluator import Execution
        from research.vectorized_backtest.v62.episodes import Episodes
        shape=(1,candidates,2)
        def zeros(*dims):return torch.zeros(dims,dtype=torch.float64,device=device)
        position=zeros(*shape,9);aggregate=zeros(1,candidates,16);aggregate[...,8]=float('inf')
        opened=zeros(*shape);ticker=zeros(*shape,2)
        policy=torch.tensor([.05,.5,.5,.05,0.,0.,2.,0.,0.,0.,0.],dtype=torch.float64,device=device)[None,None,None].expand(1,candidates,1,11)
        total=20 if fast_orders else 4
        prices=torch.full((total,1,1,2),10.,dtype=torch.float64,device=device)
        valid=torch.ones_like(prices,dtype=torch.bool)
        signals=torch.zeros(shape,dtype=torch.int16,device=device)
        plan=SimpleNamespace(ratios=torch.ones((*shape,3),dtype=torch.float64,device=device),feedback=zeros(candidates,2,1),previous_clock=zeros(),buffer_phase=zeros())
        item=SimpleNamespace(execution_tail='mock',listing_ids=np.array([0,1]),arrays={'clocks':np.arange(total)})
        class Tail:
            def __init__(self,*args):self.receipt={'pages':[{}]}
            def load(self,index):
                return dict(mark=np.full((4,2),9.),bid=np.full((4,2),9.),ask=np.full((4,2),9.),
                    quote_valid=np.ones((4,2),bool),bid_size=np.full((4,2),100.),ask_size=np.full((4,2),100.),
                    quote_us=np.repeat((np.array([1,1,6,6]) if repeated_quote else np.arange(4,8))[:,None]*1.e6,2,axis=1),clocks=np.arange(4,8))
        episodes=Episodes(1,candidates,episode_capacity,device,max_listings=2,trace=True)
        def provide(index,p,o):
            signals.fill_(0 if active_only else 1)
            if active_only:signals[:,0].fill_(1)
            if fast_orders:signals.copy_(torch.where(index%2==0,1,2).to(signals.dtype).expand_as(signals))
        execution=Execution(risk_limits_enabled=False,order_mode='minimal-close-limit-v1',maximum_quote_age_seconds=None,minimum_order_fee=1.)
        stamps=torch.tensor([0,1,1,1] if repeated_quote else list(range(total)),device=device,dtype=torch.float64)[:,None,None,None].expand_as(prices)*1.e6
        size=100. if fast_orders else 30.
        capacity=torch.stack((torch.full_like(prices,size),torch.full_like(prices,size),stamps),-1)
        with patch('research.vectorized_backtest.v62.order_tail.Tail',Tail):
            result=replay(item=item,plan=plan,position=position,aggregate=aggregate,opened=opened,policy=policy,
                prices=prices,observed=valid,membership=valid,signals=signals,values=zeros(1,1),
                ids=torch.zeros((total,1,2),dtype=torch.int64,device=device),columns=torch.zeros(candidates,dtype=torch.int64,device=device),
                limits=zeros(1,candidates,1,5),ticker_state=ticker,bids=prices,asks=prices,quote_valid=valid,
                capacity=capacity,execution=execution,episodes=episodes,provide=provide,
                progress=None,block=2,gate=None,backend=backend,record_capacity=record_capacity)
        return position.cpu(),aggregate.cpu(),result,episodes.export()


    def test_capacity_bounded_execution_preserves_all_financial_and_order_results(self):
        full=self.run_path('cpu','eager',fast_orders=True)
        small=self.run_path('cpu','eager',fast_orders=True,record_capacity=4,episode_capacity=4)
        for a,b in zip(full[:2],small[:2]):torch.testing.assert_close(a,b,rtol=0,atol=0)
        assert full[2:]==small[2:]

    def test_tail_compaction_and_quote_reuse_preserve_candidate_identity(self):
        full=self.run_path('cpu','eager',64,True,repeated_quote=True)
        assert (full[0][...,0]==0).all()
        assert all(not row for row in full[2]['execution_orders'][1:])
        assert full[3][0]['episode_exit_clocks'][0]==[6,6]

    @pytest.mark.skipif(os.environ.get('V60_TEST_CUDA')!='1',reason='opt-in GPU')
    def test_captured_execution_matches_cpu_and_repeats_exactly(self,tmp_path):
        from research.vectorized_backtest.v62.compiler_runtime import configure_caches
        configure_caches(tmp_path/'compiler')
        cpu=self.run_path('cpu','eager')
        first=self.run_path('cuda','compile')
        second=self.run_path('cuda','compile')
        for expected,actual in ((cpu,first),(first,second)):
            for a,b in zip(expected[:2],actual[:2]):torch.testing.assert_close(a,b,rtol=0,atol=0)
            assert expected[2:]==actual[2:]
