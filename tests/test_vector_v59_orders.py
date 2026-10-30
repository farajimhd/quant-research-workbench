import unittest
import torch
from research.vectorized_backtest.v59.orders import transition, require_complete, ORDER_WIDTH


class PersistentOrders(unittest.TestCase):
    def setUp(self):
        self.p=torch.zeros((1,1,1,9),dtype=torch.float64)
        self.o=torch.zeros((1,1,1,ORDER_WIDTH),dtype=torch.float64)
        self.policy=torch.tensor([.05,.5,.5,.05,0.,0.,2.,0.,0.],dtype=torch.float64).reshape(1,1,1,9)
        self.clock=0
    def tick(self,signal=1,capacity=30.,fresh=True,terminal=False,price=10.,swing=0.):
        self.clock+=1
        shape=(1,1,1)
        values=dict(signals=torch.full(shape,signal,dtype=torch.int16),ratios=torch.ones((*shape,3),dtype=torch.float64),
            policy=self.policy,mark=torch.full(shape,price),previous=torch.full(shape,price),
            membership=torch.ones(shape,dtype=torch.bool),observed=torch.ones(shape,dtype=torch.bool),
            swing=torch.full(shape,swing),bid=torch.full(shape,price,dtype=torch.float64),ask=torch.full(shape,price,dtype=torch.float64),
            quote_valid=torch.full(shape,fresh),capacity=torch.full((*shape,2),capacity,dtype=torch.float64),clock=torch.tensor(float(self.clock)),
            terminal=torch.full(shape,terminal),entry_dollars=1000.,add_dollars=200.,fee_per_share=.005,minimum_order_fee=1.,cost=0.,minimum_order_dollars=0.)
        self.p,self.o,complete,record=transition(self.p,self.o,**values)
        return complete,record
    def test_stale_then_partial_fill_and_terminal_drain(self):
        self.tick(fresh=False)
        self.assertEqual(self.p[...,0].item(),0.)
        self.assertEqual(self.o[...,0].item(),1.)
        self.tick(capacity=30);self.tick(capacity=20)
        self.assertEqual(self.p[...,0].item(),50.)
        self.assertEqual(self.o[...,12].item(),1.)
        done,record=self.tick(capacity=50,terminal=True)
        self.assertTrue(done.item());self.assertEqual(record[...,3].item()/record[...,2].item(),10.)
        self.tick(capacity=40,terminal=True)
        self.assertEqual(self.p[...,0].item(),60.)
        self.tick(capacity=60,terminal=True)
        require_complete(self.p,self.o)
        self.assertEqual(self.p[...,7].item(),-2.)
        self.assertEqual(self.o[...,12].item(),2.)
        self.assertEqual(self.o[...,13].item(),2.)
    def test_no_liquidity_is_pending_not_a_failed_candidate(self):
        self.tick(capacity=0)
        self.assertEqual(self.p[...,0].item(),0.)
        with self.assertRaisesRegex(RuntimeError,'no fitness published'):require_complete(self.p,self.o)
    def test_pending_exit_is_not_cancelled_by_later_signals(self):
        self.tick(capacity=100)
        self.tick(signal=2,capacity=20)
        self.tick(signal=4,capacity=80)
        require_complete(self.p,self.o)
        self.assertEqual(self.o[...,12].item(),2.)
    def test_price_and_minimum_fee_accumulate_per_order(self):
        self.tick(capacity=50,price=10)
        done,record=self.tick(capacity=50,price=11)
        self.assertTrue(done.item())
        self.assertEqual(self.p[...,1].item(),10.5)
        self.assertEqual(record[...,3].item()/record[...,2].item(),10.5)
        self.assertEqual(record[...,4].item(),1.)

    def test_zero_capacity_does_not_freeze_quantity_to_an_unusable_quote(self):
        self.tick(capacity=0,price=10)
        self.assertEqual(self.o[...,3].item(),0.)
        self.tick(capacity=100,price=20)
        self.assertEqual(self.p[...,0].item(),50.)

    def test_unused_swing_cannot_poison_fractional_stop_orders(self):
        self.tick(capacity=100,swing=float('nan'))
        self.tick(signal=2,capacity=100,swing=float('nan'))
        require_complete(self.p,self.o)


class ReplayPath(unittest.TestCase):
    def test_routed_plan_applies_configured_neural_tile_to_execution(self):
        from types import SimpleNamespace
        from research.vectorized_backtest.v59.routing_plan import RoutingPlan
        plan=RoutingPlan.__new__(RoutingPlan);plan.base=SimpleNamespace(cuda_block=128)
        plan.cuda_block=64
        self.assertEqual(plan.base.cuda_block,64)
        self.assertEqual(plan.cuda_block,64)

    def test_asynchronous_journal_preserves_rows_and_candidate_hashes(self):
        import json,tempfile
        import numpy as np
        import polars as pl
        from pathlib import Path
        from hashlib import sha256
        from research.vectorized_backtest.v59.order_journal import Journal
        runtime=Path('D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v59/checks')
        runtime.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=runtime,prefix='order-journal-') as folder:
            journal=Journal(folder,2,{'fixture':True})
            data=np.arange(27,dtype=np.float64).reshape(3,9)
            journal.submit(np.array([0,1]),np.array([2,1]),data)
            journal.submit(np.array([0,1]),np.array([2,1]),data.copy())
            refs=journal.finish();receipt=json.loads(Path(refs[0]['receipt']).read_bytes())
            rows=pl.read_parquet([journal.root/p['file'] for p in receipt['pages']])
            self.assertEqual(rows.height,6);self.assertEqual(refs[0]['orders'],4)
            self.assertEqual(refs[0]['sha256'],sha256(data[:2].tobytes()*2).hexdigest())
            for page in receipt['pages']:
                self.assertEqual(sha256((journal.root/page['file']).read_bytes()).hexdigest(),page['sha256'])

    @unittest.skipUnless(__import__('os').environ.get('V59_TEST_CUDA')=='1','opt-in GPU qualification')
    def test_fused_orders_match_randomized_cpu_oracle_each_clock(self):
        from research.vectorized_backtest.v59.order_transition import transition as gpu
        from research.vectorized_backtest.v59.order_accounting import account
        from research.vectorized_backtest.v59.order_accounting_cuda import account as gpu_account
        from research.vectorized_backtest.v59.order_step import step
        torch.manual_seed(419)
        shape=(1,5,137);p=torch.zeros((*shape,9),dtype=torch.float64);o=torch.zeros((*shape,ORDER_WIDTH),dtype=torch.float64)
        pg=p.cuda();og=o.cuda()
        aggregate=torch.zeros((1,5,16),dtype=torch.float64);aggregate[...,8]=float('inf');ag=aggregate.cuda()
        policy=torch.tensor([.05,.03,.4,.03,-.1,-.1,4.,2.,0.],dtype=torch.float64)[None,None,None].expand(1,5,1,9)
        for clock in range(1,65):
            price=10+torch.rand(shape,dtype=torch.float64)
            args=dict(signals=torch.randint(0,32,shape,dtype=torch.int16),ratios=torch.rand((*shape,3),dtype=torch.float64),policy=policy,
                previous=price,membership=torch.rand(shape)>.1,swing=torch.full_like(price,float('nan')),bid=price,ask=price+.02,
                quote_valid=torch.rand(shape)>.3,capacity=torch.randint(0,30,(*shape,2)).double(),clock=torch.tensor(float(clock),dtype=torch.float64),
                terminal=torch.tensor(clock>40),entry_dollars=1000.,add_dollars=200.,fee_per_share=.005,minimum_order_fee=1.,cost=.0002,minimum_order_dollars=0.)
            expected=transition(p,o,mark=price,observed=torch.ones(shape,dtype=torch.bool),**args)
            names=('signals','ratios','policy','previous','membership','swing','bid','ask','quote_valid','capacity','clock','terminal')
            actual=gpu(pg,og,*(args[n].cuda() for n in names),1000.,200.,.005,1.,.0002,0.)
            for x,y in zip(expected,actual):torch.testing.assert_close(x,y.cpu(),rtol=0,atol=1.e-10)
            fused_p=pg.clone();fused_o=og.clone()
            snapshot=torch.cat((args['capacity'],torch.full((*shape,1),float(clock),dtype=torch.float64)),-1).cuda()
            fused=step(fused_p,fused_o,torch.zeros((*shape,3),device='cuda',dtype=torch.float64),
                torch.zeros(shape,device='cuda',dtype=torch.float64),torch.zeros((*shape,2),device='cuda',dtype=torch.float64),ag,
                args['signals'].cuda(),args['ratios'].cuda(),policy.cuda(),price.cuda(),price.cuda(),args['membership'].cuda(),
                args['swing'].cuda(),price.cuda(),args['ask'].cuda(),args['quote_valid'].cuda(),snapshot,
                args['clock'].cuda(),args['terminal'].cuda(),torch.tensor(True,device='cuda'),1000.,200.,.005,1.,.0002,0.)
            torch.testing.assert_close(fused_p,actual[0],rtol=0,atol=0)
            torch.testing.assert_close(fused_o,actual[1],rtol=0,atol=0)
            aggregate=account(p,expected[0],o,expected[1],aggregate,price,expected[2],expected[3])
            ag=gpu_account(pg,actual[0],og,actual[1],ag,price.cuda(),actual[3])
            torch.testing.assert_close(fused[0],ag,rtol=0,atol=0)
            torch.testing.assert_close(aggregate,ag.cpu(),rtol=1.e-12,atol=1.e-8)
            p,o=expected[:2];pg,og=actual[:2]

    def run_path(self,device,backend,candidates=2,active_only=False,repeated_quote=False,fast_orders=False,record_capacity=None,episode_capacity=32):
        from types import SimpleNamespace
        from unittest.mock import patch
        import numpy as np
        from research.vectorized_backtest.v59.order_replay import replay
        from research.vectorized_backtest.v59.evaluator import Execution
        from research.vectorized_backtest.v59.episodes import Episodes
        shape=(1,candidates,2)
        def zeros(*dims):return torch.zeros(dims,dtype=torch.float64,device=device)
        position=zeros(*shape,9);aggregate=zeros(1,candidates,16);aggregate[...,8]=float('inf')
        opened=zeros(*shape);ticker=zeros(*shape,2)
        policy=torch.tensor([.05,.5,.5,.05,0.,0.,2.,0.,0.],dtype=torch.float64,device=device)[None,None,None].expand(1,candidates,1,9)
        total=20 if fast_orders else 4
        prices=torch.full((total,1,1,2),10.,dtype=torch.float64,device=device)
        valid=torch.ones_like(prices,dtype=torch.bool)
        signals=torch.zeros(shape,dtype=torch.int16,device=device)
        plan=SimpleNamespace(ratios=torch.ones((*shape,3),dtype=torch.float64,device=device),feedback=zeros(candidates,2,1))
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
        execution=Execution(risk_limits_enabled=False,order_mode='persistent-quote-size-orders-v2',maximum_quote_age_seconds=None,minimum_order_fee=1.)
        stamps=torch.tensor([0,1,1,1] if repeated_quote else list(range(total)),device=device,dtype=torch.float64)[:,None,None,None].expand_as(prices)*1.e6
        size=100. if fast_orders else 30.
        capacity=torch.stack((torch.full_like(prices,size),torch.full_like(prices,size),stamps),-1)
        with patch('research.vectorized_backtest.v59.order_tail.Tail',Tail):
            result=replay(item=item,plan=plan,position=position,aggregate=aggregate,opened=opened,policy=policy,
                prices=prices,observed=valid,membership=valid,signals=signals,values=zeros(1,1),
                ids=torch.zeros((total,1,2),dtype=torch.int64,device=device),columns=torch.zeros(candidates,dtype=torch.int64,device=device),
                limits=zeros(1,candidates,1,5),ticker_state=ticker,bids=prices,asks=prices,quote_valid=valid,
                capacity=capacity,execution=execution,episodes=episodes,provide=provide,
                progress=None,block=2,gate=None,backend=backend,record_capacity=record_capacity)
        return position.cpu(),aggregate.cpu(),result,episodes.export()

    def test_complete_path_includes_tail_fills_and_drawdown(self):
        p,a,orders,episodes=self.run_path('cpu','eager')
        self.assertTrue((p[...,0]==0).all())
        self.assertEqual(len(orders['execution_orders'][0]),4)
        # 90 bought at 10, last 10 at 9; 100 sold at 9, $2 fees per ticker.
        torch.testing.assert_close(p[...,7],torch.full_like(p[...,7],-92.))
        torch.testing.assert_close(a[...,1],torch.full_like(a[...,1],184.))
        self.assertEqual(episodes[0]['episode_exit_clocks'],[[5,5],[5,5]])

    def test_tail_compaction_preserves_original_candidate_indices(self):
        p,a,orders,episodes=self.run_path('cpu','eager',64,True)
        self.assertEqual(len(orders['execution_orders'][0]),4)
        self.assertTrue(all(not row for row in orders['execution_orders'][1:]))
        self.assertTrue((p[:,1:]==0).all())
        self.assertEqual(episodes[0]['episode_exit_clocks'][0],[5,5])

    def test_repeated_quote_does_not_replenish_consumed_size(self):
        p,a,orders,episodes=self.run_path('cpu','eager',repeated_quote=True)
        torch.testing.assert_close(p[...,7],torch.full_like(p[...,7],-32.))
        self.assertEqual(episodes[0]['episode_exit_clocks'],[[7,7],[7,7]])
        self.assertEqual(orders['execution_orders'][0][0][-1],6.)

    def test_order_journal_retries_chunks_without_losing_or_duplicating_fills(self):
        full=self.run_path('cpu','eager',fast_orders=True)
        small=self.run_path('cpu','eager',fast_orders=True,record_capacity=4)
        torch.testing.assert_close(full[0],small[0],rtol=0,atol=0)
        self.assertEqual(full[2:],small[2:])
        self.assertEqual(len(small[2]['execution_orders'][0]),36)

    def test_position_pages_preserve_all_trades_during_chunk_retry(self):
        full=self.run_path('cpu','eager',fast_orders=True)
        small=self.run_path('cpu','eager',fast_orders=True,episode_capacity=4)
        torch.testing.assert_close(full[1],small[1],rtol=0,atol=0)
        self.assertEqual(full[2:],small[2:])
        self.assertEqual(len(small[3][0]['episode_profits'][0]),18)

    @unittest.skipUnless(__import__('os').environ.get('V59_TEST_CUDA')=='1','opt-in GPU qualification')
    def test_captured_path_matches_eager_and_repeats_exactly(self):
        eager=self.run_path('cpu','eager');first=self.run_path('cuda','compile');second=self.run_path('cuda','compile')
        for expected,actual in ((eager,first),(first,second)):
            torch.testing.assert_close(expected[0],actual[0],rtol=0,atol=1.e-12)
            torch.testing.assert_close(expected[1],actual[1],rtol=0,atol=1.e-12)
            self.assertEqual(expected[2:],actual[2:])
        cpu=self.run_path('cpu','eager',64,True);gpu=self.run_path('cuda','compile',64,True)
        torch.testing.assert_close(cpu[0],gpu[0],rtol=0,atol=1.e-12)
        torch.testing.assert_close(cpu[1],gpu[1],rtol=0,atol=1.e-12)
        self.assertEqual(cpu[2:],gpu[2:])
        cpu=self.run_path('cpu','eager',fast_orders=True,episode_capacity=4)
        gpu=self.run_path('cuda','compile',fast_orders=True,episode_capacity=4)
        torch.testing.assert_close(cpu[1],gpu[1],rtol=0,atol=1.e-12)
        self.assertEqual(cpu[2:],gpu[2:])
        cpu=self.run_path('cpu','eager',fast_orders=True,record_capacity=4)
        gpu=self.run_path('cuda','compile',fast_orders=True,record_capacity=4)
        torch.testing.assert_close(cpu[0],gpu[0],rtol=0,atol=1.e-12)
        self.assertEqual(cpu[2:],gpu[2:])
        cpu=self.run_path('cpu','eager',repeated_quote=True);gpu=self.run_path('cuda','compile',repeated_quote=True)
        torch.testing.assert_close(cpu[0],gpu[0],rtol=0,atol=1.e-12)
        self.assertEqual(cpu[2:],gpu[2:])


if __name__=='__main__':unittest.main()
