"""Qualify the unchanged FP32 mixer and measure complete prefetched pipelines."""
import os
os.environ.update(PYTHONDONTWRITEBYTECODE="1", OMP_NUM_THREADS="1", MKL_NUM_THREADS="1")
import copy
import time
from pathlib import Path
import numpy as np
import torch
from .run_train import parser, build
from .execution import Replay, EvaluationReplay, backward
from .data import forward_batch
from .objectives import objective
from .metrics import Metrics
from .train import evaluate
from .eligibility import reasons
from ..phase1_v7.full_data import prepare_raw
from ..phase1_v11.pipeline import loader, DeviceBatches
from ..v5.data_v3 import close_loader
from ..v5.shards.phase1_contract import write_json
from ..phase1_v27.model import Autoencoder as Parent, Config as ParentConfig
from ..phase1_v8.runtime import preflight


def make_optimizer(model):
    encoder=[p for n,p in model.named_parameters() if p.requires_grad and n.startswith(('stem.','blocks.','readout.'))]
    mixer=[p for n,p in model.named_parameters() if p.requires_grad and not n.startswith(('stem.','blocks.','readout.'))]
    return torch.optim.AdamW([dict(params=encoder,lr=1e-5),dict(params=mixer,lr=1e-4)],weight_decay=0,fused=True)


def main():
    args=parser().parse_args()
    if args.output.exists() or not args.output.resolve().is_relative_to(Path('D:/TradingML/runtimes').resolve()):
        raise ValueError('Fresh runtime profiling directory required')
    preflight(args, desktop_paths=(r"c:\windows\system32\applicationframehost.exe",r"c:\windows\immersivecontrolpanel\systemsettings.exe"))
    args.preparation,args.shard_prefetch,args.shard_prefetch_mib='raw_gpu',16,64
    torch.set_num_threads(4)
    torch.manual_seed(args.seed)
    model,manifest,contract=build(args)
    initial=copy.deepcopy(model.state_dict())
    train_rows=np.load(args.data/'train.npy',mmap_mode='r')
    development=np.load(args.data/'development.npy',mmap_mode='r')
    tiny=prepare_raw(train_rows[:64],native=True).to('cuda')
    optimizer=make_optimizer(model)
    # Frozen identity equivalence is checked against the actual pinned parent.
    parent=Parent(dict(mean=model.mean,scale=model.scale,**model.get_extra_state()),
                  ParentConfig(**{k:v for k,v in model.config.__dict__.items() if k in ParentConfig.__dataclass_fields__})).cuda()
    parent.load_state_dict({k:v for k,v in initial.items() if k != 'mask_token'})
    model.eval()
    with torch.no_grad():
        a,values,valid=forward_batch(model,tiny)
        b,_,_=forward_batch(parent,tiny)
        torch.testing.assert_close(a['logs'],b['logs'],rtol=0,atol=0)
    model.train()
    losses=[]
    for _ in range(200):
        torch.manual_seed(29)
        optimizer.zero_grad(set_to_none=True)
        loss,norm,_,_=backward(model,tiny)
        assert bool(torch.isfinite(loss)&torch.isfinite(norm))
        optimizer.step();losses.append(float(loss))
    assert losses[-1]<losses[0], 'Tiny overfit gate failed'
    for name,value in model.state_dict().items():
        if name.startswith('decoder.') and isinstance(value,torch.Tensor):
            torch.testing.assert_close(value,initial[name],rtol=0,atol=0)
    assert not torch.equal(model.readout.weight,initial['readout.weight'])
    write_json(args.output/'overfit.json',dict(initial_loss=losses[0],final_loss=losses[-1],losses=losses,
                                            decoder_unchanged=True,encoder_updated=True,initial_identity_exact=True))
    del optimizer,parent,tiny
    model.load_state_dict(initial)
    batch=prepare_raw(train_rows[:256],native=True).to('cuda')
    model.zero_grad(set_to_none=True)
    torch.manual_seed(29)
    expected=backward(model,batch)
    gradients={n:p.grad.clone() for n,p in model.named_parameters() if p.requires_grad}
    torch.manual_seed(29)
    replay=Replay(model,batch);actual=replay(batch)
    torch.testing.assert_close(actual[0],expected[0],rtol=1e-6,atol=1e-7)
    for name,p in model.named_parameters():
        if p.requires_grad:torch.testing.assert_close(p.grad,gradients[name],rtol=1e-5,atol=1e-6)
    # Verify every metric on a real tail-inclusive development pass.
    model.eval()
    reference,candidate=Metrics('cuda'),Metrics('cuda')
    with torch.no_grad():
        evaluation_replay=None
        for start in range(0,len(development),1024):
            batch=prepare_raw(development[start:start+1024],native=True).to('cuda')
            out,v,k=forward_batch(model,batch);reference.add(model,out,v,k,batch)
            if len(batch.target)==1024:
                if evaluation_replay is None:evaluation_replay=EvaluationReplay(model,batch,candidate)
                evaluation_replay(batch)
            else:candidate.add(model,out,v,k,batch)
        a,b=reference.result(),candidate.result()
    for key,value in a['metrics'].items():
        if value is None:assert b['metrics'][key] is None
        else:np.testing.assert_allclose(b['metrics'][key],value,rtol=1e-8,atol=1e-7,err_msg=key)
    write_json(args.output/'parity.json',dict(metrics=len(a['metrics']),windows=len(development),passed=True,gradient_parity=True))
    write_json(args.output/'frozen-baseline-development.json',a)
    class Recorder:
        def update(self,**kwargs): pass
        def log(self,*args): pass
    rng=torch.cuda.get_rng_state()
    first=evaluate(model,manifest,args,Recorder(),0,development,'masked_development')
    second=evaluate(model,manifest,args,Recorder(),0,development,'masked_development')
    assert torch.equal(rng,torch.cuda.get_rng_state())
    for key,value in first['metrics'].items():
        if value is not None:np.testing.assert_allclose(second['metrics'][key],value,rtol=1e-8,atol=1e-7,err_msg=key)
    write_json(args.output/'masked-evaluation-parity.json',dict(passed=True,training_rng_preserved=True,metrics=len(first['metrics'])))
    del reference,candidate,replay,evaluation_replay,batch,model
    torch.cuda.empty_cache()
    results=[]
    for chunk,prefetch in ((262144,4),(131072,4),(65536,4)):
        batch_size,workers=4096,16
        args.chunk_bars,args.prefetch=chunk,prefetch
        torch.manual_seed(args.seed)
        model,_,_=build(args);model.load_state_dict(initial);model.train()
        args.batch_size,args.workers=batch_size,workers
        began=time.perf_counter()
        source=loader(manifest,args,'train',0)
        device=DeviceBatches(iter(source),'cuda')
        optimizer=make_optimizer(model)
        replay=None
        try:
            for step in range(230):
                if step==30:
                    torch.cuda.synchronize();startup=time.perf_counter()-began
                    began=time.perf_counter();waiting=device.wait_seconds
                    torch.cuda.reset_peak_memory_stats()
                batch=device.pop();optimizer.zero_grad(set_to_none=True)
                if replay is None:replay=Replay(model,batch)
                loss,norm,_,_=replay(batch)
                device.advance()
                assert bool(torch.isfinite(loss)&torch.isfinite(norm))
                optimizer.step()
                # Include the production eligibility accounting in the timing.
                low,high,missing=reasons(batch.grid,batch.valid)
                counts=torch.stack(((~(low|high|missing)).sum(),low.sum(),high.sum(),missing.sum()))
            torch.cuda.synchronize();seconds=time.perf_counter()-began
            result=dict(batch=batch_size,workers=workers,chunk_bars=chunk,prefetch=prefetch,windows_per_second=200*batch_size/seconds,
                        seconds=seconds,startup_seconds=startup,loader_wait_seconds=device.wait_seconds-waiting,
                        peak_gib=torch.cuda.max_memory_allocated()/2**30,final_loss=float(loss))
            results.append(result);print(result,flush=True)
            write_json(args.output/'profiles.json',dict(contract=contract,results=results))
        finally:device.close();close_loader(source)
        del model,optimizer,replay,batch
        torch.cuda.empty_cache()
    print('All gates passed; results',results,flush=True)


if __name__=='__main__':main()
