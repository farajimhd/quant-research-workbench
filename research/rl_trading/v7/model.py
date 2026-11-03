"""Shared timeframe encoder and cross-candidate/account attention, without ticker IDs."""
import torch
from torch import nn
from torch.distributions import Categorical


class Policy(nn.Module):
    def __init__(self, feature_count=98, width=64, heads=4):
        super().__init__()
        if feature_count != 98:
            raise ValueError('Expected seven timeframe groups with seven values and masks')
        self.frame = nn.Sequential(nn.Linear(14, width), nn.LayerNorm(width), nn.GELU())
        self.timeframe = nn.Parameter(torch.randn(7, width)*.02)
        self.time_attention = nn.MultiheadAttention(width, heads, dropout=0., batch_first=True)
        self.time_norm = nn.LayerNorm(width)
        self.account = nn.Sequential(nn.Linear(8, width), nn.LayerNorm(width), nn.GELU())
        self.holding = nn.Sequential(nn.Linear(8, width), nn.LayerNorm(width), nn.GELU())
        layer = nn.TransformerEncoderLayer(width, heads, 2*width, dropout=0.,
                                           activation='gelu', batch_first=True, norm_first=True)
        self.market = nn.TransformerEncoder(layer, 2, enable_nested_tensor=False)
        self.norm = nn.LayerNorm(width)
        self.buy = nn.Linear(width, 1)
        self.control = nn.Linear(width, 2)
        self.value = nn.Linear(width, 1)
        nn.init.orthogonal_(self.buy.weight, .01)
        nn.init.orthogonal_(self.control.weight, .01)
        nn.init.zeros_(self.buy.bias)
        nn.init.zeros_(self.control.bias)
        # Sparse exploration prior: avoid thousands of fee-paying round trips
        # before reward learning has seen its first completed training session.
        with torch.no_grad():
            self.control.bias[0] = 6.

    def forward(self, features, account, holding, candidate_valid, action_mask):
        b = features.shape[0]
        x = self.frame(features.reshape(b*5, 7, 14).clamp(-20, 20))+self.timeframe
        context, _ = self.time_attention(x, x, x, need_weights=False)
        candidates = self.time_norm(x+context).mean(1).reshape(b, 5, -1)
        tokens = torch.cat((candidates, self.holding(holding)[:, None], self.account(account)[:, None]), 1)
        valid = torch.cat((candidate_valid, holding[:, :1]>0, torch.ones((b,1),dtype=torch.bool,device=x.device)),1)
        tokens = self.norm(self.market(tokens, src_key_padding_mask=~valid))
        control = self.control(tokens[:, -1])
        logits = torch.cat((control[:, :1], self.buy(tokens[:, :5]).squeeze(-1), control[:, 1:]),1)
        return Categorical(logits=logits.masked_fill(~action_mask, -1e9)), self.value(tokens[:, -1]).squeeze(-1)


def tensor_batch(observations, device='cpu'):
    import numpy as np
    return {k: torch.as_tensor(np.stack([o[k] for o in observations]), device=device)
            for k in ('features','account','holding','candidate_valid','action_mask')}
