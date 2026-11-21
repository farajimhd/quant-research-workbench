"""Finalist training-gap and retained audit checks, without optimizer updates."""
import os
os.environ.update(PYTHONDONTWRITEBYTECODE='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1')
import argparse,json,time
from pathlib import Path
import torch
import wandb
from .experiment import evaluate
from ..phase1_v16.model import Autoencoder
from ..phase1_v12.model import Config
from ..phase1_v12.data import load_teacher
from ..phase1_v13.experiment import cache
from ..phase1_v11.data import verify
from ..v5.shards.phase1_contract import file_hash,write_json
from ..phase2_v2.train import lease
from research.mlops.env import discover_env_files,load_env_files


def main():
    p=argparse.ArgumentParser();p.add_argument('--checkpoint',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--revision',required=True);a=p.parse_args()
    if a.output.exists():raise ValueError('Immutable audit identity required')
    receipt=json.loads((a.checkpoint.parent/'result.json').read_text())
    if file_hash(a.checkpoint)!=receipt['checkpoint_sha']:raise ValueError('Checkpoint changed')
    saved=torch.load(a.checkpoint,map_location='cpu',weights_only=False)
    root=Path('D:/TradingML/runtimes/bar_gpt');dataset=root/'phase1-v8-learned-mae-20261117/dataset'
    cal=json.loads((root/'phase1-v12-frozen16-mae-20261121/teacher/calibration.json').read_text())
    if verify(dataset)['hash']!=cal['data_hash']:raise ValueError('Data changed')
    a.output.mkdir(parents=True);torch.set_num_threads(4);torch.set_float32_matmul_precision('highest')
    load_env_files(discover_env_files(Path(__file__).resolve().parents[3]),verbose=False)
    with lease(a.output):
        teacher,_=load_teacher(Path(cal['teacher_path']),cal['split_hash'],'cuda')
        model=Autoencoder(Config(**saved['contract']['config']),cal['mean'],cal['scale'],'scaled_slots_price').cuda();model.load_state_dict(saved['model'])
        w=wandb.init(entity='mehdifaraji',project='bargpt-phase1-v17-activity',name=a.output.name,dir=str(a.output),config=dict(source=a.revision,checkpoint=str(a.checkpoint),checkpoint_sha=receipt['checkpoint_sha'],data_hash=cal['data_hash'],split_hash=cal['split_hash']))
        results={};started=time.perf_counter()
        try:
            for i,split in enumerate(('train','audit')):
                data=cache(dataset/(split+'.npy'),teacher);detailed,metrics=evaluate(model,teacher,data)
                write_json(a.output/(split+'-detailed.json'),detailed);results[split]=metrics
                w.log({split+'/'+k:v for k,v in metrics.items()},step=i);del data
            write_json(a.output/'result.json',dict(metrics=results,seconds=time.perf_counter()-started,wandb_url=w.url,checkpoint_sha=receipt['checkpoint_sha']))
            w.summary['status']='complete';w.finish()
        except BaseException:
            w.finish(exit_code=1);raise


if __name__=='__main__':main()
