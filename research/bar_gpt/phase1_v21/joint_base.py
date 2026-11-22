import torch
from torch.nn import functional as F
from ..phase0_v1.model import objective as raw_objective
from ..phase1_v18.objectives import penalties
from ..phase1_v19.objectives import relative_activity_loss,wick_loss
from ..phase1_v12.model import gather


def loss(model,values,valid,raw,frames,pos,old_latent,activity_scale,latent,output,anchor_weight=1.,include_activity=True):
    base,_=raw_objective(model,output,values,valid)
    known=torch.zeros_like(valid[...,0]).scatter(1,pos,True)
    if include_activity:
        level,change,tail=penalties(output['code'],raw,valid,known,frames,activity_scale,False)
        relative=relative_activity_loss(output['code'],raw,valid,known)
    else:level=change=tail=relative=base.new_zeros(())
    wick=wick_loss(output['code'],raw,valid,known)
    body=base.new_zeros(())
    for b in (0,6,12):
        mask=valid[...,b]&valid[...,b+3]
        target=torch.log(torch.where(mask,raw[...,b].double(),1)/torch.where(mask,raw[...,b+3].double(),1))
        error=(output['code'][...,b+1].double()-target)*10000
        weight=mask*torch.where(known,1.,.1)
        term=F.smooth_l1_loss(error,torch.zeros_like(error),reduction='none')
        body=body+(torch.where(mask,term,0)*weight).sum()/weight.sum().clamp_min(1)/3
    mask=gather(valid,pos)[...,:18].any(-1)
    distance=((latent-gather(old_latent,pos))/model.temporal.scale).square().mean(-1)
    anchor=torch.where(mask,distance,0).sum()/mask.sum().clamp_min(1)
    total=base+.5*body+level+change+.01*tail+.1*relative+.05*wick+anchor_weight*anchor
    return total,dict(raw=base,body=body,activity_level=level,activity_change=change,activity_tail=tail,activity_relative=relative,wick=wick,bar_latent_anchor=anchor)
