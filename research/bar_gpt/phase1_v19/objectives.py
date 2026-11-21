"""Add an inverse-aware activity penalty; targets are used only by the loss."""
import torch
from torch.nn import functional as F
from ..phase1_v18.objectives import objective as previous,calibrate,constants

ARMS=('control','relative01','relative1','unscaled_relative01','relative_all','relative_visible_symmetric','counts_only')


def relative_price_loss(logs,raw,valid):
    indices=torch.tensor([j for b in (0,6,12) for j in range(b,b+4)],device=raw.device)
    mask=valid.index_select(-1,indices)
    target=torch.where(mask,raw.index_select(-1,indices).double(),1).log()
    error=torch.where(mask,logs.index_select(-1,indices).double()-target,0)
    relative=error.expm1()
    loss=F.smooth_l1_loss(relative,torch.zeros_like(relative),beta=.1,reduction='none')
    return (torch.where(mask,loss,0).sum((0,1))/mask.sum((0,1)).clamp_min(1)).mean()


def relative_activity_loss(code,raw,valid,symmetric_visible=None):
    indices,_=constants(raw.device);mask=valid.index_select(-1,indices)
    target=torch.where(mask,raw.index_select(-1,indices).double(),0).log1p()
    # The floor is the existing Phase0 inverse, not a new output/target ceiling.
    predicted=code.index_select(-1,indices).double().clamp_min(0)
    error=torch.where(mask,predicted-target,0)
    if symmetric_visible is not None:error=torch.where(symmetric_visible[...,None],error.abs(),error)
    # Exactly (predicted_activity-target_activity)/(1+target_activity).
    # No clipping of valid values or exponentials. Nonfinite training fails closed.
    relative=error.expm1()
    loss=F.smooth_l1_loss(relative,torch.zeros_like(relative),beta=.1,reduction='none')
    return (torch.where(mask,loss,0).sum((0,1))/mask.sum((0,1)).clamp_min(1)).mean()


def objective(model,teacher,pred,latent,valid,pos,raw,source_valid,arm,frames,scale_sq):
    if arm not in ARMS:raise ValueError(arm)
    if arm=='counts_only':
        code=teacher.decoder(model.denormalize(pred).float())*teacher.scale[:18]+teacher.mean[:18]
        indices=torch.tensor([5,11,17],device=raw.device);mask=source_valid.index_select(-1,indices)
        target=torch.where(mask,raw.index_select(-1,indices).double(),0).log1p()
        prediction=code.index_select(-1,indices).double()
        known=torch.zeros_like(valid).scatter(1,pos,True)
        weights=mask*torch.where(known,1.,.1)[...,None]
        error=torch.where(mask,prediction-target,0)
        level=(error.square()*weights).sum()/weights.sum().clamp_min(1)
        pair=mask[:,1:]&mask[:,:-1]&known[:,1:,None]&known[:,:-1,None]
        delta=(prediction[:,1:].clamp_min(0)-prediction[:,:-1].clamp_min(0))-(target[:,1:]-target[:,:-1])
        change=torch.where(pair,delta.square(),0).sum()/pair.sum().clamp_min(1)
        relative=torch.where(known[...,None],error.abs(),error).expm1()
        tail=(F.smooth_l1_loss(relative,torch.zeros_like(relative),beta=.1,reduction='none')*weights).sum()/weights.sum().clamp_min(1)
        return level+change+.1*tail,dict(count_log_mse=level,count_delta_mse=change,count_relative=tail)
    loss,parts=previous(model,teacher,pred,latent,valid,pos,raw,source_valid,'joint',frames,scale_sq)
    if arm=='control':return loss,dict(parts,activity_relative=loss.new_zeros(()),price_relative=loss.new_zeros(()))
    code=teacher.decoder(model.denormalize(pred).float())*teacher.scale[:18]+teacher.mean[:18]
    known=torch.zeros_like(valid).scatter(1,pos,True) if arm=='relative_visible_symmetric' else None
    relative=relative_activity_loss(code,raw,source_valid,known)
    price=relative_price_loss(teacher.decode(model.denormalize(pred).float())['logs'],raw,source_valid) if arm in ('relative_all','relative_visible_symmetric') else loss.new_zeros(())
    return loss+(.1 if arm in ('relative01','unscaled_relative01','relative_all','relative_visible_symmetric') else 1.)*relative+price,dict(parts,activity_relative=relative,price_relative=price)
