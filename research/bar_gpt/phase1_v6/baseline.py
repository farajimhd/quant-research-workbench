"""Evaluate the pinned historical raw model on the same development subset, on CPU."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,time
from pathlib import Path
import numpy as np
import torch
from ..phase1_v5_raw.model import Config,Autoencoder
from ..phase1_v5_raw.data import prepare
from ..v5.shards.phase1_contract import file_hash,write_json
from .train import load_data
from .metrics import evaluate


def main():
    p=argparse.ArgumentParser();p.add_argument('--checkpoint',type=Path,required=True)
    p.add_argument('--data',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    if a.output.exists():raise FileExistsError(a.output)
    started=time.time();torch.set_num_threads(4)
    data,manifest=load_data(a.data);rows=data['development']
    state=torch.load(a.checkpoint,map_location='cpu',weights_only=False)
    model=Autoencoder(Config()).eval();model.load_state_dict(state['model'],strict=True)
    predictions=[]
    with torch.no_grad():
        for start in range(0,len(rows),128):
            part=rows[start:start+128]
            batch=prepare(torch.from_numpy(part['values'].copy()),torch.from_numpy(part['valid'].astype(bool)),
                torch.from_numpy(part['end_us'].copy()),torch.from_numpy(part['timeframe_us'].copy()),None,
                torch.Generator().manual_seed(731),model.config,full=True)
            _,out=model.from_batch(batch);predictions.append(out.numpy())
    write_json(a.output,dict(checkpoint_sha256=file_hash(a.checkpoint),dataset_hash=manifest['hash'],
        epoch=state['epoch'],step=state['step'],precision='CPU FP32 (historical training used BF16 encoder)',
        elapsed_seconds=time.time()-started,development_full=evaluate(np.concatenate(predictions),rows)))


if __name__=='__main__':main()
