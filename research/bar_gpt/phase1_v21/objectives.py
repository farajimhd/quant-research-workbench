import torch
from functools import lru_cache
from torch.nn import functional as F
from ..phase1_v19.objectives import wick_loss
from .joint_base import loss as joint_base
from ..phase1_v11.transforms import decode as inverse_coordinates


@lru_cache(maxsize=8)
def range_edges(device):
    return torch.tensor([.0005,.001,.0025,.005],device=device,dtype=torch.float64).log1p()


def routed_output(output,active):
    """Identical forward values; choose coordinate routes for this loss only."""
    code=output['code']
    routed=torch.stack([code[...,j] if j%6 in active else code[...,j].detach() for j in range(18)],-1)
    constrained=torch.stack([routed[...,j].clamp_min(0) if j%6 in (2,3,4,5) else routed[...,j] for j in range(18)],-1)
    logs=inverse_coordinates(constrained,code.new_zeros(len(code),9),constrain=False)
    return dict(output,logs=logs)


def loss(model,values,valid,raw,frames,pos,old_latent,activity_scale,anchor_weight=1.):
    latent,_,out=model(values,valid,pos)
    separated=model.repair_arm=='joint_separated'
    range_out=routed_output(out,(2,3)) if separated else out
    candle_out=routed_output(out,(1,)) if separated else out
    known=torch.zeros_like(valid[...,0]).scatter(1,pos,True)
    terms=[];bin_terms=[]
    for b in (0,6,12):
        mask=valid[...,b+1]&valid[...,b+2]
        target=torch.log(torch.where(mask,raw[...,b+1].double(),1)/torch.where(mask,raw[...,b+2].double(),1))
        prediction=range_out['logs'][...,b+1].double()-range_out['logs'][...,b+2].double()
        weights=mask*torch.where(known,1.,.1)
        err=(prediction-target)*10000
        term=F.smooth_l1_loss(err,torch.zeros_like(err),reduction='none')
        terms.append((torch.where(mask,term,0)*weights).sum()/weights.sum().clamp_min(1))
        if model.repair_arm in ('metric','metric_log','joint_metric_log','joint_repaired','joint_separated'):
            edges=range_edges(prediction.device)
            logits=(prediction[...,None]-edges)*10000
            labels=(target[...,None]>edges).to(logits.dtype)
            ordinal=F.binary_cross_entropy_with_logits(logits,labels,reduction='none').mean(-1)
            bin_terms.append((torch.where(mask,ordinal,0)*weights).sum()/weights.sum().clamp_min(1))
    shape=torch.stack(terms).mean()
    wick=wick_loss(out['code'],raw,valid,known)
    activity=shape.new_zeros(());direction=shape.new_zeros(())
    frame_weight=None
    if hasattr(model,'activity_frame_weights'):
        frame_weight=((frames[:,None]==model.activity_frame_codes).to(model.activity_frame_weights.dtype)*model.activity_frame_weights).sum(-1)[:,None]
    bins=torch.stack(bin_terms).mean() if bin_terms else shape.new_zeros(())
    if model.repair_arm not in ('range','body_direction'):
        for j in (4,5,10,11,16,17):
            mask=valid[...,j];target=torch.where(mask,raw[...,j].double(),0)
            code=out['code'][...,j].double();pred=code.clamp_min(0)
            weights=mask*torch.where(known,1.,.1)
            if frame_weight is not None:weights=weights*frame_weight
            log=(code-target.log1p()).square()
            # Multiplicatively symmetric error in (1+activity), used only
            # in the loss. No target-dependent reference reaches the decoder.
            error=(pred-target.log1p()).abs().expm1() if model.repair_arm in ('both_symmetric','metric') else log
            pair=mask[:,1:]&mask[:,:-1]&known[:,1:]&known[:,:-1]
            pair_weight=pair if frame_weight is None else pair*frame_weight
            delta=(pred[:,1:]-pred[:,:-1])-(target[:,1:].log1p()-target[:,:-1].log1p())
            activity+=((torch.where(mask,error+.1*log,0)*weights).sum()/weights.sum().clamp_min(1)+.5*(torch.where(pair,delta.square(),0)*pair_weight).sum()/pair_weight.sum().clamp_min(1))/6
            if model.repair_arm in ('metric','metric_log','joint_metric_log','joint_repaired','joint_separated'):
                actual=target[:,1:].log1p()-target[:,:-1].log1p()
                moving=pair&(actual!=0)
                moving_weight=moving if frame_weight is None else moving*frame_weight
                logits=(pred[:,1:]-pred[:,:-1])/.1
                term=F.binary_cross_entropy_with_logits(logits,(actual>0).to(logits.dtype),reduction='none')
                direction+=(torch.where(moving,term,0)*moving_weight).sum()/moving_weight.sum().clamp_min(1)/6
    extra=shape+.05*wick+activity+.5*bins+.1*direction
    base=shape.new_zeros(());candle=shape.new_zeros(())
    if model.repair_arm=='body_direction':
        body=shape.new_zeros(())
        for b in (0,6,12):
            mask=valid[...,b]&valid[...,b+3]
            target=torch.log(torch.where(mask,raw[...,b].double(),1)/torch.where(mask,raw[...,b+3].double(),1))
            error=(out['code'][...,b+1].double()-target)*10000
            weights=mask*torch.where(known,1.,.1)
            term=F.smooth_l1_loss(error,torch.zeros_like(error),reduction='none')
            body+=(torch.where(mask,term,0)*weights).sum()/weights.sum().clamp_min(1)/3
            moving=mask&known&(raw[...,b]!=raw[...,b+3])
            logits=(out['logs'][...,b+3].double()-out['logs'][...,b].double())*10000
            ce=F.binary_cross_entropy_with_logits(logits,(raw[...,b+3]>raw[...,b]).to(logits.dtype),reduction='none')
            candle+=torch.where(moving,ce,0).sum()/moving.sum().clamp_min(1)/3
        total=body+.5*candle+.01*shape
        base=body
    elif model.repair_arm.startswith('joint_'):
        base,_=joint_base(model,values,valid,raw,frames,pos,old_latent,activity_scale,latent,out,include_activity=model.repair_arm not in ('joint_repaired','joint_separated'),level_output=routed_output(out,(0,4,5)) if separated else None)
        if model.repair_arm in ('joint_metric_log','joint_repaired','joint_separated'):
            for b in (0,6,12):
                mask=valid[...,b]&valid[...,b+3]&(raw[...,b]!=raw[...,b+3])
                logits=(candle_out['logs'][...,b+3].double()-candle_out['logs'][...,b].double())*10000
                term=F.binary_cross_entropy_with_logits(logits,(raw[...,b+3]>raw[...,b]).to(logits.dtype),reduction='none')
                weights=mask*torch.where(known,1.,.1)
                candle+=(torch.where(mask,term,0)*weights).sum()/weights.sum().clamp_min(1)/3
        if model.repair_arm in ('joint_repaired','joint_separated'):
            # Keep raw-price, body, wick and bar-anchor objectives. Replace the
            # old overlapping activity penalties with the tested log objective.
            activity_weight=getattr(model,'activity_weight',1.)
            total=base+activity_weight*(activity+.1*direction)+.1*shape+.05*bins+.5*candle
        else:total=base+(.1*extra+.05*candle if model.repair_arm=='joint_metric_log' else 0)
    else:total=extra
    return total,dict(range=shape,wick=wick,activity=activity,range_bins=bins,activity_direction=direction,joint_base=base,candle_direction=candle)
