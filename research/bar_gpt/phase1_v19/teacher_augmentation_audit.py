"""Paired CPU audit of the frozen teacher on real and synthetic train inputs."""
import os
os.environ.update(PYTHONDONTWRITEBYTECODE='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1')
import argparse,json,time
from pathlib import Path
import numpy as np
import torch
from ..phase1_v12.data import load_teacher,prepare
from ..phase1_v12.metrics import RawMetrics,raw_scalars
from ..phase1_v17.metrics import ActivityMetrics,scalars
from ..phase1_v7.full_data import prepare_raw
from ..v5.shards.phase1_contract import file_hash,write_json,FRAMES


@torch.no_grad()
def main():
    p=argparse.ArgumentParser();p.add_argument('--augmentation',type=Path,required=True);a=p.parse_args()
    root=Path('D:/TradingML/runtimes/bar_gpt');torch.set_num_threads(4);start=time.perf_counter()
    calpath=root/'phase1-v12-frozen16-mae-20261121/teacher/calibration.json';cal=json.loads(calpath.read_text())
    teacher,identity=load_teacher(Path(cal['teacher_path']),cal['split_hash'],'cpu')
    manifest=json.loads((a.augmentation/'manifest.json').read_text())
    if manifest['split_hash']!=cal['split_hash'] or file_hash(a.augmentation/'train.npy')!=manifest['files']['train.npy']:raise ValueError('Augmentation identity')
    original=np.load(root/'phase1-v8-learned-mae-20261117/dataset/train.npy',mmap_mode='r')
    synthetic=np.load(a.augmentation/'train.npy',mmap_mode='r');rng=np.random.default_rng(192832)
    ids=np.concatenate([rng.choice(np.flatnonzero(original['timeframe_us']==f),128,replace=False) for f in FRAMES])
    results={}
    for name,rows in (('original',original[ids]),('synthetic',synthetic[manifest['real_windows']+ids])):
        raw=RawMetrics('cpu');activity=ActivityMetrics('cpu')
        for i in range(0,len(rows),32):
            b=prepare_raw(rows[i:i+32],True);z,meta,valid,x,v=prepare(teacher,b);out=teacher.decode(z)
            raw.add(teacher,out,x,v,b);activity.add(out['logs'],b.grid,v,b.frames)
        results[name]={**raw_scalars(raw.result()),**{'activity/'+k:v for k,v in scalars(activity.result()).items()}}
    result=dict(teacher_sha=identity['checkpoint_sha256'],augmentation_hash=manifest['hash'],precision='CPU FP32',
                training_indices=ids.tolist(),windows_per_arm=len(ids),seconds=time.perf_counter()-start,metrics=results,
                scope='Paired training-only teacher audit, not validation or Phase1 performance')
    write_json(a.augmentation/'teacher-audit.json',result)
    for name,m in results.items():print(name,{k:v for k,v in m.items() if k in ('candle/1s/trade/moving_direction_pct','mape_pct/1s/trade_close','activity/1s/bid_size/moving_direction_pct','nonfinite')},flush=True)


if __name__=='__main__':main()
