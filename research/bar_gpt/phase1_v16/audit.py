"""Evaluate retained finalists on the frozen audit subset, without updates."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json,time
from pathlib import Path
import torch,wandb
from .model import Autoencoder
from ..phase1_v12.model import Config
from ..phase1_v12.data import load_teacher
from ..phase1_v15.experiment import cache,audit
from ..phase1_v11.data import verify
from ..v5.shards.phase1_contract import file_hash,write_json
from ..phase2_v2.train import lease
from research.mlops.env import discover_env_files,load_env_files


def main():
    p=argparse.ArgumentParser();p.add_argument('--checkpoints',type=Path,nargs='+',required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--revision',required=True);a=p.parse_args()
    if a.output.exists():raise ValueError('New audit identity required')
    a.output.mkdir(parents=True);torch.set_num_threads(4);torch.set_float32_matmul_precision('highest')
    root=Path('D:/TradingML/runtimes/bar_gpt');dataset=root/'phase1-v8-learned-mae-20261117/dataset'
    cal=json.loads((root/'phase1-v12-frozen16-mae-20261121/teacher/calibration.json').read_text())
    if verify(dataset)['hash']!=cal['data_hash']:raise ValueError('Frozen dataset mismatch')
    load_env_files(discover_env_files(Path(__file__).resolve().parents[3]),verbose=False)
    with lease(a.output):
        teacher,_=load_teacher(Path(cal['teacher_path']),cal['split_hash'],'cuda');data=cache(dataset/'audit.npy',teacher)
        results=[]
        for index,path in enumerate(a.checkpoints):
            receipt=json.loads((path.parent/'result.json').read_text())
            if file_hash(path)!=receipt['checkpoint_sha']:raise ValueError('Changed checkpoint')
            saved=torch.load(path,map_location='cpu',weights_only=False);arm=saved['contract'].get('arm','base')
            m=Autoencoder(Config(**saved['contract']['config']),cal['mean'],cal['scale'],arm).cuda();m.load_state_dict(saved['model'])
            out=a.output/f'{index}-{arm}';out.mkdir()
            contract=dict(source=a.revision,checkpoint=str(path),checkpoint_sha=file_hash(path),arm=arm,data_hash=cal['data_hash'],split_hash=cal['split_hash'],scope='Frozen V8 audit subset; unseen by V16 training, not a pristine external test',windows=len(data[0]))
            write_json(out/'manifest.json',contract)
            w=wandb.init(entity='mehdifaraji',project='bargpt-phase1-v16-direction',name=a.output.name+f'-{index}-{arm}',dir=str(out),config=contract,reinit=True)
            try:
                began=time.perf_counter();detail,metrics=audit(m,teacher,data)
                write_json(out/'details.json',detail);write_json(out/'metrics.json',metrics)
                w.log({'audit/'+k:v for k,v in metrics.items()},step=0)
                result=dict(contract,seconds=time.perf_counter()-began,wandb_url=w.url);results.append(result);write_json(a.output/'results.json',results)
                w.summary['status']='complete'
            except BaseException:
                w.finish(exit_code=1);raise
            else:w.finish()
            del m;torch.cuda.empty_cache()


if __name__=='__main__':main()
