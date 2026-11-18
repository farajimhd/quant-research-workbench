"""Finite qualification and complete-pipeline profiling; never training resumes."""
import copy,time
import numpy as np
import torch
from .run import make_model
from .data import prepare_raw,loader,gpu_inputs
from .step import forward,backward,make_replay
from .model import learning_rate,select,inverse
from ..phase1_v5_raw.engine import DeviceBatches
from ..v5.data_v3 import close_loader
from ..v5.shards.phase1_contract import write_json
from ..phase2_v2.train import rng_state,restore_rng,save_checkpoint,load_checkpoint


def engineering(a,status):
    rows=np.load(a.data/'tiny.npy',allow_pickle=False)[:4];b=prepare_raw(rows,native=True).to(a.device)
    m=make_model(a);m.train();opt=torch.optim.AdamW(m.parameters(),lr=a.learning_rate,weight_decay=.01,fused=a.device=='cuda')
    # Actual source preparation and geometry roundtrip; valid examples unchanged.
    from ..phase1_v8.data import cache
    from ..phase1_v8.model import coordinates
    from ..joint_v9.model import structured
    values,valid,_=gpu_inputs(b);cpu=cache(rows)
    torch.testing.assert_close(values.cpu(),cpu['values'],rtol=2e-5,atol=2e-5)
    code,cm=coordinates(values[...,:18],valid[...,:18],'joint')
    torch.testing.assert_close(structured(code)[cm],values[...,:18][cm],rtol=2e-5,atol=2e-5)
    replay=make_replay(m,b) if a.device=='cuda' and a.cuda_graph else None
    initial=None;began=time.perf_counter()
    for step in range(a.tiny_steps):
        opt.param_groups[0]['lr']=learning_rate(step,a.tiny_steps,a.learning_rate,a.min_learning_rate)
        if replay:loss,channel,norm=replay(b)
        else:opt.zero_grad(set_to_none=True);loss,channel,norm=backward(m,b)
        if not (torch.isfinite(loss)&torch.isfinite(norm)):raise FloatingPointError('Tiny finite gate')
        if initial is None:initial=float(loss)
        opt.step()
        if step%50==0:status(stage='tiny masked overfit',step=step,loss=float(loss))
    final=float(loss)
    if final>=initial*.2:raise AssertionError(f'Tiny overfit insufficient: {initial} -> {final}')
    contract=dict(test='v10 engineering',config=vars(m.config));path=a.output/'engineering-checkpoint.pt'
    save_checkpoint(path,dict(model=m.state_dict(),optimizer=opt.state_dict(),rng=rng_state(),contract=contract,step=a.tiny_steps,cursor=(0,a.tiny_steps)))
    if replay:expected,_,_=replay(b)
    else:opt.zero_grad(set_to_none=True);expected,_,_=backward(m,b)
    opt.step();expected_state={k:v.detach().clone() for k,v in m.state_dict().items()}
    del replay,opt,m
    if a.device=='cuda':torch.cuda.empty_cache()
    saved=load_checkpoint(path,contract);m=make_model(a);m.load_state_dict(saved['model']);m.train()
    opt=torch.optim.AdamW(m.parameters(),lr=a.learning_rate,weight_decay=.01,fused=a.device=='cuda');opt.load_state_dict(saved['optimizer'])
    restore_rng(saved['rng']);replay=make_replay(m,b) if a.device=='cuda' and a.cuda_graph else None
    if replay:resumed,_,_=replay(b)
    else:opt.zero_grad(set_to_none=True);resumed,_,_=backward(m,b)
    opt.step();torch.testing.assert_close(expected,resumed,rtol=1e-5,atol=1e-6)
    for k,v in m.state_dict().items():torch.testing.assert_close(v,expected_state[k],rtol=1e-5,atol=1e-6)
    with torch.no_grad():
        m.eval();x,v,pos,_=select(values,valid,64);p=m(x,v,pos)[0]
        raw=inverse(p)
        if not torch.isfinite(raw).all() or (raw<0).any():raise FloatingPointError('Native precision gate')
        z=m.encoder(x,v,pos);encoder=copy.deepcopy(m.encoder);torch.testing.assert_close(z,encoder(x,v,pos),rtol=0,atol=0)
    result=dict(initial_loss=initial,final_loss=final,ratio=final/initial,seconds=time.perf_counter()-began,
        preparation_parity=True,geometry_roundtrip=True,checkpoint_optimizer_rng_resume_parity=True,standalone_encoder_parity=True,
        encoder_parameters=sum(p.numel() for p in m.encoder.parameters()),decoder_parameters=sum(p.numel() for p in m.decoder.parameters()))
    write_json(a.output/'engineering.json',result);return result


def compute(a,status):
    rows=np.load(a.data/'train.npy',mmap_mode='r',allow_pickle=False);results=[]
    for bs in a.batches:
        for graph in (False,True) if a.cuda_graph else (False,):
            m=opt=b=replay=None
            try:
                torch.manual_seed(a.seed);m=make_model(a).train();b=prepare_raw(rows[:bs]).to('cuda')
                opt=torch.optim.AdamW(m.parameters(),lr=a.learning_rate,weight_decay=.01,fused=True)
                began=time.perf_counter();replay=make_replay(m,b) if graph else None;startup=time.perf_counter()-began
                for step in range(a.steps+5):
                    if step==5:torch.cuda.synchronize();torch.cuda.reset_peak_memory_stats();began=time.perf_counter()
                    if replay:loss,channel,norm=replay(b)
                    else:opt.zero_grad(set_to_none=True);loss,channel,norm=backward(m,b)
                    if not (torch.isfinite(loss)&torch.isfinite(norm)):raise FloatingPointError('Compute finite gate')
                    opt.step()
                torch.cuda.synchronize();seconds=time.perf_counter()-began
                result=dict(batch=bs,graph=graph,state='complete',windows_per_second=bs*a.steps/seconds,seconds=seconds,
                            startup_seconds=startup,peak_mib=torch.cuda.max_memory_allocated()/2**20,loss=float(loss))
            except torch.cuda.OutOfMemoryError:result=dict(batch=bs,graph=graph,state='out-of-memory')
            finally:
                del m,opt,b,replay
                torch.cuda.empty_cache()
            results.append(result);write_json(a.output/'compute.json',results);status(stage='compute profile',latest=result);print(result,flush=True)
    return dict(configurations=results)


def stream(a,manifest,status):
    m=make_model(a).train();opt=torch.optim.AdamW(m.parameters(),lr=a.learning_rate,weight_decay=.01,fused=True)
    rows=np.load(a.data/'train.npy',mmap_mode='r',allow_pickle=False)
    example=prepare_raw(rows[:a.batch_size]).to('cuda');replay=make_replay(m,example) if a.cuda_graph else None;del example
    source=loader(manifest,a,'train');device=None;seen=0;began=time.perf_counter()
    try:
        device=DeviceBatches(source,'cuda');startup=time.perf_counter()-began
        for i in range(a.steps+8):
            b=device.pop()
            if i==8:torch.cuda.synchronize();began=time.perf_counter();wait=device.wait_seconds;torch.cuda.reset_peak_memory_stats()
            if replay and len(b.target)==a.batch_size:loss,channel,norm=replay(b)
            else:opt.zero_grad(set_to_none=True);loss,channel,norm=backward(m,b)
            device.advance()
            if not (torch.isfinite(loss)&torch.isfinite(norm)):raise FloatingPointError('Stream finite gate')
            opt.step()
            if i>=8:seen+=len(b.target)
            if i%20==0:status(stage='profiling full certified stream',step=i,profile_total=a.steps+8,loss=float(loss))
        torch.cuda.synchronize();seconds=time.perf_counter()-began
        result=dict(windows=seen,seconds=seconds,windows_per_second=seen/seconds,startup_seconds=startup,
            loader_wait_seconds=device.wait_seconds-wait,peak_mib=torch.cuda.max_memory_allocated()/2**20,
            batch=a.batch_size,workers=a.workers,prefetch=a.prefetch,cuda_graph=a.cuda_graph)
        write_json(a.output/'stream.json',result);return result
    finally:
        if device:device.close()
        close_loader(source)
