"""Profile processing chunks without changing the optimizer batch or model."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import copy,time
from dataclasses import replace
import numpy as np
import torch
from .run_train import parser,build
from .model import Autoencoder
from .execution import Replay,backward
from .profile import make_optimizer
from .train import evaluate
from ..phase1_v8.runtime import preflight
from ..phase1_v7.full_data import prepare_raw
from ..phase1_v11.pipeline import loader,DeviceBatches
from ..v5.data_v3 import close_loader
from ..v5.shards.phase1_contract import write_json


def main():
    args=parser().parse_args()
    preflight(args,desktop_paths=(r'c:\windows\system32\applicationframehost.exe',r'c:\windows\immersivecontrolpanel\systemsettings.exe'))
    args.preparation,args.shard_prefetch,args.shard_prefetch_mib='raw_gpu',16,64
    torch.set_num_threads(4);torch.manual_seed(17)
    original,manifest,contract=build(args)
    initial=copy.deepcopy(original.state_dict());config=original.config
    parameters=dict(mean=original.mean,scale=original.scale,**original.get_extra_state())
    rows=np.load(args.data/'train.npy',mmap_mode='r')
    batch=prepare_raw(rows[:4096],native=True).to('cuda')
    torch.manual_seed(31);expected=backward(original,batch)
    gradients={n:p.grad.clone() for n,p in original.named_parameters() if p.requires_grad}
    expected_loss=float(expected[0]);del original,expected
    results=[]
    for bars,windows in ((262144,4096),(65536,4096),(131072,2048),(65536,1024),(32768,512),(65536,512)):
        model=Autoencoder(parameters,replace(config,chunk_bars=bars,mixer_chunk_windows=windows)).cuda()
        model.load_state_dict(initial);model.train();torch.manual_seed(31)
        replay=Replay(model,batch);loss,norm,_,_=replay(batch)
        np.testing.assert_allclose(float(loss),expected_loss,rtol=1e-6,atol=1e-5)
        for name,p in model.named_parameters():
            if p.requires_grad:torch.testing.assert_close(p.grad,gradients[name],rtol=5e-4,atol=2e-5)
        optimizer=make_optimizer(model)
        for _ in range(10):
            replay(batch);optimizer.step()
        torch.cuda.synchronize();start=time.perf_counter()
        for _ in range(100):
            loss,norm,_,_=replay(batch)
            if not bool(torch.isfinite(loss)&torch.isfinite(norm)):raise FloatingPointError('Nonfinite profile')
            optimizer.step()
        torch.cuda.synchronize();seconds=time.perf_counter()-start
        row=dict(chunk_bars=bars,mixer_chunk_windows=windows,cached_windows_per_second=409600/seconds,gradient_parity=True)
        results.append(row);print(row,flush=True)
        del replay,optimizer,model
        torch.cuda.empty_cache()
    selected=max(results,key=lambda x:x['cached_windows_per_second'])
    model=Autoencoder(parameters,replace(config,chunk_bars=selected['chunk_bars'],mixer_chunk_windows=selected['mixer_chunk_windows'])).cuda()
    model.load_state_dict(initial);model.train()
    source=loader(manifest,args,'train',0);device=DeviceBatches(iter(source),'cuda')
    optimizer=make_optimizer(model);replay=None
    try:
        for step in range(330):
            if step==30:
                torch.cuda.synchronize();start=time.perf_counter();wait=device.wait_seconds
            b=device.pop()
            if replay is None:replay=Replay(model,b)
            loss,norm,_,_=replay(b);device.advance()
            if not bool(torch.isfinite(loss)&torch.isfinite(norm)):raise FloatingPointError('Nonfinite profile')
            optimizer.step()
        torch.cuda.synchronize();seconds=time.perf_counter()-start
        pipeline=dict(windows_per_second=300*4096/seconds,loader_wait_seconds=device.wait_seconds-wait)
    finally:device.close();close_loader(source)
    class Reporter:
        def update(self,**kwargs):pass
        def log(self,*args):pass
    development=np.load(args.data/'development.npy',mmap_mode='r')
    evaluations=[]
    for scope in ('development','development','masked_development','masked_development'):
        report=evaluate(model,manifest,args,Reporter(),0,development,scope)
        evaluations.append(dict(scope=scope,seconds=report['seconds'],windows_per_second=report['windows_per_second']))
    write_json(args.output/'profiles.json',dict(candidates=results,selected=selected,pipeline=pipeline,evaluations=evaluations))
    print(dict(selected=selected,pipeline=pipeline,evaluations=evaluations),flush=True)


if __name__=='__main__':main()
