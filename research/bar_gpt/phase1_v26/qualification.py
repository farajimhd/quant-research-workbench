"""Exact assembly checks and inexpensive CPU fixture from real certified rows."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json
from pathlib import Path
import numpy as np
import torch
from .data import parents
from .model import Hybrid,parity
from .step import backward
from ..phase1_v7.full_data import prepare_raw,gpu_inputs
from ..phase1_v12.model import positions
from ..phase1_v11.data import verify
from ..v5.shards.phase1_contract import write_json,file_hash,digest


def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    if not a.output.parent.is_dir():raise FileNotFoundError('Required runtime parent')
    torch.set_num_threads(4);packs,identity=parents(a.root);data=a.root/'phase1-v8-learned-mae-20261117/dataset'
    # Read the same workstation authority through UNC on the laptop; do not
    # resolve the manifest's workstation D: paths against the laptop D: drive.
    manifest=json.loads((data/'manifest.json').read_text())
    if digest({k:v for k,v in manifest.items() if k!='hash'})!=manifest['hash']:raise ValueError('Dataset manifest hash')
    if file_hash(a.root/'phase1-v6-cohort-balanced-v4/split.json')!=manifest['source_split_file_sha256']:raise ValueError('Split changed')
    if file_hash(data/'train.npy')!=manifest['files']['train.npy']:raise ValueError('Training file changed')
    authority=json.loads((a.root/'phase1-v6-cohort-balanced-v4/split.json').read_text())
    if authority['manifest_hash']!=manifest['source_split_hash']:raise ValueError('Split identity')
    if file_hash(data/'train-identities.json')!=manifest['files']['train-identities.json']:raise ValueError('Training identities changed')
    if not all(r['end_us']<authority['cutoff_us'] for r in json.loads((data/'train-identities.json').read_text())):raise ValueError('Training cutoff')
    rows=np.load(data/'train.npy',mmap_mode='r');b=prepare_raw(rows[:8],True);x,v,_=gpu_inputs(b);records=[]
    gradients=[]
    for fusion in (False,True):
        m=Hybrid(packs,fusion)
        for n in (16,56,64):
            pos=positions(len(x),'cpu',n,torch.Generator().manual_seed(71));records.append(dict(fusion=fusion,visible=n,**parity(m,packs,x,v,pos)))
        m.configure('activity');assert all(not p.requires_grad for p in m.price.parameters());assert not m.heads['price'].weight.requires_grad
        assert all(p.requires_grad for p in m.activity.parameters())
        with torch.no_grad():teacher=m.price.features(x,v)[0]
        for mode in (('fusion','gradual') if fusion else ('activity','independent')):
            m.configure(mode,mode=='gradual');m.zero_grad(set_to_none=True)
            pos=positions(len(x),'cpu',64);value,parts,norm=backward(m,(x,v,torch.from_numpy(b.grid) if isinstance(b.grid,np.ndarray) else b.grid,None,teacher,pos,pos))
            if not bool(torch.isfinite(value)&torch.isfinite(norm)):raise ValueError('Nonfinite CPU gradients')
            gradients.append(dict(mode=mode,loss=float(value),norm=float(norm),groups=[name for name,_ in m.groups()]))
    a.output.mkdir(exist_ok=True);write_json(a.output/'cpu-parity.json',dict(parents=identity,data_hash=manifest['hash'],records=records,gradients=gradients));print(json.dumps(records+gradients))
if __name__=='__main__':main()
