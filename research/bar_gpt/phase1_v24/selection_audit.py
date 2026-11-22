"""One post-selection check; preserve the audit partition's exposure caveat."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,gc,json,time
from pathlib import Path
import numpy as np
import torch
from .model import restore
from .runtime import preflight
from ..phase1_v23.metrics import evaluate
from ..phase0_v3.experiment import cache
from ..phase1_v11.data import verify
from ..phase1_v12.data import load_teacher
from ..phase1_v22.compare import summarize
from ..v5.shards.phase1_contract import file_hash,write_json


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--revision',required=True)
    p.add_argument('--arm',type=Path,action='append',required=True);a=p.parse_args()
    a.device='cuda';a.source_revision=a.revision;a.resume=False;preflight(a)
    root=Path('D:/TradingML/runtimes/bar_gpt');dataset=root/'phase1-v8-learned-mae-20261117/dataset'
    manifest=verify(dataset,parts=('train','development','audit'));torch.set_num_threads(4);torch.set_float32_matmul_precision('highest');started=time.time()
    teacher,identity=load_teacher(root/'phase1-v12-frozen16-mae-20261121/teacher/teacher.pt',manifest['source_split_hash'],'cuda')
    data=cache(np.load(dataset/'audit.npy',mmap_mode='r'))
    with torch.no_grad():z=torch.cat([teacher.encode(data[0][i:i+512],data[1][i:i+512]) for i in range(0,len(data[0]),512)])
    data=(*data,z);results=[]
    for arm in a.arm:
        result=json.loads((arm/'result.json').read_text());cp=arm/'checkpoint_last.pt'
        if file_hash(cp)!=result['checkpoint_sha']:raise ValueError('Checkpoint changed')
        saved=torch.load(cp,map_location='cpu',weights_only=False)
        if saved['contract']['data_hash']!=manifest['hash'] or saved['contract']['teacher']!=identity:raise ValueError('Data/teacher changed')
        model=restore(saved).cuda();del saved
        detail,metrics=evaluate(model,teacher,data)
        row=dict(name=result['name'],checkpoint_sha=result['checkpoint_sha'],training_exposures=result['seen'],summary=summarize(result['name'],metrics),metrics=metrics)
        results.append(row);write_json(a.output/(result['name']+'.json'),detail)
        write_json(a.output/'results.json',dict(source=a.revision,data_hash=manifest['hash'],partition_file_sha=file_hash(dataset/'audit.npy'),windows=len(data[0]),policy=manifest['policy'],pristine_external_test=False,post_selection_only=True,no_training_or_calibration=True,seconds=time.time()-started,results=results))
        print(json.dumps(row['summary']),flush=True);del model;gc.collect();torch.cuda.empty_cache()


if __name__=='__main__':main()
