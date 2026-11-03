"""Causal attention encoder with training-only future-return prediction heads."""
import torch
from torch import nn
from torch.distributions import Categorical
from .cost_aware import CostPolicy


class PredictivePolicy(CostPolicy):
    def __init__(self,feature_count=112,width=64,heads=4):
        super().__init__(feature_count,width,heads)
        self.forecast=nn.Linear(width,3)
        nn.init.zeros_(self.forecast.weight);nn.init.zeros_(self.forecast.bias)
        scale=torch.ones(8,14)
        scale[:7,3:5]=.1
        scale[:7,:3]=torch.tensor([100.,100.,50.,10.,10.,100.,100.])[:,None]
        self.register_buffer('input_scale',scale)

    def encode(self,features,account,holding,candidate_valid):
        b=features.shape[0]
        groups=features.reshape(b*5,8,14)*self.input_scale
        x=self.frame(groups.clamp(-20,20))+self.timeframe
        context,_=self.time_attention(x,x,x,need_weights=False)
        candidates=self.time_norm(x+context).mean(1).reshape(b,5,-1)
        tokens=torch.cat((candidates,self.holding(holding)[:,None],self.account(account)[:,None]),1)
        valid=torch.cat((candidate_valid,holding[:,:1]>0,torch.ones((b,1),dtype=torch.bool,device=x.device)),1)
        return self.norm(self.market(tokens,src_key_padding_mask=~valid))

    def predict(self,features,account,holding,candidate_valid):
        return self.forecast(self.encode(features,account,holding,candidate_valid)[:,:5])

    def forward(self,features,account,holding,candidate_valid,action_mask):
        tokens=self.encode(features,account,holding,candidate_valid)
        prediction=self.forecast(tokens[:,:5])
        costs=features[:,:,-14:]
        # Convert log-return point forecasts to price ratios before applying the
        # current quote's spread and single-fill fee estimate. Equal horizon
        # weighting, +/-50 log-percent score clipping and gain10 are explicit
        # initial-policy choices, not learned execution guarantees.
        ratio=torch.exp(prediction.clamp(-50,50)/100).mean(-1)
        net=100*((1-costs[:,:,4]/100)*ratio-1)-costs[:,:,2]-costs[:,:,3]
        buy=self.buy(tokens[:,:5]).squeeze(-1)+10*net
        control=self.control(tokens[:,-1])
        logits=torch.cat((control[:,:1],buy,control[:,1:]),1)
        return Categorical(logits=logits.masked_fill(~action_mask,-1e9)),self.value(tokens[:,-1]).squeeze(-1)
