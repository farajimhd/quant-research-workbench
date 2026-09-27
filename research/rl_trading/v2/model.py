"""Permutation-equivariant market actor with stochastic mode and continuous size."""
import numpy as np
import torch
from torch import nn
from torch.distributions import Bernoulli, Beta, Categorical

from research.rl_trading.v2.environment import ACCOUNT_FEATURES, POSITION_FEATURES


def collate(observations, device='cpu'):
    count = max(len(x['ids']) for x in observations)
    batch = {}
    for key in ('market','position','valid','action_mask'):
        values = []
        for obs in observations:
            value = obs[key]
            padded = np.zeros((count,*value.shape[1:]),dtype=value.dtype)
            padded[:len(value)] = value
            if key == 'action_mask':
                padded[len(value):,0] = True
            values.append(padded)
        batch[key] = torch.as_tensor(np.stack(values),device=device)
    batch['account'] = torch.as_tensor(np.stack([x['account'] for x in observations]),device=device)
    return batch


class PortfolioPolicy(nn.Module):
    def __init__(self, features, width=64, heads=4):
        super().__init__()
        if features < 1 or heads < 1 or width < 4 or width % heads:
            raise ValueError('Policy width must be divisible by attention heads')
        self.temporal = nn.Sequential(nn.Conv1d(features,width,3,padding=1),nn.GELU(),
                                      nn.Conv1d(width,width,3,padding=1),nn.GELU())
        self.history = nn.Linear(width*2,width)
        self.position = nn.Linear(POSITION_FEATURES,width)
        self.account = nn.Linear(ACCOUNT_FEATURES,width)
        layer = nn.TransformerEncoderLayer(width,heads,width*2,dropout=0.,batch_first=True)
        self.market = nn.TransformerEncoder(layer,1,enable_nested_tensor=False)
        self.actor = nn.Linear(width,4)
        self.trade_gate = nn.Linear(width,1)
        nn.init.zeros_(self.trade_gate.weight)
        nn.init.constant_(self.trade_gate.bias,-5.)
        self.size = nn.Linear(width,6)  # allocation, stop distance, target distance
        self.critic = nn.Sequential(nn.Linear(width,width),nn.Tanh(),nn.Linear(width,1))

    def forward(self, batch, *, scheduler=False):
        x = batch['market']
        b,n,h,f = x.shape
        temporal = self.temporal(x.reshape(b*n,h,f).transpose(1,2))
        encoded = self.history(torch.cat((temporal[:,:,-1],temporal.mean(dim=2)),dim=-1)).reshape(b,n,-1)
        encoded = encoded+self.position(batch['position'])
        account = self.account(batch['account']).unsqueeze(1)
        mask = torch.cat((torch.zeros((b,1),dtype=torch.bool,device=x.device),~batch['valid']),dim=1)
        context = self.market(torch.cat((account,encoded),dim=1),src_key_padding_mask=mask)
        logits = self.actor(context[:,1:]).masked_fill(~batch['action_mask'],-1e9)
        parameters = torch.nn.functional.softplus(self.size(context[:,1:])).reshape(b,n,3,2)+1.01
        category = Categorical(logits=logits)
        sizing = Beta(parameters[...,0],parameters[...,1])
        value = self.critic(context[:,0]).squeeze(-1)
        if not scheduler:
            return category,sizing,value
        eligible = batch['action_mask'][...,1:].any(dim=(1,2))
        gate_logits = self.trade_gate(context[:,0]).squeeze(-1).masked_fill(~eligible,-1e9)
        choice_logits = logits[...,1:].reshape(b,-1)
        return category,sizing,value,Bernoulli(logits=gate_logits),Categorical(logits=choice_logits)

    def action(self, batch, modes=None, sizes=None, *, deterministic=False):
        _,sizing,value,gate,choice = self(batch,scheduler=True)
        b,n = batch['valid'].shape
        if modes is None:
            trade = (gate.probs >= .5).long() if deterministic else gate.sample().long()
            chosen = choice.probs.argmax(dim=-1) if deterministic else choice.sample()
            modes = torch.zeros((b,n),dtype=torch.long,device=batch['valid'].device)
            modes.scatter_(1,(chosen//3).unsqueeze(1),
                ((chosen%3+1)*trade).unsqueeze(1))
        else:
            trade = (modes != 0).sum(dim=1)
            if torch.any(trade > 1):
                raise ValueError('Only one discretionary action is sampled per second')
            trade = trade.long()
            slot = (modes != 0).long().argmax(dim=1)
            chosen = slot*3+(modes.gather(1,slot.unsqueeze(1)).squeeze(1)-1).clamp_min(0)
        if sizes is None:
            sizes = sizing.mean if deterministic else sizing.sample()
        sizes = sizes.clamp(1e-6,1-1e-6)
        active_size = ((modes == 1) | (modes == 2)) & batch['valid']
        entry = (modes == 1) & (batch['position'][...,0] == 0) & batch['valid']
        active = torch.stack((active_size,entry,entry),dim=-1)
        size_logprob = (sizing.log_prob(sizes)*active).sum(dim=(1,2))
        size_entropy = (sizing.entropy()*active).sum(dim=(1,2))
        logprob = gate.log_prob(trade.float()) + trade*choice.log_prob(chosen) + size_logprob
        entropy = gate.entropy()+gate.probs*choice.entropy()+trade*size_entropy
        return modes,sizes,logprob,entropy,value
