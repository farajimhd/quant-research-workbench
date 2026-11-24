"""Differentiable reconstruction, direction, range and source-aware OHLC losses.

No hard accuracy percentage is differentiated. Price deltas use log ratios;
activity deltas use log1p differences, preserving increases/decreases and zeros.
"""
import torch
from torch.nn import functional as F
from ..phase0_v7.objectives import objective as reconstruction
from ..phase0_v7.transforms import coordinates


def masked_mean(value, valid):
    return torch.where(valid,value,0).sum()/valid.sum().clamp_min(1)


def direction_loss(predicted, target, valid, scale):
    """Balance down/flat/up classes so unchanged activity cannot hide wrong signs."""
    pieces=[]; present=[]
    for sign in (-1,0,1):
        selected=valid & (target.sign()==sign)
        # A target-sized hinge is zero for an exact reconstruction, unlike a
        # logistic classifier that keeps pushing an already-correct move larger.
        # These are loss-only label weights, never encoder/decoder metadata.
        margin=target.abs().clamp_max(scale)
        residual=predicted/scale if sign==0 else F.relu(margin-sign*predicted)/scale
        error=F.smooth_l1_loss(residual,torch.zeros_like(residual),reduction='none')
        pieces.append(masked_mean(error,selected));present.append(selected.any())
    present=torch.stack(present)
    return (torch.stack(pieces)*present).sum()/present.sum().clamp_min(1)


def objective(model,out,values,valid):
    hidden=out.get('hidden')
    if (model.training or model.eval_mask) and hidden is not None:
        masked,_=reconstruction(model,out,values,valid & hidden[...,None])
        visible,_=reconstruction(model,out,values,valid & ~hidden[...,None])
        recon = 0.1 * masked + 0.9 * visible
    else:
        recon,_=reconstruction(model,out,values,valid)
        masked=visible=recon
    # Mask invalid source values BEFORE arithmetic, never because error is large.
    keep=valid[...,:18]
    target=torch.where(keep,values[...,:18],0)
    predicted=out['logs']
    y=target.reshape(-1,64,3,6);p=predicted.reshape_as(y);v=keep.reshape_as(y)
    opened,high,low,close=y[...,:4].unbind(-1)
    po,ph,pl,pc=p[...,:4].unbind(-1)
    candle=direction_loss(pc-po,close-opened,v[...,0]&v[...,3],model.config.price_direction_scale)
    consecutive=direction_loss(pc[:,1:]-pc[:,:-1],close[:,1:]-close[:,:-1],v[:,1:,:,3]&v[:,:-1,:,3],model.config.price_direction_scale)
    # Average-volume ratio replaces trade count here too. Counts never slip back
    # into the objective through a directional auxiliary term.
    code,code_valid=coordinates(target,keep)
    activity_indices=(4,5,10,11,16,17)
    ay=torch.stack([code[...,j] if j==5 else target[...,j] for j in activity_indices],-1)
    ap=torch.stack([out['code'][...,j] if j==5 else predicted[...,j] for j in activity_indices],-1)
    av=torch.stack([code_valid[...,j] if j==5 else keep[...,j] for j in activity_indices],-1)
    activity=direction_loss(ap[:,1:]-ap[:,:-1],ay[:,1:]-ay[:,:-1],av[:,1:]&av[:,:-1],model.config.activity_direction_scale)
    direction=(candle+consecutive+activity)/3
    range_error=((ph-pl)-(high-low))/.001
    ranges=masked_mean(F.smooth_l1_loss(range_error,torch.zeros_like(range_error),reduction='none'),v[...,1]&v[...,2])
    complete=v[...,:4].all(-1)
    source_consistent=(high>=torch.maximum(opened,close)) & (low<=torch.minimum(opened,close))
    violation=torch.stack((F.relu(po-ph),F.relu(pc-ph),F.relu(pl-po),F.relu(pl-pc),F.relu(pl-ph)),-1)/.001
    structural=F.smooth_l1_loss(violation,torch.zeros_like(violation),reduction='none').mean(-1)
    ohlc=masked_mean(structural,complete&source_consistent)
    c=model.config
    total=recon+c.direction_weight*direction+c.range_weight*ranges+c.ohlc_weight*ohlc
    return total,dict(reconstruction=recon,masked_reconstruction=masked,visible_reconstruction=visible,
                      direction=direction,candle_direction=candle,close_direction=consecutive,
                      activity_direction=activity,range=ranges,ohlc=ohlc,
                      source_inconsistent_ohlc=(complete&~source_consistent).sum().float())
