"""Bounded workstation compute, precision, and certified-loader qualification."""
import time,json
from pathlib import Path
import numpy as np
import torch
from .full_data import prepare,loader
from .full_step import Replay,forward,backward
from .model import Autoencoder,Config,PRICES
from .metrics import evaluate,require_structured
from ..phase1_v5_raw.engine import DeviceBatches
from ..v5.data_v3 import close_loader
from ..v5.shards.phase1_contract import write_json


def model():return Autoencoder(Config(variant='geometry_joint',width=2048,embedding=768),torch.zeros(18),torch.ones(18)).cuda()


def engineering(args):
    """Tiny overfit, captured/eager parity, and full-state serialization resume."""
    from ..phase2_v2.train import save_checkpoint,load_checkpoint,rng_state
    rows=np.load(args.data/'tiny.npy',allow_pickle=False)
    b=prepare(rows,compact=args.preparation=='gpu').to('cuda')
    m=model();other=model();other.load_state_dict(m.state_dict())
    opt=torch.optim.AdamW(m.parameters(),lr=3e-4,fused=True)
    eager_opt=torch.optim.AdamW(other.parameters(),lr=3e-4,fused=True)
    replay=Replay(m,b,args.precision)
    for _ in range(3):
        value,_,norm=replay(b);opt.step()
        eager_opt.zero_grad(set_to_none=True);ev,_,en=backward(other,b,args.precision);eager_opt.step()
        torch.testing.assert_close(value,ev,rtol=1e-5,atol=1e-7)
        for p,q in zip(m.parameters(),other.parameters()):torch.testing.assert_close(p,q,rtol=1e-5,atol=1e-7)
    initial=float(value);began=time.perf_counter()
    for _ in range(800):
        value,_,norm=replay(b)
        if not (torch.isfinite(value)&torch.isfinite(norm)):raise FloatingPointError('Tiny overfit')
        opt.step()
    final=float(value)
    if final>=initial*.2:raise AssertionError(f'Tiny overfit insufficient: {initial} -> {final}')
    contract=dict(test='full-state-engineering',precision=args.precision,preparation=args.preparation)
    path=args.output/'checkpoint_test.pt'
    save_checkpoint(path,dict(model=m.state_dict(),optimizer=opt.state_dict(),rng=rng_state(),contract=contract,step=803,cursor=(0,803)))
    saved=load_checkpoint(path,contract);other.load_state_dict(saved['model']);eager_opt.load_state_dict(saved['optimizer'])
    value,_,_=replay(b);opt.step()
    eager_opt.zero_grad(set_to_none=True);ev,_,_=backward(other,b,args.precision);eager_opt.step()
    for p,q in zip(m.parameters(),other.parameters()):torch.testing.assert_close(p,q,rtol=1e-5,atol=1e-7)
    result=dict(initial_loss=initial,final_loss=final,ratio=final/initial,seconds=time.perf_counter()-began,
        graph_eager_update_parity=True,checkpoint_resume_update_parity=True)
    write_json(args.output/'engineering.json',result);return result


def qualify(args):
    rows=np.load(args.data/'development.npy',allow_pickle=False)
    saved=torch.load(args.checkpoint,map_location='cpu',weights_only=False)
    m=model();m.load_state_dict(saved['model']);m.eval();result={};predictions={}
    for precision in ('fp32','bf16'):
        parts=[]
        with torch.no_grad():
            for start in range(0,len(rows),512):
                b=prepare(rows[start:start+512],compact=args.preparation=='gpu').to('cuda')
                parts.append(m.transform.inverse(forward(m,b,precision)).cpu().numpy())
        pred=np.concatenate(parts);metrics=evaluate(pred,rows);require_structured(metrics,paired=True)
        predictions[precision]=pred;result[precision]=metrics
    p=result['fp32'];q=result['bf16']
    result['bf16_accepted']=all(q[k]<=p[k]*1.02 for k in ('balanced_price_relative_mean','balanced_activity_log_mae','worst_price_band_relative_mean'))
    # Native quality gates by every nonempty frame/channel/price-band, not just aggregate.
    regressions=[]
    for f,pf in p['frames'].items():
        for c,pc in pf['channels'].items():
            qc=q['frames'][f]['channels'][c]
            for band,pb in pc.get('bands',{}).items():
                qb=qc['bands'][band]
                if pb['count'] and qb['mean']>pb['mean']*1.05+1e-5:
                    regressions.append(dict(frame=f,channel=c,band=band,fp32=pb['mean'],bf16=qb['mean']))
    result['bf16_group_regressions']=regressions
    result['bf16_accepted'] &= not bool(regressions)
    write_json(args.output/'precision.json',result)
    return dict(bf16_accepted=result['bf16_accepted'],regressions=len(regressions),
                fp32=p['balanced_price_relative_mean'],bf16=q['balanced_price_relative_mean'])


def compute(args):
    rows=np.load(args.data/'train.npy',mmap_mode='r',allow_pickle=False)
    results=[]
    for precision in ('fp32','bf16'):
        for batch in args.batches:
            for graph in (False,True):
                torch.cuda.empty_cache();torch.manual_seed(17);m=model();m.train()
                b=prepare(rows[:batch],compact=args.preparation=='gpu').to('cuda');opt=torch.optim.AdamW(m.parameters(),lr=3e-4,fused=True)
                began=time.perf_counter();replay=Replay(m,b,precision) if graph else None
                startup=time.perf_counter()-began
                def step():
                    if replay:loss,channels,norm=replay(b)
                    else:
                        opt.zero_grad(set_to_none=True);loss,channels,norm=backward(m,b,precision)
                    if not (torch.isfinite(loss)&torch.isfinite(norm)):raise FloatingPointError('Profile gradient/loss')
                    opt.step()
                    return float(loss)
                for _ in range(5):step()
                torch.cuda.synchronize();torch.cuda.reset_peak_memory_stats();began=time.perf_counter()
                initial=step()
                for _ in range(args.steps-1):last=step()
                torch.cuda.synchronize();seconds=time.perf_counter()-began
                result=dict(precision=precision,batch=batch,graph=graph,seconds=seconds,
                    windows_per_second=batch*args.steps/seconds,startup_seconds=startup,
                    peak_mib=torch.cuda.max_memory_allocated()/2**20,initial_loss=initial,last_loss=last)
                results.append(result);write_json(args.output/'compute.json',results);print(json.dumps(result),flush=True)
                del replay,opt,m,b
    return results


def stream(args,manifest,status):
    m=model();m.train();opt=torch.optim.AdamW(m.parameters(),lr=3e-4,fused=True)
    # Capture before starting pin-memory threads.
    rows=np.load(args.data/'train.npy',mmap_mode='r',allow_pickle=False)
    example=prepare(rows[:args.batch_size],compact=args.preparation=='gpu').to('cuda')
    replay=Replay(m,example,args.precision) if args.cuda_graph else None
    began=time.perf_counter();source=loader(manifest,args,'train');device=None
    seen=0;steady=None;initial_wait=0
    try:
        device=DeviceBatches(source,'cuda');startup=time.perf_counter()-began
        for i in range(args.steps+8):
            b=device.pop()
            if i==8:
                torch.cuda.synchronize();steady=time.perf_counter();initial_wait=device.wait_seconds
                torch.cuda.reset_peak_memory_stats()
            if replay:loss,channels,norm=replay(b)
            else:
                opt.zero_grad(set_to_none=True);loss,channels,norm=backward(m,b,args.precision)
            device.advance()
            if not (torch.isfinite(loss)&torch.isfinite(norm)):raise FloatingPointError('Stream loss/gradient')
            opt.step()
            if i>=8:seen+=len(b.target)
            if i%10==0:status(stage='profiling certified stream',profile_step=i,profile_total=args.steps+8)
        torch.cuda.synchronize();seconds=time.perf_counter()-steady
        result=dict(windows=seen,seconds=seconds,windows_per_second=seen/seconds,startup_seconds=startup,
            loader_wait_seconds=device.wait_seconds-initial_wait,peak_mib=torch.cuda.max_memory_allocated()/2**20,
            precision=args.precision,batch=args.batch_size,workers=args.workers,prefetch=args.prefetch,cuda_graph=args.cuda_graph)
        write_json(args.output/'stream.json',result);return result
    finally:
        if device:device.close()
        close_loader(source)
