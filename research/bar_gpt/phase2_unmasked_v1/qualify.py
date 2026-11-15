"""Same-input, fresh-state BF16 eager/capture comparison at retained batch1024."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,copy,json,time,statistics
from pathlib import Path
import torch
from torch.nn.attention import sdpa_kernel,SDPBackend
from .model import Forecaster,future_loss
from ..phase2_v2.cuda_step import CudaStep
from ..phase2_v2.benchmark import active_trainers
from ..v5.shards.contract import file_hash
from ..v5.checkpoint_transition_v1 import state_hash


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--examples',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args(argv)
    if a.output.exists():raise FileExistsError(a.output)
    if active_trainers():raise RuntimeError('Another trainer owns the GPU')
    torch.set_num_threads(1)
    if file_hash(a.examples)!=a.examples.with_suffix('.sha256').read_text().strip():raise ValueError('Examples changed')
    data=torch.load(a.examples,map_location='cuda',weights_only=False)
    x,y=data['train_x'].float(),data['train_y'].float()
    x=x.repeat((1024+len(x)-1)//len(x),1,1)[:1024]
    y=y.repeat((1024+len(y)-1)//len(y),1)[:1024]
    meta=torch.zeros(1024,16,9,device='cuda')
    trials=[];parity=None
    for repeat in range(3):
        outcomes={};states={}
        for capture in ((False,True) if repeat%2==0 else (True,False)):
            torch.manual_seed(71);model=Forecaster().cuda().train()
            opt=torch.optim.AdamW(model.parameters(),lr=3e-4,weight_decay=.01,fused=True)
            graph=CudaStep(model,1024,attention_backend='cudnn') if capture else None
            losses=[]
            def update():
                if graph is not None:pred,loss,norm=graph(x,meta,y)
                else:
                    opt.zero_grad(set_to_none=True)
                    with sdpa_kernel(SDPBackend.CUDNN_ATTENTION),torch.autocast('cuda',dtype=torch.bfloat16):
                        pred,ids=model.training_prediction(x,None);loss=future_loss(pred,y,ids)
                    loss.backward();norm=torch.nn.utils.clip_grad_norm_(model.parameters(),1.,error_if_nonfinite=True)
                if not (torch.isfinite(loss)&torch.isfinite(norm)):raise FloatingPointError('Qualification')
                opt.step()
                return loss.detach()
            for _ in range(8):update()
            torch.cuda.synchronize();start=time.perf_counter()
            for _ in range(48):loss=update()
            torch.cuda.synchronize();seconds=time.perf_counter()-start
            trials.append(dict(repeat=repeat,capture=capture,seconds=seconds,origins_per_second=48*1024/seconds,loss=float(loss)))
            outcomes[capture]={k:v.detach().cpu().clone() for k,v in model.state_dict().items()}
            states[capture]=dict(optimizer=state_hash(opt.state_dict()),
                rng=state_hash(dict(cpu=torch.get_rng_state(),cuda=torch.cuda.get_rng_state_all())))
            del graph,opt,model
            torch.cuda.empty_cache()
        differences=[(outcomes[False][k]-outcomes[True][k]).abs().max().item() for k in outcomes[False]]
        maximum=max(differences)
        if maximum>2e-5:raise AssertionError(f'Capture/eager parameter divergence {maximum}')
        if states[False]!=states[True]:raise AssertionError('Capture/eager optimizer or RNG diverged')
        parity=max(parity or 0.,maximum)
    eager=statistics.median(t['origins_per_second'] for t in trials if not t['capture'])
    captured=statistics.median(t['origins_per_second'] for t in trials if t['capture'])
    result=dict(passed=True,batch_size=1024,precision='bf16',backend='cudnn',trials=trials,
        examples_sha256=file_hash(a.examples),maximum_parameter_difference=parity,
        optimizer_and_rng_exact=True,
        eager_origins_per_second=eager,captured_origins_per_second=captured,speedup=captured/eager,
        scope='same-input student compute/optimizer only; live full pipeline must be checked separately')
    a.output.write_text(json.dumps(result,indent=2));print(json.dumps(result),flush=True)


if __name__=='__main__':main()
