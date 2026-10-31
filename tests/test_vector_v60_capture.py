import os,unittest
import torch
from types import SimpleNamespace
from unittest.mock import patch


@unittest.skipUnless(os.environ.get('V60_TEST_CUDA')=='1','opt-in GPU qualification')
class CaptureParity(unittest.TestCase):
    def test_capture_padding_and_forced_journal_overflow_are_exact(self):
        from research.vectorized_backtest.v60.order_replay import replay
        from research.vectorized_backtest.v60.episodes import Episodes
        from research.vectorized_backtest.v60.evaluator import Execution
        def run(backend,capacity):
            b,u,t=2,3,65;device='cuda'
            position=torch.zeros((1,b,u,9),device=device,dtype=torch.float64)
            aggregate=torch.zeros((1,b,16),device=device,dtype=torch.float64);aggregate[...,8]=float('inf')
            opened=torch.zeros((1,b,u),device=device,dtype=torch.float64)
            ticker=torch.zeros((1,b,u,2),device=device,dtype=torch.float64)
            signals=torch.zeros((1,b,u),device=device,dtype=torch.int16)
            plan=SimpleNamespace(ratios=torch.ones((1,b,u,3),device=device,dtype=torch.float64),
                feedback=torch.zeros((b,u,4),device=device),previous_clock=torch.tensor(-1,device=device),
                buffer_phase=torch.tensor(0,device=device))
            price=(10+torch.arange(t,device=device,dtype=torch.float64)/100)[:,None,None,None].expand(t,1,1,u).contiguous()
            valid=torch.ones_like(price,dtype=torch.bool)
            book=torch.stack((torch.full_like(price,10000),torch.full_like(price,10000),torch.arange(t,device=device,dtype=torch.float64)[:,None,None,None].expand_as(price)),dim=-1)
            policy=torch.tensor([.03,.5,.25,.03,0.,0.,10.,0.,0.,0.,0.],device=device,dtype=torch.float64)[None,None,None].expand(1,b,1,11)
            actions=torch.where(torch.arange(t,device=device)%5==0,1,torch.where(torch.arange(t,device=device)%5==1,2,0)).to(torch.int16)
            def provide(index,p,o):
                signals.copy_(actions.index_select(0,index.reshape(1)).reshape(1,1,1).expand_as(signals))
                plan.previous_clock.copy_(index);plan.buffer_phase.add_(1)
            episodes=Episodes(1,b,capacity,device,max_listings=u,trace=True)
            item=SimpleNamespace(execution_tail=None,listing_ids=list(range(u)))
            with patch('research.vectorized_backtest.v60.order_tail.Tail',lambda *a:SimpleNamespace(receipt={'pages':[]})):
                result=replay(item=item,plan=plan,position=position,aggregate=aggregate,opened=opened,policy=policy,
                    prices=price,observed=valid,membership=valid,signals=signals,values=torch.zeros((1,1),device=device),
                    ids=torch.zeros((t,1,u),device=device,dtype=torch.int64),columns=torch.zeros(b,device=device,dtype=torch.int64),
                    limits=torch.zeros((1,b,1,5),device=device,dtype=torch.float64),ticker_state=ticker,
                    bids=price,asks=price,quote_valid=valid,capacity=book,execution=Execution(),episodes=episodes,
                    provide=provide,progress=None,block=4,gate=None,backend=backend,record_capacity=capacity)
            return result,episodes.export(),position.cpu(),aggregate.cpu(),plan.previous_clock.cpu(),plan.buffer_phase.cpu()
        baseline=run('eager',8192)
        for actual in (run('compile',8192),run('compile',24)):
            self.assertEqual(actual[:2],baseline[:2])
            for left,right in zip(actual[2:4],baseline[2:4]):torch.testing.assert_close(left,right,rtol=0,atol=0)
            # Padding invokes the provider but cannot advance the account or counters.
            self.assertEqual(actual[4].item(),64)
