import torch
from torch.nn import functional as F
from ..phase1_v16.model import direction_objective
from ..phase1_v17.objectives import ACTIVITY
from ..v5.shards.phase1_contract import FRAMES

ARMS=('joint','joint_calibrated')


@torch.no_grad()
def calibrate(raw,valid,frames):
    indices=torch.tensor(ACTIVITY,device=raw.device)
    mask=valid.index_select(-1,indices)
    target=torch.where(mask,raw.index_select(-1,indices).double(),0).log1p()
    pair=mask[:,1:]&mask[:,:-1];delta=target[:,1:]-target[:,:-1]
    codes=torch.tensor(FRAMES,device=raw.device)
    group=F.one_hot((frames[:,None]==codes).long().argmax(1),6).T.double()
    count=group@pair.double().sum(1)
    square=group@torch.where(pair,delta.square(),0).sum(1)
    if (count==0).any():raise ValueError('Missing training activity calibration group')
    rms2=square/count
    return torch.where(rms2>0,rms2,1).float(),dict(pair_counts=count.cpu().tolist(),mean_square_log_change=rms2.cpu().tolist(),constant_groups=int((rms2==0).sum()))


def penalties(code,raw,valid,known,frames,scale_sq,balanced):
    indices=torch.tensor(ACTIVITY,device=raw.device)
    mask=valid.index_select(-1,indices)
    target=torch.where(mask,raw.index_select(-1,indices).double(),0).log1p().float()
    predicted=code.index_select(-1,indices)
    # Penalize pre-clamp activity coordinates too: a negative prediction must
    # not lose its correction gradient at Phase0's nonnegative output floor.
    err=predicted-target
    weights=mask*torch.where(known,1.,.1)[...,None]
    denominator=weights.sum((0,1)).clamp_min(1)
    level=(torch.where(mask,err.square(),0)*weights).sum((0,1))/denominator
    tail=(torch.where(mask,err.pow(4),0)*weights).sum((0,1))/denominator
    pair=mask[:,1:]&mask[:,:-1]&known[:,1:,None]&known[:,:-1,None]
    q=predicted.clamp_min(0)
    delta=(q[:,1:]-q[:,:-1])-(target[:,1:]-target[:,:-1])
    if balanced:
        f=(frames[:,None]==torch.tensor(FRAMES,device=frames.device)).long().argmax(1)
        delta=delta/scale_sq[f,None,:].sqrt()
    change=torch.where(pair,delta.square(),0).sum((0,1))/pair.sum((0,1)).clamp_min(1)
    return level.mean(),change.mean(),tail.mean()


def objective(model,teacher,pred,latent,valid,pos,raw,source_valid,arm,frames,scale_sq):
    if arm not in ARMS:raise ValueError(arm)
    loss,body=direction_objective(model,teacher,pred,latent,valid,pos,raw,source_valid)
    code=teacher.decoder(model.denormalize(pred).float())*teacher.scale[:18]+teacher.mean[:18]
    known=torch.zeros_like(valid).scatter(1,pos,True)
    level,delta,tail=penalties(code,raw,source_valid,known,frames,scale_sq,arm=='joint_calibrated')
    return loss+.4*body+level+delta+.01*tail,dict(body=body,activity_log_mse=level,activity_delta_mse=delta,activity_tail=tail)
