"""Frozen 256D activity-change probes, trained on training windows only."""
import os
os.environ.update(PYTHONDONTWRITEBYTECODE='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1')
import argparse,json
from pathlib import Path
import torch
import wandb
from .objectives import ACTIVITY
from .metrics import NAMES
from ..phase1_v16.model import Autoencoder
from ..phase1_v16.probe import fit_probe
from ..phase1_v12.model import Config,positions
from ..phase1_v12.data import load_teacher
from ..phase1_v13.experiment import cache
from ..phase1_v11.data import verify
from ..v5.shards.phase1_contract import file_hash,write_json
from ..phase2_v2.train import lease
from research.mlops.env import discover_env_files,load_env_files


@torch.no_grad()
def main():
    p=argparse.ArgumentParser();p.add_argument('--checkpoints',nargs='+',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--revision',required=True);a=p.parse_args()
    if a.output.exists():raise ValueError('New identity required')
    a.output.mkdir(parents=True);torch.set_num_threads(4);torch.set_float32_matmul_precision('highest')
    root=Path('D:/TradingML/runtimes/bar_gpt');dataset=root/'phase1-v8-learned-mae-20261117/dataset'
    cal=json.loads((root/'phase1-v12-frozen16-mae-20261121/teacher/calibration.json').read_text())
    if verify(dataset)['hash']!=cal['data_hash']:raise ValueError('Data changed')
    with lease(a.output):
        teacher,_=load_teacher(Path(cal['teacher_path']),cal['split_hash'],'cuda')
        data=[cache(dataset/(split+'.npy'),teacher) for split in ('train','development')]
        models=[]
        for path in a.checkpoints:
            receipt=json.loads((path.parent/'result.json').read_text())
            if file_hash(path)!=receipt['checkpoint_sha']:raise ValueError('Checkpoint changed')
            saved=torch.load(path,map_location='cpu',weights_only=False)
            m=Autoencoder(Config(**saved['contract']['config']),cal['mean'],cal['scale'],'scaled_slots_price').cuda()
            m.load_state_dict(saved['model']);models.append((str(path),m.eval()))
        torch.manual_seed(17)
        models.append(('untrained',Autoencoder(Config(**saved['contract']['config']),cal['mean'],cal['scale'],'scaled_slots_price').cuda().eval()))
        models.append(('raw_summary',None))
        results={}
        for name,model in models:
            features=[]
            for z,meta,valid,x,v,frames,raw in data:
                if model is None:
                    # Independent baseline only; never fed into a learned encoder.
                    x=x[...,:18].double();mask=v[...,:18];count=mask.sum(1).clamp_min(1)
                    mean=torch.where(mask,x,0).sum(1)/count
                    std=(torch.where(mask,(x-mean[:,None]).square(),0).sum(1)/count).sqrt()
                    low=torch.where(mask,x,float('inf')).amin(1);high=torch.where(mask,x,float('-inf')).amax(1)
                    features.append(torch.cat((mean,std,torch.where(mask.any(1),low,0),torch.where(mask.any(1),high,0),
                                               torch.where(mask[:,0],x[:,0],0),torch.where(mask[:,-1],x[:,-1],0),mask.sum(1)/64),-1))
                else:
                    features.append(torch.cat([model.encoder(z[i:i+512],meta[i:i+512],positions(len(z[i:i+512]),'cuda',64)) for i in range(0,len(z),512)]))
            results[name]={}
            for j,channel in zip(ACTIVITY,NAMES):
                masks=[d[4][...,j].all(1) & (d[5]==1000000) for d in data]
                counts=[dict(total=int((d[5]==1000000).sum()),included=int(mask.sum())) for d,mask in zip(data,masks)]
                target=[d[6][mask,:,j].double().log1p().diff(dim=1)*10000 for d,mask in zip(data,masks)]
                if min(int(mask.sum()) for mask in masks)<300:
                    results[name][channel]=dict(counts=counts,status='insufficient complete windows');continue
                result=fit_probe(features[0][masks[0]],target[0],features[1][masks[1]],target[1])
                results[name][channel]=dict(counts=counts,probe=result)
            print(name,{k:v.get('probe',{}).get('development',{}).get('moving') for k,v in results[name].items()},flush=True)
            write_json(a.output/'result.json',dict(scope='1s same-window activity log-change probes; not future forecasts; complete source-valid channel windows only; error units log-basis-points of 1+activity',ridge=.01,results=results))
        load_env_files(discover_env_files(Path(__file__).resolve().parents[3]),verbose=False)
        w=wandb.init(entity='mehdifaraji',project='bargpt-phase1-v17-activity',name=a.output.name,dir=str(a.output),config=dict(source=a.revision,data_hash=cal['data_hash'],checkpoints={str(p):file_hash(p) for p in a.checkpoints},ridge=.01))
        payload={}
        for name,channels in results.items():
            label=Path(name).parent.name if name not in ('untrained','raw_summary') else name
            for channel,result in channels.items():
                for split,scores in result.get('probe',{}).items():
                    payload.update({f'{label}/{channel}/{split}/{k}':v for k,v in scores['moving'].items()})
        w.log(payload,step=0);write_json(a.output/'wandb.json',dict(url=w.url));w.finish()


if __name__=='__main__':main()
