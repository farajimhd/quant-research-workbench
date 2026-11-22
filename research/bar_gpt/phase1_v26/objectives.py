"""Family reconstruction losses plus the trained price bar-latent anchor."""
from types import SimpleNamespace
import torch
from ..phase1_v25.objectives import loss as family_loss
from ..phase1_v12.model import gather


def loss(model,out,x,v,raw,teacher,pp,ap):
    shared={k:getattr(model,k) for k in ('mean','scale','loss_factor','price_indices','activity_indices','range_edges')}
    price,pt=family_loss(SimpleNamespace(kind='price',**shared),out,x,v,raw,pp)
    activity,at=family_loss(SimpleNamespace(kind='activity',**shared),out,x,v,raw,ap)
    distance=((out['price_latent']-gather(teacher,pp))/model.price.temporal.scale).square().mean(-1)
    mask=gather(v,pp)[...,:18].any(-1);anchor=torch.where(mask,distance,0).sum()/mask.sum().clamp_min(1)
    return price+activity+anchor,dict(price=price,activity=activity,anchor=anchor,candle=pt['candle_direction'],range=pt['range'],activity_direction=at['activity_direction'])
