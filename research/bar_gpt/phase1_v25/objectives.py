"""V21 family losses, independently normalized; no teacher or latent anchor."""
import torch
from torch.nn import functional as F
from ..phase1_v11.transforms import coordinates,PRICES,ACTIVITY
from ..phase1_v19.objectives import wick_loss


def mean(error,weight):
    return (torch.where(weight>0,error,0)*weight).sum()/weight.sum().clamp_min(1)


def loss(model,out,x,v,raw,pos):
    known=torch.zeros_like(v[...,0]).scatter(1,pos,True)
    weights=torch.where(known,1.,.1);code,cm=coordinates(x[...,:18],v[...,:18])
    coordinate=F.smooth_l1_loss(out['normalized'],(code-model.mean)/model.scale,reduction='none')
    err=(out['logs']-x[...,:18])*model.loss_factor
    huber=F.smooth_l1_loss(err,torch.zeros_like(err),reduction='none')
    channel=(torch.where(v[...,:18],huber,0).sum((0,1))/v[...,:18].sum((0,1)).clamp_min(1))
    zero=out['code'].sum()*0;price=zero;activity=zero;body=zero;ranges=zero;bins=zero;candle=zero;direction=zero
    if model.kind!='activity':
        for b in (0,6,12):
            mask=v[...,b]&v[...,b+3];o=torch.where(mask,raw[...,b].double(),1);c=torch.where(mask,raw[...,b+3].double(),1)
            target=(o/c).log();error=(out['code'][...,b+1].double()-target)*10000
            body=body+mean(F.smooth_l1_loss(error,torch.zeros_like(error),reduction='none'),mask*weights)/3
            logits=(out['logs'][...,b+3].double()-out['logs'][...,b].double())*10000
            candle=candle+mean(F.binary_cross_entropy_with_logits(logits,(c>o).double(),reduction='none'),mask*(o!=c)*weights)/3
            mask=v[...,b+1]&v[...,b+2]
            target=(torch.where(mask,raw[...,b+1].double(),1)/torch.where(mask,raw[...,b+2].double(),1)).log()
            prediction=out['logs'][...,b+1].double()-out['logs'][...,b+2].double();error=(prediction-target)*10000
            ranges=ranges+mean(F.smooth_l1_loss(error,torch.zeros_like(error),reduction='none'),mask*weights)/3
            edges=prediction.new_tensor([.0005,.001,.0025,.005]).log1p()
            term=F.binary_cross_entropy_with_logits((prediction[...,None]-edges)*10000,(target[...,None]>edges).double(),reduction='none').mean(-1)
            bins=bins+mean(term,mask*weights)/3
        price=.5*channel[list(PRICES)].mean()+.1*mean(coordinate[...,list(PRICES)],cm[...,list(PRICES)].float())+.5*body+.05*wick_loss(out['code'],raw,v,known)+.1*ranges+.05*bins+.5*candle
    if model.kind!='price':
        for j in ACTIVITY:
            mask=v[...,j];target=torch.where(mask,raw[...,j].double(),0).log1p();pred=out['code'][...,j].double()
            pair=mask[:,1:]&mask[:,:-1]&known[:,1:]&known[:,:-1]
            delta=pred[:,1:]-pred[:,:-1];actual=target[:,1:]-target[:,:-1]
            activity=activity+(1.1*mean((pred-target).square(),mask*weights)+.5*mean((delta-actual).square(),pair.float()))/6
            direction=direction+mean(F.binary_cross_entropy_with_logits(delta/.1,(actual>0).double(),reduction='none'),(pair&(actual!=0)).float())/6
        activity=activity+.1*direction+.5*channel[list(ACTIVITY)].mean()+.1*mean(coordinate[...,list(ACTIVITY)],cm[...,list(ACTIVITY)].float())
    return price+activity,dict(price=price,activity=activity,body=body,range=ranges,range_bins=bins,candle_direction=candle,activity_direction=direction)
