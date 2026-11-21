"""Frozen linear probes of same-window candle direction, not future forecasts."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json
from pathlib import Path
import torch
from .model import Autoencoder
from ..phase1_v12.model import Config,positions,gather
from ..phase1_v12.data import load_teacher
from ..phase1_v13.experiment import cache
from ..v5.shards.phase1_contract import write_json,file_hash
from ..phase2_v2.train import lease


def summary(pred,target):
    result={}
    moving=target!=0
    for name,mask in [('moving',moving),('at_least_1bp',target.abs()>=1),('at_least_5bp',target.abs()>=5),('at_least_10bp',target.abs()>=10)]:
        result[name]=dict(count=int(mask.sum()),direction_pct=float(((pred.sign()==target.sign())&mask).sum()/mask.sum().clamp_min(1)*100),mae_log_basis_points=float(torch.where(mask,(pred-target).abs(),0).sum()/mask.sum().clamp_min(1)))
    return result


def fit_probe(x,y,dx,dy):
    x=x.double();dx=dx.double();y=y.double();dy=dy.double()
    center=x.mean(0);scale=x.std(0).clamp_min(1e-8);x=(x-center)/scale;dx=(dx-center)/scale
    target_center=y.mean(0);n=len(x)
    weight=torch.linalg.solve(x.T@x/n+.01*torch.eye(x.shape[1],device=x.device,dtype=x.dtype),x.T@(y-target_center)/n)
    return dict(train=summary(x@weight+target_center,y),development=summary(dx@weight+target_center,dy))


@torch.no_grad()
def main():
    p=argparse.ArgumentParser();p.add_argument('--checkpoints',nargs='+',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    if a.output.exists():raise ValueError('New probe identity required')
    a.output.mkdir(parents=True);torch.set_num_threads(4);torch.set_float32_matmul_precision('highest')
    root=Path('D:/TradingML/runtimes/bar_gpt');cal=json.loads((root/'phase1-v12-frozen16-mae-20261121/teacher/calibration.json').read_text())
    with lease(a.output):
        teacher,_=load_teacher(Path(cal['teacher_path']),cal['split_hash'],'cuda')
        train=cache(root/'phase1-v8-learned-mae-20261117/dataset/train.npy',teacher);dev=cache(root/'phase1-v8-learned-mae-20261117/dataset/development.npy',teacher)
        selected=[];targets=[];counts=[]
        for data in (train,dev):
            valid=(data[4][...,0]&data[4][...,3]).all(1)
            counts.append(dict(total=len(valid),included=int(valid.sum()),missing_trade_pair=int((~valid).sum())))
            selected.append(valid);raw=data[6][valid]
            targets.append((10000*torch.log(raw[...,0].double()/raw[...,3].double())).float())
        y,dy=targets;rows={}
        train_1s=train[5][selected[0]]==1000000;dev_1s=dev[5][selected[1]]==1000000
        rows['training_constant']=summary(y.mean(0).expand_as(dy),dy)
        rows['mean_latent_probe']=fit_probe(train[0][selected[0]].mean(1),y,dev[0][selected[1]].mean(1),dy)
        rows['phase0_teacher']=summary(10000*teacher.decode(dev[0][selected[1]])['code'][...,1],dy)
        for path in a.checkpoints:
            receipt=json.loads((path.parent/'result.json').read_text())
            if file_hash(path)!=receipt['checkpoint_sha']:raise ValueError('Checkpoint hash mismatch')
            saved=torch.load(path,map_location='cpu',weights_only=False);arm=saved['contract'].get('arm','base')
            m=Autoencoder(Config(**saved['contract']['config']),cal['mean'],cal['scale'],arm).cuda();m.load_state_dict(saved['model']);m.eval()
            features=[];predictions=[]
            for data,valid in zip((train,dev),selected):
                z,meta=data[0][valid],data[1][valid];parts=[];preds=[]
                for start in range(0,len(z),512):
                    sl=slice(start,start+512);pos=positions(len(z[sl]),'cuda',64)
                    emb=m.encoder(z[sl],meta[sl],pos);parts.append(emb)
                    preds.append(10000*teacher.decode(m.denormalize(m.decode(emb)))['code'][...,1])
                features.append(torch.cat(parts));predictions.append(torch.cat(preds))
            rows[str(path)]=dict(sha256=file_hash(path),probe=fit_probe(features[0],y,features[1],dy),train_decoder=summary(predictions[0],y),decoder=summary(predictions[1],dy))
            rows[str(path)]['probe_1s']=fit_probe(features[0][train_1s],y[train_1s],features[1][dev_1s],dy[dev_1s])
            rows[str(path)]['decoder_1s']=summary(predictions[1][dev_1s],dy[dev_1s])
            write_json(a.output/'result.json',dict(scope='Same-window trade candle reconstruction, complete source-valid trade pairs only; no future labels',counts=counts,ridge=.01,results=rows))
            print(path.parent.name,rows[str(path)]['probe']['development']['moving'],rows[str(path)]['decoder']['moving'],flush=True)
            del m,features,predictions
        write_json(a.output/'result.json',dict(scope='Same-window trade candle reconstruction, complete source-valid trade pairs only; no future labels',counts=counts,ridge=.01,results=rows))


if __name__=='__main__':main()
