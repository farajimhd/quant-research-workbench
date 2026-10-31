import unittest,os
import torch
from research.vectorized_backtest.v60.orders import transition,ORDER_WIDTH

@unittest.skipUnless(os.environ.get("V60_TEST_CUDA")=="1", "opt-in GPU qualification")
class GPUParity(unittest.TestCase):
    def test_ledger_pack_is_exact_for_strided_and_integer_columns(self):
        from research.vectorized_backtest.v60.ledger_pack import pack
        counts=torch.tensor([0,73,1,34,12],device='cuda',dtype=torch.int64)
        raw=torch.arange(5*73*9,device='cuda',dtype=torch.float64).reshape(5,73,9)/3
        fields=[raw[...,i] for i in range(9)]
        fields[2]=fields[2].long()
        mask=torch.arange(73,device='cuda')[None]<counts[:,None]
        expected=torch.stack([f.double() for f in fields],-1)[mask]
        actual=pack(fields,counts,counts.cpu().numpy())
        torch.testing.assert_close(actual,expected,rtol=0,atol=0)

    def test_feedforward_action_scope_matches_cpu(self):
        import numpy as np
        from types import SimpleNamespace
        from research.vectorized_backtest.v60.genome import sample
        from research.vectorized_backtest.v60.features import CATALOG
        from research.vectorized_backtest.v60.network import NetworkPlan
        members=sample(np.random.default_rng(188),5);u=37;c=len(CATALOG)
        torch.manual_seed(93)
        v=torch.rand((u,3*c))*.8-.4;known=torch.rand((u,c))>.15
        v[:,2*c:]=known.float()
        plans=[NetworkPlan(members,device) for device in ('cpu','cuda')]
        position=torch.zeros((1,5,u,9),dtype=torch.float64)
        position[0,::2,:,0]=1.;position[...,1]=10.;position[...,2]=9.
        opened=torch.zeros((1,5,u),dtype=torch.float64);previous=torch.full((1,1,u),10.,dtype=torch.float64)
        results=[]
        for plan in plans:
            device=plan.device
            plan.project=lambda data,index,device=device:(v.to(device),known.to(device))
            data=SimpleNamespace(resident_features=SimpleNamespace(tensors={'clocks':torch.arange(2,device=device)}),
                tensors={'membership':torch.ones((2,u),device=device,dtype=torch.bool)})
            results.append(plan.signals(data,torch.tensor(1,device=device),position.to(device),opened.to(device),previous.to(device)).cpu())
        torch.testing.assert_close(results[0],results[1],rtol=0,atol=0)
        # Positions cannot affect any head in the new market-only contract.
        for plan in plans:
            changed=position.clone();changed[...,0]=900.;changed[...,1]=.1;changed[...,7]=-10000.
            device=plan.device
            data=SimpleNamespace(resident_features=SimpleNamespace(tensors={'clocks':torch.arange(2,device=device)}),
                tensors={'membership':torch.ones((2,u),device=device,dtype=torch.bool)})
            actual=plan.signals(data,torch.tensor(1,device=device),changed.to(device),torch.full_like(opened,-900).to(device),previous.to(device)).cpu()
            torch.testing.assert_close(actual,results[0],rtol=0,atol=0)
        for plan in plans:plan.close()

    def test_paired_order_append_preserves_rows_and_overflow_counts(self):
        from research.vectorized_backtest.v60.order_records import OrderRecords
        from research.vectorized_backtest.v60.order_scatter import append
        torch.manual_seed(72)
        for capacity in (16,1024):
            old=OrderRecords(5,137,'cuda',capacity=capacity)
            new=OrderRecords(5,137,'cuda',capacity=capacity)
            for _ in range(4):
                record=torch.rand((1,5,137,17),dtype=torch.float64,device='cuda')
                record[...,16]=(record[...,16]>.7).double()
                complete=torch.rand((1,5,137),device='cuda')>.6
                append(old,record[...,16]>0,record[...,8:16])
                append(old,complete,record[...,:8])
                new.append(complete,record)
                torch.testing.assert_close(old.counts,new.counts,rtol=0,atol=0)
                for row,count in enumerate(old.counts.tolist()):
                    torch.testing.assert_close(old.data[row,:min(count,capacity)],new.data[row,:min(count,capacity)],rtol=0,atol=0)

    def test_fused_orders_match_randomized_cpu_oracle_each_clock(self):
        from research.vectorized_backtest.v60.order_transition import transition as gpu
        from research.vectorized_backtest.v60.order_accounting import account
        from research.vectorized_backtest.v60.order_accounting_cuda import account as gpu_account
        from research.vectorized_backtest.v60.order_step import step
        compiled_step=torch.compile(step,backend='eager',fullgraph=True)
        torch.manual_seed(419)
        shape=(1,5,137);p=torch.zeros((*shape,9),dtype=torch.float64);o=torch.zeros((*shape,ORDER_WIDTH),dtype=torch.float64)
        pg=p.cuda();og=o.cuda()
        aggregate=torch.zeros((1,5,16),dtype=torch.float64);aggregate[...,8]=float('inf');ag=aggregate.cuda()
        policy=torch.tensor([.05,.03,.4,.03,0.,0.,4.,2.,0.,.005,-.005],dtype=torch.float64)[None,None,None].expand(1,5,1,11).clone()
        policy[:,::2,:,8]=2
        diagnostic=torch.zeros((5,2,16),dtype=torch.int64);gpu_diagnostic=diagnostic.cuda()
        for clock in range(1,65):
            price=10+torch.rand(shape,dtype=torch.float64)
            args=dict(signals=torch.randint(0,32,shape,dtype=torch.int16),ratios=torch.rand((*shape,3),dtype=torch.float64),policy=policy,
                previous=price,membership=torch.rand(shape)>.1,swing=torch.full_like(price,10.5),bid=price,ask=price+.02,
                quote_valid=torch.rand(shape)>.3,capacity=torch.randint(0,30,(*shape,2)).double(),clock=torch.tensor(float(clock),dtype=torch.float64),
                terminal=torch.tensor(clock>40),entry_dollars=1000.,add_dollars=200.,fee_per_share=.005,minimum_order_fee=1.,cost=.0002,minimum_order_dollars=0.)
            args['capacity'][...,::3,:]=5.684341886080802e-14
            expected=transition(p,o,mark=price,observed=torch.ones(shape,dtype=torch.bool),diagnostic=diagnostic,**args)
            names=('signals','ratios','policy','previous','membership','swing','bid','ask','quote_valid','capacity','clock','terminal')
            actual=gpu(pg,og,*(args[n].cuda() for n in names),1000.,200.,.005,1.,.0002,0.)
            for x,y in zip(expected,actual):torch.testing.assert_close(x,y.cpu(),rtol=0,atol=1.e-10)
            fused_p=pg.clone();fused_o=og.clone()
            snapshot=torch.cat((args['capacity'],torch.full((*shape,1),float(clock),dtype=torch.float64)),-1).cuda()
            counters=gpu_diagnostic if clock<64 else torch.empty(0,device='cuda',dtype=torch.int64)
            fused=compiled_step(fused_p,fused_o,torch.zeros((*shape,3),device='cuda',dtype=torch.float64),
                torch.zeros(shape,device='cuda',dtype=torch.float64),torch.zeros((*shape,2),device='cuda',dtype=torch.float64),ag,
                args['signals'].cuda(),args['ratios'].cuda(),policy.cuda(),price.cuda(),price.cuda(),args['membership'].cuda(),
                args['swing'].cuda(),price.cuda(),args['ask'].cuda(),args['quote_valid'].cuda(),snapshot,
                args['clock'].cuda(),args['terminal'].cuda(),torch.tensor(True,device='cuda'),1000.,200.,.005,1.,.0002,0.,counters)
            if clock<64:torch.testing.assert_close(diagnostic,gpu_diagnostic.cpu(),rtol=0,atol=0)
            from research.vectorized_backtest.v60.admission import export
            export(gpu_diagnostic)
            torch.testing.assert_close(fused_p,actual[0],rtol=0,atol=0)
            torch.testing.assert_close(fused_o,actual[1],rtol=0,atol=0)
            torch.testing.assert_close(fused[4],actual[2],rtol=0,atol=0)
            torch.testing.assert_close(fused[5][...,16],actual[3][...,16],rtol=0,atol=0)
            # Fused records have payloads only for flagged journal events.
            for mask,start in ((fused[4],0),(fused[5][...,16]>0,8)):
                torch.testing.assert_close(fused[5][...,start:start+8][mask],actual[3][...,start:start+8][mask],rtol=0,atol=0)
            aggregate=account(p,expected[0],o,expected[1],aggregate,price,expected[2],expected[3])
            ag=gpu_account(pg,actual[0],og,actual[1],ag,price.cuda(),actual[3])
            torch.testing.assert_close(fused[0],ag,rtol=0,atol=0)
            torch.testing.assert_close(aggregate,ag.cpu(),rtol=1.e-12,atol=1.e-8)
            p,o=expected[:2];pg,og=actual[:2]
