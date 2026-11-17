"""Read-only pinned reconstruction; explicit audit mode is post-selection replication."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse
from pathlib import Path
import torch
import numpy as np
from .model import Autoencoder,Config
from .train import load_data,cache,predict
from .metrics import evaluate
from ..v5.shards.phase1_contract import file_hash,write_json


def main():
    p=argparse.ArgumentParser();p.add_argument('--checkpoint',type=Path,required=True)
    p.add_argument('--data',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--partition',choices=('development','audit'),default='development')
    p.add_argument('--device',choices=('cpu','cuda'),default='cpu');a=p.parse_args()
    if a.output.exists():raise FileExistsError(a.output)
    if a.output.resolve().is_relative_to(Path(__file__).resolve().parents[3]):raise ValueError('Reports belong under runtime roots')
    torch.set_num_threads(4)
    state=torch.load(a.checkpoint,map_location='cpu',weights_only=False);identity=state['identity']
    rows,manifest=load_data(a.data);assert identity['dataset_hash']==manifest['hash']
    if a.partition=='audit':
        path=a.data/'audit.npy';assert file_hash(path)==manifest['files']['audit.npy']
        rows['audit']=np.load(path,allow_pickle=False)
    mean=torch.tensor(identity['calibration']['mean']);scale=torch.tensor(identity['calibration']['scale'])
    data=cache(rows[a.partition],mean,scale,a.device)
    model=Autoencoder(Config(**identity['config']),mean,scale).to(a.device);model.load_state_dict(state['model'])
    result={}
    for full in (True,False):
        pred,_,mask=predict(model,data,512,full)
        result['full' if full else 'masked']=evaluate(pred,rows[a.partition],mask)
    write_json(a.output,dict(checkpoint_sha256=file_hash(a.checkpoint),dataset_hash=manifest['hash'],device=a.device,partition=a.partition,
        mask_seed=731,mask_note='CPU and CUDA generators use different masked draws; all-visible population is identical.',results=result))


if __name__=='__main__':main()
