"""Explicit bounded CUDA qualification; refuses another active BarGPT trainer."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json,time
from pathlib import Path
from types import SimpleNamespace
import torch
from torch.nn.attention import sdpa_kernel,SDPBackend
from ..phase2_v2.benchmark import active_trainers
from ..phase2_v2.data import load_units
from ..phase2_v2.tokenizer import load_teacher
from ..phase2_v2.teacher_selection import select_training_teacher
from ..v5.checkpoint_transition_v1 import state_hash
from .model import Forecaster
from .targets import loss
from .data import batches
from .cuda_step import CudaStep


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--dataset-release',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args(argv)
    if a.output.exists():raise FileExistsError(a.output)
    if active_trainers():raise RuntimeError('Another BarGPT trainer owns the GPU')
    if not a.output.resolve().is_relative_to(Path('D:/TradingML/runtimes').resolve()):raise ValueError('Runtime output required')
    torch.set_num_threads(1)
    units,release=load_units(release=a.dataset_release)
    path,sha,_=select_training_teacher();teacher,proof=load_teacher(path,sha,'cuda',required_epoch=50)
    args=SimpleNamespace(device='cuda',precision='bf16',batch_size=1024,cache_mib=1024,
        token_batch=2048,prepare_workers=4,io_workers=4,prefetch_mib=8192,pack_batches=True)
    source=batches(units,0,teacher,sha,args)
    try:x,meta,target,cursor,_,_=next(source)
    finally:source.close()
    torch.cuda.synchronize();outcomes={};trials=[]
    for capture in (False,True):
        torch.manual_seed(71);model=Forecaster().cuda();opt=torch.optim.AdamW(model.parameters(),lr=3e-4,weight_decay=.01,fused=True)
        graph=CudaStep(model,1024,attention_backend='cudnn') if capture else None
        def update():
            if graph is not None:pred,value,norm=graph(x,meta,target)
            else:
                opt.zero_grad(set_to_none=True)
                with sdpa_kernel(SDPBackend.CUDNN_ATTENTION),torch.autocast('cuda',dtype=torch.bfloat16):
                    pred=model(x,None)[1];value=loss(pred,target)
                value.backward();norm=torch.nn.utils.clip_grad_norm_(model.parameters(),1.,error_if_nonfinite=True)
            if not (torch.isfinite(value)&torch.isfinite(norm)):raise FloatingPointError('Nonfinite qualification update')
            opt.step()
            return value
        for _ in range(8):update()
        torch.cuda.synchronize();start=time.perf_counter()
        for _ in range(48):value=update()
        torch.cuda.synchronize();elapsed=time.perf_counter()-start
        outcomes[capture]=dict(model=state_hash(model.state_dict()),optimizer=state_hash(opt.state_dict()),
            rng=state_hash(dict(cpu=torch.get_rng_state(),cuda=torch.cuda.get_rng_state_all())))
        trials.append(dict(capture=capture,seconds=elapsed,origins_per_second=48*1024/elapsed,loss=float(value.detach())))
        del graph,opt,model;torch.cuda.empty_cache()
    result=dict(passed=outcomes[False]==outcomes[True],exact_state=outcomes,release=release,teacher=proof,
        cursor=cursor,trials=trials,scope='student eager/capture same-input qualification; not sustained corpus speed')
    a.output.write_text(json.dumps(result,indent=2));print(json.dumps(result),flush=True)
    if not result['passed']:raise AssertionError('Direct-bars CUDA state parity failed')


if __name__=='__main__':main()
