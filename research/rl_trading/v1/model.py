"""Ticker-identified temporal encoder and cross-market autoregressive order head."""
from __future__ import annotations

import torch
from torch import nn


class MarketPolicy(nn.Module):
    def __init__(self, *, features: int, tickers: int, top_n: int, max_lots: int,
                 max_orders: int, d_model: int = 256, layers: int = 4, heads: int = 8):
        super().__init__()
        if d_model % heads or min(features,tickers,top_n,max_lots,max_orders,layers) < 1:
            raise ValueError('Invalid market policy dimensions')
        self.top_n = top_n
        self.max_lots = max_lots
        self.max_orders = max_orders
        self.temporal = nn.Sequential(nn.Conv1d(features,d_model,3,padding=1),nn.GELU(),
            nn.Conv1d(d_model,d_model,3,padding=1),nn.GELU())
        self.identity = nn.Embedding(tickers+1,d_model,padding_idx=0)
        self.slot_metadata = nn.Linear(2,d_model)
        layer = nn.TransformerEncoderLayer(d_model,heads,4*d_model,dropout=.05,batch_first=True)
        self.market = nn.TransformerEncoder(layer,layers,enable_nested_tensor=False)
        self.account = nn.Linear(3,d_model)
        self.lot = nn.Linear(3,d_model)
        self.step = nn.Embedding(max_orders,d_model)
        self.stop_head = nn.Linear(d_model,1)
        self.buy_head = nn.Linear(d_model,1)
        self.sell_head = nn.Linear(d_model,1)
        self.value_head = nn.Sequential(nn.Linear(d_model,d_model),nn.GELU(),nn.Linear(d_model,1))

    def forward(self, batch: dict[str,torch.Tensor], *, teacher_actions: torch.Tensor | None = None):
        x = batch['market']
        count,tickers,history,features = x.shape
        if tickers != self.top_n:
            raise ValueError('Policy input top-N differs from its action head')
        encoded = self.temporal(x.reshape(count*tickers,history,features).transpose(1,2))[:,:,-1]
        encoded = encoded.reshape(count,tickers,-1)
        encoded = encoded+self.identity(batch['ticker_id'])+self.slot_metadata(
            torch.stack((batch['rank'],batch['held']),dim=-1))
        mask = ~batch['valid']
        encoded = self.market(encoded,src_key_padding_mask=mask)
        encoded = encoded.masked_fill(mask.unsqueeze(-1),0)
        context = encoded.sum(dim=1)/batch['valid'].sum(dim=1,keepdim=True).clamp_min(1)
        context = context+self.account(batch['account'])
        lot_index = batch['lot_slots'].clamp_min(0)
        held = torch.gather(encoded,1,lot_index.unsqueeze(-1).expand(-1,-1,encoded.shape[-1]))
        held = held+self.lot(batch['lots'])
        held = held.masked_fill((batch['lot_slots'] < 0).unsqueeze(-1),0)
        value = self.value_head(context).squeeze(-1)
        previous = torch.zeros_like(context)
        logits = []
        for order in range(self.max_orders):
            focus = context+previous+self.step.weight[order]
            stop = self.stop_head(focus)
            buys = self.buy_head(torch.tanh(encoded+focus.unsqueeze(1))).squeeze(-1)
            sells = self.sell_head(torch.tanh(held+focus.unsqueeze(1))).squeeze(-1)
            logits.append(torch.cat((stop,buys,sells),dim=1))
            if order+1 < self.max_orders and teacher_actions is not None:
                token = teacher_actions[:,order]
                buy = (token > 0) & (token <= self.top_n)
                sell = token > self.top_n
                buy_value = torch.gather(encoded,1,(token-1).clamp(0,self.top_n-1)
                    .view(-1,1,1).expand(-1,1,encoded.shape[-1])).squeeze(1)
                sell_value = torch.gather(held,1,(token-self.top_n-1).clamp(0,self.max_lots-1)
                    .view(-1,1,1).expand(-1,1,encoded.shape[-1])).squeeze(1)
                previous = torch.where(buy.unsqueeze(1),buy_value,
                    torch.where(sell.unsqueeze(1),sell_value,torch.zeros_like(context)))
        return torch.stack(logits,dim=1),value
