"""Disposable native qualification: actual frozen embeddings, bounded speed/overfit."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
os.environ.setdefault('POLARS_MAX_THREADS','1')
import argparse,copy,gc,json,statistics,time
from dataclasses import replace
from pathlib import Path
import torch
from .benchmark import sample_units,active_trainers
from .model import Forecaster,future_loss
from .tokenizer import load_teacher,SessionTokens
from .teacher_selection import select_training_teacher
from .data import checked_unit,plan_block
from .train import rng_state,restore_rng
from .cuda_step import CudaStep
from ..v5.shards.contract import write_json,digest


def real_examples(certificate,limit=4,origins=32):
    units,_=sample_units(certificate,32)
    units=[u for u in units if u.blocks[0][1]==0][:limit]
    if not units:raise ValueError('No certified training samples')
    path,sha,selection=select_training_teacher()
    teacher,proof=load_teacher(path,sha,'cuda',required_epoch=50)
    rows=[];evidence=[]
    for unit in units:
        reader=checked_unit(unit);plan=plan_block(reader,unit.blocks[0][0]);n=min(origins,len(plan.origins))
        small=replace(plan,origins=plan.origins[:n],endpoints=tuple(e[:n] for e in plan.endpoints),
                      targets=plan.targets[:n],origin_us=plan.origin_us[:n])
        session=SessionTokens(reader,teacher,sha,device='cuda',precision='bf16',memory_mib=1024,token_batch=2048)
        session.prepare(small);x,meta,y=session.batch(small,0,n)
        rows.append((x.clone(),meta.clone(),y.clone()))
        evidence.append(dict(path=str(unit.path),origins=n,block=unit.blocks[0][0],split=0,
            first=int(plan.origin_us[0]),last=int(plan.origin_us[n-1]),receipt=reader.receipt['hash']))
        del session
    return tuple(torch.cat([r[i] for r in rows]) for i in range(3)),dict(teacher=proof,samples=evidence),teacher,units


def update(model,opt,x,meta,y,captured=None):
    if captured is not None:pred,loss,norm=captured(x,meta,y)
    else:
        opt.zero_grad(set_to_none=True)
        with torch.autocast('cuda',dtype=torch.bfloat16):
            pred,ids=model.training_prediction(x,meta);loss=future_loss(pred,y,ids)
        loss.backward();norm=torch.nn.utils.clip_grad_norm_(model.parameters(),1.,error_if_nonfinite=True)
    if not bool(torch.isfinite(loss)&torch.isfinite(norm)):raise FloatingPointError('Nonfinite qualification')
    opt.step();return float(loss.detach())


def benchmark(examples):
    trials=[];x,meta,y=examples
    for batch in (512,1024,2048,4096):
        for capture in (False,True):
            torch.manual_seed(23);model=Forecaster().cuda().train()
            opt=torch.optim.AdamW(model.parameters(),lr=3e-4,weight_decay=.01,fused=True)
            copies=(batch+len(y)-1)//len(y)
            bx=x.repeat(copies,1,1)[:batch];bm=meta.repeat(copies,1,1)[:batch];by=y.repeat(copies,1)[:batch]
            graph=None
            try:
                torch.cuda.reset_peak_memory_stats()
                graph=CudaStep(model,batch,memory_mib=24000) if capture else None
                for _ in range(5):update(model,opt,bx,bm,by,graph)
                times=[]
                for _ in range(3):
                    torch.cuda.synchronize();start=time.perf_counter()
                    for _ in range(12):update(model,opt,bx,bm,by,graph)
                    torch.cuda.synchronize();times.append(time.perf_counter()-start)
                trials.append(dict(batch=batch,capture=capture,seconds=times,
                    origins_per_second=batch*12/statistics.median(times),
                    peak_gib=torch.cuda.max_memory_allocated()/2**30))
            except (torch.cuda.OutOfMemoryError,MemoryError) as e:
                trials.append(dict(batch=batch,capture=capture,error=type(e).__name__))
            finally:
                del graph,opt,model,bx,bm,by;gc.collect();torch.cuda.empty_cache()
    return trials


def graph_parity(examples):
    x,meta,y=(t[:16] for t in examples)
    torch.manual_seed(9);a=Forecaster().cuda();b=copy.deepcopy(a)
    oa=torch.optim.AdamW(a.parameters(),lr=3e-4,fused=True)
    ob=torch.optim.AdamW(b.parameters(),lr=3e-4,fused=True)
    graph=CudaStep(b,len(y))
    state=rng_state()
    for _ in range(4):update(a,oa,x,meta,y)
    after=rng_state();restore_rng(state)
    for _ in range(4):update(b,ob,x,meta,y,graph)
    from ..v5.checkpoint_transition_v1 import state_hash
    result=dict(model_exact=state_hash(a.state_dict())==state_hash(b.state_dict()),
        optimizer_exact=state_hash(oa.state_dict())==state_hash(ob.state_dict()),
        rng_exact=state_hash(after)==state_hash(rng_state()))
    result['passed']=all(result.values())
    del graph,oa,ob,a,b;gc.collect();torch.cuda.empty_cache()
    return result


@torch.no_grad()
def score(model,x,meta,y):
    model.eval()
    with torch.autocast('cuda',dtype=torch.bfloat16):_,p=model(x,meta)
    mse=float((p.float()-y.float()).square().mean());model.train()
    return mse


def overfit(examples,seconds=180):
    x,meta,y=examples;torch.manual_seed(31);model=Forecaster().cuda()
    opt=torch.optim.AdamW(model.parameters(),lr=1e-3,weight_decay=.01,fused=True)
    initial=score(model,x,meta,y);mean=float((y-y.mean(0)).square().mean())
    history=[];start=time.perf_counter();steps=0
    while steps<2500 and time.perf_counter()-start<seconds:
        loss=update(model,opt,x,meta,y);steps+=1
        if steps%100==0:
            value=score(model,x,meta,y);history.append(dict(step=steps,masked_loss=loss,full_mse=value))
            if value<initial*.05 and value<mean*.5:break
    final=score(model,x,meta,y)
    result=dict(initial_mse=initial,final_mse=final,training_mean_mse=mean,steps=steps,
        seconds=time.perf_counter()-start,reduction_pct=100*(1-final/initial),history=history,
        passed=final<initial*.2 and final<mean,scope='repeated small actual-shard training set, not generalization')
    del opt,model;gc.collect();torch.cuda.empty_cache();return result


def main(argv=None):
    p=argparse.ArgumentParser();p.add_argument('--dataset-release',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--part',choices=('profile','overfit'),required=True)
    a=p.parse_args(argv)
    if active_trainers():raise RuntimeError('Another trainer is active')
    if not a.output.resolve().is_relative_to(Path('D:/TradingML/runtimes').resolve()):raise ValueError('Use runtime')
    if a.output.exists():raise FileExistsError('Use a new qualification output')
    a.output.mkdir(parents=True);torch.set_num_threads(1)
    examples,evidence,teacher,units=real_examples(a.dataset_release)
    result=dict(evidence=evidence,part=a.part)
    if a.part=='profile':result.update(graph_parity=graph_parity(examples),trials=benchmark(examples))
    else:result['overfit']=overfit(examples)
    write_json(a.output/'result.json',result);print(json.dumps(result),flush=True)

if __name__=='__main__':main()
