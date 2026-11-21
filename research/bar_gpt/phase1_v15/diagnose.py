import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json,time
from pathlib import Path
import torch
from .model import Autoencoder
from ..phase1_v12.model import Config,positions
from ..phase1_v12.data import load_teacher
from ..phase1_v13.experiment import cache,evaluate
from ..v5.shards.phase1_contract import write_json,file_hash


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    a.output.mkdir(parents=True,exist_ok=False);torch.set_num_threads(4);torch.set_float32_matmul_precision('highest')
    root=Path('D:/TradingML/runtimes/bar_gpt');cal=json.loads((root/'phase1-v12-frozen16-mae-20261121/teacher/calibration.json').read_text())
    teacher,_=load_teacher(root/'phase1-v12-frozen16-mae-20261121/teacher/teacher.pt',cal['split_hash'],'cuda')
    dev=cache(root/'phase1-v8-learned-mae-20261117/dataset/development.npy',teacher)
    paths=[root/f'phase1-v14-inputnorm-seed{seed}-20261121/zscore/checkpoint.pt' for seed in (17,29)]
    paths.append(root/'phase1-v13-stability-15k-20261121/fresh-stable/checkpoint.pt')
    results=[]
    for path in paths:
        state=torch.load(path,map_location='cpu',weights_only=False)
        weights={k:v for k,v in state['model'].items() if k not in ('encoder.input_center','encoder.input_scale')}
        for corrected in (False,True):
            stable='fresh-stable' in str(path);variant=('stable_balanced' if corrected else 'stable') if stable else ('balanced' if corrected else 'original')
            m=Autoencoder(Config(),cal['mean'],cal['scale'],variant).cuda();m.load_state_dict(weights)
            measured=evaluate(m,teacher,dev)
            with torch.no_grad():
                torch.manual_seed(917);z,meta=dev[0][:512],dev[1][:512];pos=positions(len(z),'cuda')
                one=m(z,meta,pos);repeated=m(z,meta,pos.repeat_interleave(4,dim=1))
                duplication=dict(mean_abs=float((one-repeated).abs().mean()),max_abs=float((one-repeated).abs().max()))
            record=dict(checkpoint=str(path),sha256=file_hash(path),variant=variant,metrics=measured,duplicate_same_tokens=duplication)
            results.append(record);print(json.dumps(record),flush=True);write_json(a.output/'comparison.json',results)


if __name__=='__main__':main()
