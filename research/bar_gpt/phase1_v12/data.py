import json
from pathlib import Path
import torch
from ..phase0_v2.model import Autoencoder as BarEncoder,Config as BarConfig
from ..phase1_v7.full_data import gpu_inputs
from ..phase2_v2.train import load_checkpoint
from ..v5.shards.phase1_contract import file_hash


def load_teacher(path,split_hash,device='cpu'):
    path=Path(path)
    proof=json.loads(path.with_suffix('.proof.json').read_text())
    if file_hash(path)!=proof['sha256']:raise ValueError('Teacher hash mismatch')
    peek=torch.load(path,map_location='cpu',weights_only=False)
    state=load_checkpoint(path,peek['contract']);c=state['contract']
    if c['version']!='phase0-v2' or c['split_hash']!=split_hash:raise ValueError('Teacher version/split differs')
    if state['epoch']!=20 or state['phase']!='train' or state['windows']!=20*c['counts']['train']:raise ValueError('Completed epoch20 teacher required')
    if c['config']['embedding']!=16:raise ValueError('16D teacher required')
    teacher=BarEncoder(dict(mean=state['model']['mean'],scale=state['model']['scale']),BarConfig(**c['config']))
    teacher.load_state_dict(state['model']);teacher.requires_grad_(False);teacher.eval();teacher.to(device)
    return teacher,dict(checkpoint_sha256=proof['sha256'],contract=c,epoch=20,step=state['step'])


@torch.no_grad()
def prepare(teacher,b):
    values,valid,_=gpu_inputs(b)
    latent=teacher.encode(values,valid)
    timing=torch.where(valid[...,18:],(values[...,18:]-teacher.mean[18:])/teacher.scale[18:],0)
    metadata=torch.cat((timing,valid.float()),-1)
    return latent,metadata,valid[...,:18].any(-1),values,valid
