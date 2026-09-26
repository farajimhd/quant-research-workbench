"""Permutation-equivariant market actor with stochastic mode and continuous size."""
import numpy as np
import torch
from torch import nn
from torch.distributions import Beta, Categorical

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
        self.size = nn.Linear(width,2)
        self.critic = nn.Sequential(nn.Linear(width,width),nn.Tanh(),nn.Linear(width,1))

    def forward(self, batch):
        x = batch['market']
        b,n,h,f = x.shape
        temporal = self.temporal(x.reshape(b*n,h,f).transpose(1,2))
        encoded = self.history(torch.cat((temporal[:,:,-1],temporal.mean(dim=2)),dim=-1)).reshape(b,n,-1)
        encoded = encoded+self.position(batch['position'])
        account = self.account(batch['account']).unsqueeze(1)
        mask = torch.cat((torch.zeros((b,1),dtype=torch.bool,device=x.device),~batch['valid']),dim=1)
        context = self.market(torch.cat((account,encoded),dim=1),src_key_padding_mask=mask)
        logits = self.actor(context[:,1:]).masked_fill(~batch['action_mask'],-1e9)
        parameters = torch.nn.functional.softplus(self.size(context[:,1:]))+1.01
        return Categorical(logits=logits), Beta(parameters[...,0],parameters[...,1]), self.critic(context[:,0]).squeeze(-1)

    def action(self, batch, modes=None, sizes=None, *, deterministic=False):
        category, sizing, value = self(batch)
        if modes is None:
            modes = category.probs.argmax(dim=-1) if deterministic else category.sample()
        if sizes is None:
            sizes = sizing.mean if deterministic else sizing.sample()
        sizes = sizes.clamp(1e-6,1-1e-6)
        active_size = ((modes == 1) | (modes == 2)) & batch['valid']
        logprob = (category.log_prob(modes)*batch['valid'] + sizing.log_prob(sizes)*active_size).sum(dim=1)
        entropy = (category.entropy()*batch['valid']+sizing.entropy()*active_size).sum(dim=1)
        return modes,sizes,logprob,entropy,value
