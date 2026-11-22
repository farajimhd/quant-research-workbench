"""Compare kernels on identical checkpoint, data, masks, losses and gradients."""
import os
os.environ.update(PYTHONDONTWRITEBYTECODE='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1')
import argparse,gc,json,time
from pathlib import Path
import numpy as np
import torch
from .model import restore
from .full_step import backward,Replay
from .attention_kernels import install
from ..phase1_v7.full_data import prepare_raw
from ..phase1_v12.model import positions
from ..phase1_v12.data import load_teacher
from ..phase1_v24.runtime import preflight
from ..phase2_v2.train import lease
from ..v5.shards.phase1_contract import file_hash,write_json


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--source-revision',required=True)
    p.add_argument('--checkpoint',type=Path,required=True);p.add_argument('--modes',default='baseline,tiny,pad,tiny_pad');a=p.parse_args();a.device='cuda';a.resume=False;preflight(a)
    torch.set_num_threads(4);torch.set_float32_matmul_precision('highest');torch.cuda.set_per_process_memory_fraction(.85);torch.manual_seed(123)
    root=a.output.parent
    with lease(a.output):
        saved=torch.load(a.checkpoint,map_location='cpu',weights_only=False);model=restore(saved).cuda();model.configure('independent')
        teacher,_=load_teacher(root/'phase1-v12-frozen16-mae-20261121/teacher/teacher.pt',saved['contract']['split_hash'],'cuda')
        rows=np.load(root/'phase1-v8-learned-mae-20261117/dataset/train.npy',mmap_mode='r');batch=prepare_raw(rows[:1024],True).to('cuda')
        masks=[(positions(1024,'cuda',n),positions(1024,'cuda',56)) for n in (16,64)]
        reference=[];result=dict(source=a.source_revision,checkpoint_sha=file_hash(a.checkpoint),cases=[])
        modes=a.modes.split(',')
        if modes[0]!='baseline':raise ValueError('Baseline must be first')
        for mode in modes:
            install(model,teacher,mode);case=dict(mode=mode,measurements=[],accepted=True)
            for i,(pp,ap) in enumerate(masks):
                model.zero_grad(set_to_none=True);values=backward(model,teacher,batch,pp,ap)
                grads=[None if p.grad is None else p.grad.clone() for p in model.parameters()]
                row=dict(visible=pp.shape[1],loss=float(values[0]))
                if mode=='baseline':reference.append(([v.clone() for v in values],grads))
                else:
                    base,bg=reference[i];errors=[];num=den=0.
                    try:
                        torch.testing.assert_close(values[0],base[0],rtol=2e-4,atol=2e-4)
                        for g,b in zip(grads,bg):
                            if b is not None:torch.testing.assert_close(g,b,rtol=3e-3,atol=2e-5)
                    except AssertionError as e:errors.append(str(e)[:1000]);case['accepted']=False
                    for g,b in zip(grads,bg):
                        if b is not None:num+=float((g.double()-b.double()).square().sum());den+=float(b.double().square().sum())
                    row.update(gradient_relative=(num/max(den,1e-30))**.5,errors=errors)
                del grads;model.zero_grad(set_to_none=True)
                replay=Replay(model,teacher,batch,pp,ap)
                for _ in range(5):replay(batch,pp,ap)
                torch.cuda.synchronize();start=time.perf_counter()
                for _ in range(48):replay(batch,pp,ap)
                torch.cuda.synchronize();row['seconds']=(time.perf_counter()-start)/48
                case['measurements'].append(row);del replay;model.zero_grad(set_to_none=True);gc.collect();torch.cuda.empty_cache()
            case['weighted_speed']=1024/(.75*case['measurements'][0]['seconds']+.25*case['measurements'][1]['seconds'])
            result['cases'].append(case);write_json(a.output/'result.json',result);print(json.dumps(case),flush=True)


if __name__=='__main__':main()
