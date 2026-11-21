"""CPU-only outlier attribution on the frozen development subset."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json
from pathlib import Path
import numpy as np
import torch
from ..phase1_v17.objectives import ACTIVITY
from ..phase1_v17.metrics import NAMES
from ..phase1_v16.model import Autoencoder
from ..phase1_v12.model import Config,positions
from ..phase1_v12.data import load_teacher,prepare
from ..phase1_v7.full_data import prepare_raw
from ..v5.shards.phase1_contract import write_json,file_hash


@torch.no_grad()
def main():
    p=argparse.ArgumentParser();p.add_argument('--runtime-root',type=Path,required=True);p.add_argument('--checkpoints',nargs='+',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--visible',type=int,choices=(16,64),default=16);a=p.parse_args()
    if a.output.exists():raise ValueError('Immutable diagnosis output required')
    a.output.mkdir(parents=True);torch.set_num_threads(4)
    cal=json.loads((a.runtime_root/'phase1-v12-frozen16-mae-20261121/teacher/calibration.json').read_text())
    teacher,_=load_teacher(a.runtime_root/'phase1-v12-frozen16-mae-20261121/teacher/teacher.pt',cal['split_hash'],'cpu')
    rows=np.load(a.runtime_root/'phase1-v8-learned-mae-20261117/dataset/development.npy',mmap_mode='r')
    models=[]
    for path in a.checkpoints:
        receipt=json.loads((path.parent/'result.json').read_text())
        if file_hash(path)!=receipt['checkpoint_sha']:raise ValueError('Checkpoint changed')
        saved=torch.load(path,map_location='cpu',weights_only=False)
        m=Autoencoder(Config(**saved['contract']['config']),cal['mean'],cal['scale'],'scaled_slots_price').eval();m.load_state_dict(saved['model'])
        models.append((str(path),m))
    worst={name:[] for name,_ in models};generator=torch.Generator().manual_seed(917)
    # Entire development population, all-visible, no target-selected filtering.
    for start in range(0,len(rows),128):
        batch=prepare_raw(rows[start:start+128],True)
        z,meta,valid,x,v=prepare(teacher,batch)
        pos=positions(len(z),'cpu',a.visible,generator)
        hidden=~torch.zeros_like(valid).scatter(1,pos,True) if a.visible<64 else torch.ones_like(valid)
        for name,model in models:
            pred=model(z,meta,pos);logs=teacher.decode(model.denormalize(pred))['logs'][...,ACTIVITY].double()
            raw=logs.expm1();target=batch.grid[...,ACTIVITY].double()
            err=torch.where(v[...,ACTIVITY]&hidden[...,None],(raw-target).abs(),-1)
            values,indices=err.flatten().topk(5)
            for value,index in zip(values.tolist(),indices.tolist()):
                row=index//(64*6);bar=(index//6)%64;channel=index%6
                worst[name].append(dict(subset_row=start+row,bar=bar,channel=NAMES[channel],frame=int(batch.frames[row]),
                    end_us=int(batch.slots[row,bar]),raw_error=value,target=float(target[row,bar,channel]),prediction=float(raw[row,bar,channel]),
                    predicted_log=float(logs[row,bar,channel]),target_log=float(target[row,bar,channel].log1p()),
                    latent_mse=float((pred[row,bar]-(z[row,bar]-model.encoder.mean)/model.encoder.scale).square().mean())))
            worst[name]=sorted(worst[name],key=lambda x:x['raw_error'],reverse=True)[:10]
    write_json(a.output/'result.json',dict(scope=f'CPU FP32 {a.visible}-visible full development outliers; only hidden outputs when masked; no training use or hidden references',windows=len(rows),checkpoints={str(p):file_hash(p) for p in a.checkpoints},worst=worst))
    for name,values in worst.items():print(name,values[0],flush=True)


if __name__=='__main__':main()
