import torch
from torch.nn import functional as F
from ..phase1_v19.objectives import wick_loss


def loss(model,values,valid,raw,frames,pos,old_latent,activity_scale,anchor_weight=1.):
    _,_,out=model(values,valid,pos)
    known=torch.zeros_like(valid[...,0]).scatter(1,pos,True)
    terms=[]
    for b in (0,6,12):
        mask=valid[...,b+1]&valid[...,b+2]
        target=torch.log(torch.where(mask,raw[...,b+1].double(),1)/torch.where(mask,raw[...,b+2].double(),1))
        prediction=out['logs'][...,b+1].double()-out['logs'][...,b+2].double()
        weights=mask*torch.where(known,1.,.1)
        err=(prediction-target)*10000
        term=F.smooth_l1_loss(err,torch.zeros_like(err),reduction='none')
        terms.append((torch.where(mask,term,0)*weights).sum()/weights.sum().clamp_min(1))
    shape=torch.stack(terms).mean()
    wick=wick_loss(out['code'],raw,valid,known)
    activity=shape.new_zeros(())
    if model.repair_arm!='range':
        for j in (4,5,10,11,16,17):
            mask=valid[...,j];target=torch.where(mask,raw[...,j].double(),0)
            code=out['code'][...,j].double();pred=code.clamp_min(0)
            weights=mask*torch.where(known,1.,.1)
            log=(code-target.log1p()).square()
            # Multiplicatively symmetric error in (1+activity), used only
            # in the loss. No target-dependent reference reaches the decoder.
            error=(pred-target.log1p()).abs().expm1() if model.repair_arm=='both_symmetric' else log
            pair=mask[:,1:]&mask[:,:-1]&known[:,1:]&known[:,:-1]
            delta=(pred[:,1:]-pred[:,:-1])-(target[:,1:].log1p()-target[:,:-1].log1p())
            activity+=((torch.where(mask,error+.1*log,0)*weights).sum()/weights.sum().clamp_min(1)+.5*torch.where(pair,delta.square(),0).sum()/pair.sum().clamp_min(1))/6
    return shape+.05*wick+activity,dict(range=shape,wick=wick,activity=activity)
