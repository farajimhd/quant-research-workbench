"""Causal current-quote cost token; execution, rewards and PPO remain unchanged."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import math
from pathlib import Path
import sys
if __package__ in (None,''):
    sys.path.insert(0,str(Path(__file__).resolve().parents[4]))
import numpy as np
import torch
from torch import nn
from torch.distributions import Categorical
from research.market_shards.v1.contract import sha
from research.rl_trading.v2.fees import charges
from research.rl_trading.v7 import train
from research.rl_trading.v7.environment import Environment
from research.rl_trading.v7.model import Policy

COST_FIELDS=('log_absolute_mark','allocation_fill_fraction','entry_fee_percent',
             'single_fill_exit_fee_percent','spread_slippage_percent','exit_capacity_fraction','log_quote_age')


def current_costs(env,j):
    """Observable current quote only, not a forecast of next-second fills."""
    values=np.zeros(7,np.float32);masks=np.zeros(7,np.float32)
    e,t,cfg=env.e,env.t,env.config
    mark=float(e['mark'][t,j]);stamp=int(e['quote_us'][t,j])
    if mark>0:
        values[0]=math.log1p(mark)/math.log(51.);masks[0]=1
    age=float(env.session.clocks[t])-stamp/1e6
    if stamp>0 and age>=0:
        values[6]=math.log1p(age)/math.log(3601.);masks[6]=1
    if not e['quote_valid'][t,j]:
        return np.concatenate((values,masks))
    def capacity(side):
        old,used=env.used.get((j,side),(-1,0))
        size=float(e['ask_size' if side==1 else 'bid_size'][t,j])
        return max(0,math.floor(size*cfg.displayed_participation)-(used if old==stamp else 0))
    buy=float(e['ask'][t,j])*(1+cfg.extra_slippage)
    sell=float(e['bid'][t,j])*(1-cfg.extra_slippage)
    desired=env.cash*cfg.allocation
    q=min(capacity(1),math.floor(desired/buy))
    def fee(q,price,side):return sum(charges(q,price,side,cfg).values())
    while q>0 and q*buy+fee(q,buy,1)>env.cash:q-=1
    notional=q*buy
    if desired>0:
        values[1]=notional/desired;masks[1]=1
    values[4]=100*(buy-sell)/buy;masks[4]=1
    if q>0 and notional>0:
        values[2]=100*fee(q,buy,1)/notional
        values[3]=100*fee(q,sell,-1)/notional
        values[5]=min(1.,capacity(-1)/q)
        masks[[2,3,5]]=1
    if not np.isfinite(values).all():raise ValueError('Nonfinite causal cost observation')
    return np.concatenate((values,masks))


class CostEnvironment(Environment):
    def observe(self):
        obs=super().observe()
        costs=np.zeros((5,14),np.float32)
        if self.t<len(self.session.selection):
            for slot,j in enumerate(self.session.selection[self.t]):
                if j>=0:costs[slot]=current_costs(self,int(j))
        obs['features']=np.concatenate((obs['features'],costs),axis=1)
        return obs


class CostPolicy(Policy):
    def __init__(self,feature_count=112,width=64,heads=4):
        if feature_count!=112:raise ValueError('Expected98 market plus14 cost channels')
        super().__init__(98,width,heads)
        self.timeframe=nn.Parameter(torch.cat((self.timeframe.detach(),torch.zeros(1,width)),0))

    def forward(self,features,account,holding,candidate_valid,action_mask):
        b=features.shape[0]
        x=self.frame(features.reshape(b*5,8,14).clamp(-20,20))+self.timeframe
        context,_=self.time_attention(x,x,x,need_weights=False)
        candidates=self.time_norm(x+context).mean(1).reshape(b,5,-1)
        tokens=torch.cat((candidates,self.holding(holding)[:,None],self.account(account)[:,None]),1)
        valid=torch.cat((candidate_valid,holding[:,:1]>0,torch.ones((b,1),dtype=torch.bool,device=x.device)),1)
        tokens=self.norm(self.market(tokens,src_key_padding_mask=~valid))
        control=self.control(tokens[:,-1])
        logits=torch.cat((control[:,:1],self.buy(tokens[:,:5]).squeeze(-1),control[:,1:]),1)
        return Categorical(logits=logits.masked_fill(~action_mask,-1e9)),self.value(tokens[:,-1]).squeeze(-1)


def main(argv=None):
    original_policy,original_environment,original_identity=train.Policy,train.Environment,train.source_identity
    def identity():
        return dict(original_identity(),experiment_sha256=sha(Path(__file__)),
                    experiment_recipe='current-quote-cost-token-v1',cost_fields=list(COST_FIELDS))
    train.Policy=CostPolicy;train.Environment=CostEnvironment;train.source_identity=identity
    try:return train.main(argv)
    finally:
        train.Policy=original_policy;train.Environment=original_environment;train.source_identity=original_identity


if __name__=='__main__':raise SystemExit(main())
