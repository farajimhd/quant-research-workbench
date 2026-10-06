"""Local causal-window controls for diagnosing majority-action collapse."""
import numpy as np
import torch
from torch import nn
from torch.nn import functional as F
from research.rl_trading.v6.temporal_encoders import TemporalCandleEncoder
from research.rl_trading.v6.hierarchical_heads import HierarchicalTickerHeads,HierarchicalForecast

VERSION='rl-v6-bias-controlled-local-teacher-v1'


def balance_weights(actions,weights,*,mode='branch'):
    """Fixed train-only weighted class masses, never batch/development priors."""
    actions=np.asarray(actions);weights=np.asarray(weights)
    if actions.ndim!=1 or not np.issubdtype(actions.dtype,np.integer) or weights.shape!=actions.shape or not len(actions) or not np.isin(actions,[0,1,2,3]).all() or not np.isfinite(weights).all() or (weights<=0).any():
        raise ValueError('Positive finite sample weights and known actions required')
    mass=np.bincount(actions,weights=weights,minlength=4)
    if not np.isfinite(mass).all() or not np.isfinite(mass.sum()):raise ValueError('Class mass overflow')
    present=mass>0
    if mode=='natural':return np.ones(4,np.float32)
    if mode=='sqrt':
        values=np.zeros(4);values[present]=1/np.sqrt(mass[present])
        return (values/((values*mass).sum()/mass.sum())).astype(np.float32)
    if mode not in ('branch','focal'):raise ValueError('Unknown classification balance')
    values=np.zeros(4)
    branches=[pair for pair in ((0,1),(2,3)) if mass[list(pair)].sum()>0]
    for pair in branches:
        classes=[i for i in pair if mass[i]>0]
        for i in classes:values[i]=mass.sum()/(len(branches)*len(classes)*mass[i])
    return values.astype(np.float32)


class LocalWindowTeacher(nn.Module):
    def __init__(self,architecture='lag',*,width=128,structured=False,shared_forecast=False,stationary=False,normalization=None):
        super().__init__();self.architecture=architecture;self.shared_forecast=shared_forecast
        self.encoder=TemporalCandleEncoder(width,architecture=architecture,structured=structured)
        self.market=nn.Linear(147,width,bias=False)
        self.holding=nn.Sequential(nn.Linear(width+11,width),nn.GELU(),nn.LayerNorm(width))
        self.context=nn.Sequential(nn.Linear(width,width),nn.GELU(),nn.LayerNorm(width))
        self.heads=HierarchicalTickerHeads(width)
        self.size=nn.Linear(width,1)
        future_heads=self.heads if shared_forecast else HierarchicalTickerHeads(width)
        self.forecast=HierarchicalForecast(width,future_heads)
        self.forecast_gru=nn.GRUCell(width*2+3,width)
        self.stationary=stationary
        if stationary:
            if normalization is None or normalization.get('scope')!='train_only':raise ValueError('Stationary inputs require training-only normalization')
            self.register_buffer('input_mean',torch.as_tensor(normalization['mean'],dtype=torch.float32))
            self.register_buffer('input_std',torch.as_tensor(normalization['std'],dtype=torch.float32))
            self.price_anchor=nn.Linear(1,width,bias=False)
            nn.init.normal_(self.price_anchor.weight,std=.001)

    def forward(self,windows,present,market,held,*,future_steps=0,forcing=None):
        anchor=None
        if self.stationary:
            raw_close=windows[...,3]*self.input_std[3]+self.input_mean[3]
            valid=present & (windows[...,35]>.5)
            positions=torch.arange(windows.shape[1],device=windows.device)[None].expand_as(present)
            last=torch.where(valid,positions,-1).max(1).values
            anchor=raw_close.gather(1,last.clamp_min(0)[:,None]).flatten()
            anchor=torch.where(last>=0,anchor,torch.zeros_like(anchor))
            # O/H/L already are bps relative to each completed close.
            # Only absolute log-close needs a causal window anchor.
            windows=windows.clone();windows[...,3]=torch.where(valid,(raw_close-anchor[:,None])*10,0.)
            market=market.clone();market[:,3]=0
        projected=self.encoder.project(windows)*present[...,None]
        local=self.encoder.encode_history(projected,present)
        features=held.clone();features[:,:2]=torch.log1p(features[:,:2].clamp_min(0));features[:,2]=torch.log1p(features[:,2].clamp_min(0))/10
        features[:,3:6]*=10
        if self.stationary:
            features[:,1]=torch.where(held[:,0]>0,(held[:,1].clamp_min(1e-6).log()-anchor)*10,torch.zeros_like(anchor))
        holding=held[:,0]>0
        local=torch.where(holding[:,None],self.holding(torch.cat((local,features),1)),local)
        price=0 if anchor is None else self.price_anchor(((anchor-self.input_mean[3])/self.input_std[3])[:,None])
        representation=self.context(local+self.market(market)+price)
        out=self.heads(representation,holding)
        # Conditional positive odds; invalid branches never enter a reduction.
        positive=torch.where(holding,out.logits[:,3],out.logits[:,0])
        result=dict(logit=positive,quality=out.quality,ratio=self.size(representation).sigmoid().flatten(),context=representation)
        if future_steps:
            result['future']=self.forecast(representation,self.forecast_gru,steps=future_steps,previous_targets=forcing,validated=True)
            result['future_quality']=self.forecast.quality_predictions
        return result


def current_objective(logits,actions,weights,class_weights,*,focal=False):
    positive=(actions==0)|(actions==3)
    loss=F.binary_cross_entropy_with_logits(logits,positive.to(logits.dtype),reduction='none')
    if focal:
        correct=torch.where(positive,logits.sigmoid(),(-logits).sigmoid())
        loss=loss*(1-correct)**2
    return loss*weights*class_weights[actions]
