"""Bounded laptop GPU validation; synthetic evidence is never profitability.

python -m research.rl_trading.v6.tensor_profile --runtime-root D:/TradingML/runtimes
"""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse
from datetime import date
import json
from pathlib import Path
import time
import numpy as np
import torch
from research.rl_trading.v1.common import bounds
from research.rl_trading.v6.bank import SessionBank
from research.rl_trading.v6.session_data import PackedSession
from research.rl_trading.v6.ranked_policy import RankedBracketActorCritic
from research.rl_trading.v6.market_attention import MarketAttentionConfig
from research.rl_trading.v6.tensor_broker import TensorBroker,BrokerBucket
from research.rl_trading.v6.tensor_rollout import collect_tensor_session
from research.rl_trading.v6.rollout import audit_reconstruction,update_session
from research.rl_trading.v6.actor_critic import elapsed_gae


def main(argv=None):
    parser=argparse.ArgumentParser(__doc__)
    parser.add_argument('--runtime-root',type=Path,required=True)
    parser.add_argument('--listings',type=int,default=1000)
    parser.add_argument('--broker-seconds',type=int,default=600)
    parser.add_argument('--policy-clocks',type=int,default=32)
    args=parser.parse_args(argv)
    if not args.runtime_root.is_dir() or min(args.listings,args.broker_seconds,args.policy_clocks)<2:
        raise ValueError('Existing runtime and positive bounded workload required')
    if not torch.cuda.is_available():raise RuntimeError('CUDA profile required')
    root=args.runtime_root/'rl-v6-tensor-profile'/str(time.time_ns())
    root.mkdir(parents=True)
    os.environ['TORCHINDUCTOR_CACHE_DIR']=str(root/'inductor')
    os.environ['TRITON_CACHE_DIR']=str(root/'triton')
    device=torch.device('cuda');n=args.listings
    f=torch.full((n,),10.,device=device,dtype=torch.float64)
    mask=torch.ones(n,device=device,dtype=torch.bool)
    def row(clock):
        return BrokerBucket(clock,f,f*100,f,f,f*0,mask,mask,mask,~mask)
    results={'evidence':'synthetic workload, not historical throughput or profitability',
             'gpu':torch.cuda.get_device_name(),'listings':n}
    states=[]
    for compiled in (False,True):
        broker=TensorBroker(n,device=device)
        broker.mark.fill_(10);broker.quantity.fill_(1);broker.cost.fill_(10)
        broker.side.fill_(2);broker.remaining.fill_(1);broker.order_quantity.fill_(1)
        if compiled:broker.compile_step()
        started=time.perf_counter()
        for k in range(1,101):broker.advance(row(k*100_000),dense=True)
        torch.cuda.synchronize();startup=time.perf_counter()-started
        started=time.perf_counter()
        for k in range(101,101+args.broker_seconds*10):broker.advance(row(k*100_000),dense=True)
        torch.cuda.synchronize()
        results['compiled' if compiled else 'eager']={
            'warmup_seconds':startup,'simulated_seconds':args.broker_seconds,
            'steady_seconds':time.perf_counter()-started}
        states.append({name:getattr(broker,name).clone() for name in
            ('quantity','cash','realized','fees','closed','remaining','shaping')})
    for name in states[0]:
        if not torch.allclose(states[0][name],states[1][name],atol=1e-8,rtol=1e-12):
            raise AssertionError(f'Compiled broker account mismatch: {name}')
    results['compiled_account_parity']=True
    rewards=torch.randn(60000,device=device)*.01;values=rewards*.3
    terminated=torch.zeros_like(rewards,dtype=torch.bool);elapsed=torch.ones_like(rewards)
    gae=[]
    for parallel in (False,True):
        torch.cuda.synchronize();started=time.perf_counter()
        advantage,_=elapsed_gae(rewards,values,terminated,elapsed,bootstrap=rewards.new_zeros(()),parallel_scan=parallel)
        torch.cuda.synchronize()
        results['gae_parallel' if parallel else 'gae_serial_seconds']=time.perf_counter()-started
        gae.append(advantage)
    results['gae_maximum_error']=float((gae[0]-gae[1]).abs().max())
    # Real actor, collection and optimizer; fixture candles are explicitly synthetic.
    day=date(2026,8,18);start=bounds(day)[0]+1_000_000;t=args.policy_clocks+1
    scalar=np.zeros((n*t,37),np.float32)
    scalar[:,3]=np.log(10);scalar[:,8]=np.log1p(10000);scalar[:,35]=1
    clocks=np.tile(np.arange(start,start+t*1_000_000,1_000_000,dtype=np.int64),n)
    names=tuple(f'FIXTURE{i}' for i in range(n))
    bank=SessionBank(Path('synthetic'),{'offsets':{name:[i*t,(i+1)*t] for i,name in enumerate(names)}},
        clocks,scalar,np.zeros((n*t,2,5,11),np.float32))
    session=PackedSession(day,'train',Path('synthetic'),'synthetic',bank,None,names)
    torch.manual_seed(19)
    policy=RankedBracketActorCritic(config=MarketAttentionConfig(top_r=min(n,1000))).to(device)
    broker=TensorBroker(n,device=device);broker.compile_step()
    started=time.perf_counter()
    collection=collect_tensor_session(policy,session,broker,
        (row(c) for c in range(start+100_000,start+args.policy_clocks*1_000_000+1,100_000)),
        device=device,max_clocks=args.policy_clocks)
    torch.cuda.synchronize();results['actor_collection_seconds']=time.perf_counter()-started
    results['reconstruction']=audit_reconstruction(policy,session,collection.frames,device=device)
    initial={k:v.detach().clone() for k,v in policy.state_dict().items()}
    final=[]
    for optimized in (False,True):
        policy.load_state_dict(initial)
        optimizer=torch.optim.Adam(policy.parameters(),lr=3e-4)
        started=time.perf_counter()
        metrics=update_session(policy,optimizer,session,collection.frames,collection.steps,device=device,
            epochs=1,clocks_per_chunk=8,decoder_batch_size=8,
            bootstrap=collection.bootstrap,batch_candle_projection=optimized)
        torch.cuda.synchronize()
        results['chunk_projection' if optimized else 'per_clock_projection']={
            'seconds':time.perf_counter()-started,'metrics':metrics}
        final.append({k:v.detach().clone() for k,v in policy.state_dict().items()})
    results['optimizer_parameter_maximum_error']=max(float((final[0][k]-final[1][k]).abs().max()) for k in final[0])
    results['policy_clocks']=args.policy_clocks
    (root/'complete.json').write_text(json.dumps(results,indent=2))
    print(json.dumps({'root':str(root),**results},indent=2))


if __name__=='__main__':main()
