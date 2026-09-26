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
        self.temporal_pool = nn.AdaptiveAvgPool1d(16)
        self.history_projection = nn.Linear(16*d_model,d_model)
        self.identity = nn.Embedding(tickers+2,d_model,padding_idx=0)
        self.unknown_ticker_id = tickers+1
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

    def encode(self, batch: dict[str,torch.Tensor]):
        x = batch['market']
        count,tickers,history,features = x.shape
        if tickers != self.top_n:
            raise ValueError('Policy input top-N differs from its action head')
        sequence = self.temporal(x.reshape(count*tickers,history,features).transpose(1,2))
        encoded = sequence[:,:,-1]+self.history_projection(
            self.temporal_pool(sequence).flatten(1))
        encoded = encoded.reshape(count,tickers,-1)
        encoded = encoded+self.identity(batch['ticker_id'])+self.slot_metadata(
            torch.stack((batch['rank'],batch['held']),dim=-1))
        mask = ~batch['valid']
        encoded = self.market(encoded,src_key_padding_mask=mask)
        encoded = encoded.masked_fill(mask.unsqueeze(-1),0)
        context = encoded.sum(dim=1)/batch['valid'].sum(dim=1,keepdim=True).clamp_min(1)
        raw_account = batch['account']
        account = torch.stack((torch.log1p(raw_account[:,0].clamp_min(0)),
            torch.log1p(raw_account[:,1].clamp_min(0)),raw_account[:,2]),dim=-1)
        context = context+self.account(account)
        lot_index = batch['lot_slots'].clamp_min(0)
        held = torch.gather(encoded,1,lot_index.unsqueeze(-1).expand(-1,-1,encoded.shape[-1]))
        raw_lots = batch['lots']
        lot_features = torch.stack((torch.log1p(raw_lots[:,:,0].clamp_min(0))/12,
            torch.log(raw_lots[:,:,1].clamp_min(1e-6))/8,
            raw_lots[:,:,2].clamp(0,16)/16),dim=-1)
        held = held+self.lot(lot_features)
        held = held.masked_fill((batch['lot_slots'] < 0).unsqueeze(-1),0)
        return encoded,context,held

    def action_logits(self, encoded: torch.Tensor, context: torch.Tensor,
                      held: torch.Tensor, previous: torch.Tensor, order: int):
        focus = context+previous+self.step.weight[order]
        stop = self.stop_head(focus)
        buys = self.buy_head(torch.tanh(encoded+focus.unsqueeze(1))).squeeze(-1)
        sells = self.sell_head(torch.tanh(held+focus.unsqueeze(1))).squeeze(-1)
        return torch.cat((stop,buys,sells),dim=1)

    def action_embedding(self, encoded: torch.Tensor, held: torch.Tensor,
                         token: torch.Tensor):
        buy = (token > 0) & (token <= self.top_n)
        sell = token > self.top_n
        buy_value = torch.gather(encoded,1,(token-1).clamp(0,self.top_n-1)
            .view(-1,1,1).expand(-1,1,encoded.shape[-1])).squeeze(1)
        sell_value = torch.gather(held,1,(token-self.top_n-1).clamp(0,self.max_lots-1)
            .view(-1,1,1).expand(-1,1,encoded.shape[-1])).squeeze(1)
        return torch.where(buy.unsqueeze(1),buy_value,
            torch.where(sell.unsqueeze(1),sell_value,torch.zeros_like(buy_value)))

    def forward(self, batch: dict[str,torch.Tensor], *, teacher_actions: torch.Tensor | None = None):
        encoded,context,held = self.encode(batch)
        value = self.value_head(context).squeeze(-1)
        previous = torch.zeros_like(context)
        logits = []
        for order in range(self.max_orders):
            logits.append(self.action_logits(encoded,context,held,previous,order))
            if order+1 < self.max_orders and teacher_actions is not None:
                previous = previous+self.action_embedding(encoded,held,teacher_actions[:,order])
        return torch.stack(logits,dim=1),value
