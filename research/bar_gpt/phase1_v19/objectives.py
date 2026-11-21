"""Add an inverse-aware activity penalty; targets are used only by the loss."""
import torch
from torch.nn import functional as F
from ..phase1_v18.objectives import objective as previous,calibrate,constants

ARMS=('control','relative01','relative1','unscaled_relative01')


def relative_activity_loss(code,raw,valid):
    indices,_=constants(raw.device);mask=valid.index_select(-1,indices)
    target=torch.where(mask,raw.index_select(-1,indices).double(),0).log1p()
    # The floor is the existing Phase0 inverse, not a new output/target ceiling.
    predicted=code.index_select(-1,indices).double().clamp_min(0)
    error=torch.where(mask,predicted-target,0)
    # Exactly (predicted_activity-target_activity)/(1+target_activity).
    # No clipping of valid values or exponentials. Nonfinite training fails closed.
    relative=error.expm1()
    loss=F.smooth_l1_loss(relative,torch.zeros_like(relative),beta=.1,reduction='none')
    return (torch.where(mask,loss,0).sum((0,1))/mask.sum((0,1)).clamp_min(1)).mean()


def objective(model,teacher,pred,latent,valid,pos,raw,source_valid,arm,frames,scale_sq):
    if arm not in ARMS:raise ValueError(arm)
    loss,parts=previous(model,teacher,pred,latent,valid,pos,raw,source_valid,'joint',frames,scale_sq)
    if arm=='control':return loss,dict(parts,activity_relative=loss.new_zeros(()))
    code=teacher.decoder(model.denormalize(pred).float())*teacher.scale[:18]+teacher.mean[:18]
    relative=relative_activity_loss(code,raw,source_valid)
    return loss+(.1 if arm in ('relative01','unscaled_relative01') else 1.)*relative,dict(parts,activity_relative=relative)
