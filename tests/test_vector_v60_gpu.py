import unittest,os
import torch
from research.vectorized_backtest.v60.orders import transition,ORDER_WIDTH

@unittest.skipUnless(os.environ.get("V60_TEST_CUDA")=="1", "opt-in GPU qualification")
class GPUParity(unittest.TestCase):
    def test_fused_orders_match_randomized_cpu_oracle_each_clock(self):
        from research.vectorized_backtest.v60.order_transition import transition as gpu
        from research.vectorized_backtest.v60.order_accounting import account
        from research.vectorized_backtest.v60.order_accounting_cuda import account as gpu_account
        from research.vectorized_backtest.v60.order_step import step
        torch.manual_seed(419)
        shape=(1,5,137);p=torch.zeros((*shape,9),dtype=torch.float64);o=torch.zeros((*shape,ORDER_WIDTH),dtype=torch.float64)
        pg=p.cuda();og=o.cuda()
        aggregate=torch.zeros((1,5,16),dtype=torch.float64);aggregate[...,8]=float('inf');ag=aggregate.cuda()
        policy=torch.tensor([.05,.03,.4,.03,0.,0.,4.,2.,0.,.005,-.005],dtype=torch.float64)[None,None,None].expand(1,5,1,11).clone()
        policy[:,::2,:,8]=2
        for clock in range(1,65):
            price=10+torch.rand(shape,dtype=torch.float64)
            args=dict(signals=torch.randint(0,32,shape,dtype=torch.int16),ratios=torch.rand((*shape,3),dtype=torch.float64),policy=policy,
                previous=price,membership=torch.rand(shape)>.1,swing=torch.full_like(price,10.5),bid=price,ask=price+.02,
                quote_valid=torch.rand(shape)>.3,capacity=torch.randint(0,30,(*shape,2)).double(),clock=torch.tensor(float(clock),dtype=torch.float64),
                terminal=torch.tensor(clock>40),entry_dollars=1000.,add_dollars=200.,fee_per_share=.005,minimum_order_fee=1.,cost=.0002,minimum_order_dollars=0.)
            args['capacity'][...,::3,:]=5.684341886080802e-14
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
