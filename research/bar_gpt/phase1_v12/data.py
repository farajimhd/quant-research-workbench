import json
from pathlib import Path
import torch
from ..phase0_v2.model import Autoencoder as BarEncoder,Config as BarConfig
from ..phase1_v7.full_data import gpu_inputs
from ..phase2_v2.train import load_checkpoint
from ..v5.shards.phase1_contract import file_hash


def load_teacher(path,split_hash,device='cpu',required_epoch=19):
    path=Path(path)
    proof=json.loads(path.with_suffix('.proof.json').read_text())
    if file_hash(path)!=proof['sha256']:raise ValueError('Teacher hash mismatch')
    peek=torch.load(path,map_location='cpu',weights_only=False)
    state=load_checkpoint(path,peek['contract']);c=state['contract']
    if c['version']!='phase0-v2' or c['split_hash']!=split_hash:raise ValueError('Teacher version/split differs')
    done=state['epoch_done'];population=c['counts']['train']
    checkpoint_epoch=state['epoch']+int(done>0)
    if checkpoint_epoch!=required_epoch or state['windows']!=state['epoch']*population+done:raise ValueError('Requested teacher epoch/traversal mismatch')
    if not 0<=done<=population or state['phase'] not in ('train','validation'):raise ValueError('Invalid teacher traversal')
    if c['config']['embedding']!=16:raise ValueError('16D teacher required')
    teacher=BarEncoder(dict(mean=state['model']['mean'],scale=state['model']['scale']),BarConfig(**c['config']))
    teacher.load_state_dict(state['model']);teacher.requires_grad_(False);teacher.eval();teacher.to(device)
    return teacher,dict(checkpoint_sha256=proof['sha256'],contract=c,checkpoint_epoch=checkpoint_epoch,
                        completed_epochs=state['epoch']+int(done==population),within_epoch_windows=done,step=state['step'])


@torch.no_grad()
def prepare(teacher,b):
    values,valid,_=gpu_inputs(b)
    latent=teacher.encode(values,valid)
    timing=torch.where(valid[...,18:],(values[...,18:]-teacher.mean[18:])/teacher.scale[18:],0)
    metadata=torch.cat((timing,valid.float()),-1)
    return latent,metadata,valid[...,:18].any(-1),values,valid


def resume_contract(current,path):
    """Keep original scientific identity; record current engineering SHA separately."""
    path=Path(path);proof=json.loads(path.with_suffix('.proof.json').read_text())
    if file_hash(path)!=proof['sha256']:raise ValueError('Resume checkpoint hash changed')
    prior=torch.load(path,map_location='cpu',weights_only=False)['contract']
    candidate=dict(current,source=prior['source'])
    if candidate!=prior:raise ValueError('Changed scientific contract cannot resume this experiment')
    load_checkpoint(path,prior)
    return candidate
