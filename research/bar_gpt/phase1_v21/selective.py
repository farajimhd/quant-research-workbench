"""Preserve parent trade-volume rows inside the SAME calibrated linear head."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,copy,json,time
from pathlib import Path
import numpy as np
import torch
import wandb
from ..phase1_v20.encoders import restore
from ..phase1_v20.experiment import evaluate
from ..phase0_v3.experiment import cache
from ..phase1_v12.data import load_teacher
from ..phase1_v11.data import verify
from ..phase2_v2.train import lease
from ..v5.shards.phase1_contract import file_hash,write_json
from research.mlops.env import discover_env_files,load_env_files


def combine(parent,candidate):
    result=copy.deepcopy(candidate)
    for name,value in parent['model'].items():
        if not name.startswith('decoder.'):
            torch.testing.assert_close(value,candidate['model'][name],rtol=0,atol=0)
    rows=torch.arange(1152)%18==4
    for name in ('decoder.weight','decoder.bias'):
        result['model'][name][rows]=parent['model'][name][rows]
    result.pop('optimizer',None)
    return result


def main():
    p=argparse.ArgumentParser();p.add_argument('--parent',type=Path,required=True);p.add_argument('--candidate',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--revision',required=True);a=p.parse_args()
    if a.output.exists():raise ValueError('New scientific identity required')
    a.output.mkdir();torch.set_num_threads(4);torch.set_float32_matmul_precision('highest');started=time.perf_counter()
    def checked(path):
        sha=file_hash(path)
        if sha!=json.loads((path.parent/'result.json').read_text())['checkpoint_sha']:raise ValueError('Checkpoint changed')
        return torch.load(path,map_location='cpu',weights_only=False),sha
    parent,parent_sha=checked(a.parent);candidate,candidate_sha=checked(a.candidate);saved=combine(parent,candidate)
    saved['step']=0;saved['contract'].update(source=a.revision,arm='selective_volume',steps=0,training_scope='deterministic row selection; no optimizer updates',parent_sha=parent_sha,calibrated_sha=candidate_sha,intervention='Same one head: parent trade-volume rows retained; all other calibrated rows retained. No optimizer resume.')
    write_json(a.output/'manifest.json',saved['contract'])
    root=Path('D:/TradingML/runtimes/bar_gpt');data=root/'phase1-v8-learned-mae-20261117/dataset';verified=verify(data)
    if verified['source_split_hash']!=saved['contract']['split_hash']:raise ValueError('Split')
    load_env_files(discover_env_files(Path(__file__).resolve().parents[3]),verbose=False)
    with lease(a.output):
        teacher,_=load_teacher(root/'phase1-v12-frozen16-mae-20261121/teacher/teacher.pt',verified['source_split_hash'],'cuda')
        dev=cache(np.load(data/'development.npy',mmap_mode='r'))
        with torch.no_grad():old=torch.cat([teacher.encode(dev[0][i:i+512],dev[1][i:i+512]) for i in range(0,len(dev[0]),512)])
        model=restore(saved).cuda();detail,metrics=evaluate(model,teacher,(*dev,old))
        torch.save(saved,a.output/'checkpoint.pt');sha=file_hash(a.output/'checkpoint.pt')
        w=wandb.init(entity='mehdifaraji',project='bargpt-phase01-v21-repair',name=a.output.name,dir=str(a.output),config=saved['contract']);w.log({'development/'+k:v for k,v in metrics.items()},step=0)
        write_json(a.output/'evaluation-0.json',detail);write_json(a.output/'latest-metrics.json',metrics)
        write_json(a.output/'result.json',dict(checkpoint_sha=sha,curve=[dict(step=0,metrics=metrics)],seconds=time.perf_counter()-started,wandb_url=w.url,decision='Development candidate; subsequent joint/full qualification required'))
        w.summary['status']='complete';w.finish()


if __name__=='__main__':main()
