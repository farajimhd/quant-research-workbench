"""Same supervised heads; additional causal timeframe/liquidity/market branches."""
import torch
from torch import nn
from research.rl_trading.v6.transformer_search import TransformerTeacher,TransformerSpec

FIELDS=('multi','multi_present','liquidity','market')


class EnrichedTeacher(TransformerTeacher):
    def __init__(self,spec,base,extra,normalization):
        super().__init__(spec,base,extra);width=spec.width
        self.multi_project=nn.Linear(16,width)
        self.multi_position=nn.Parameter(torch.randn(120,width)*.02)
        self.multi_scale=nn.Parameter(torch.randn(3,width)*.02)
        layer=nn.TransformerEncoderLayer(width,4,2*width,dropout=0.,activation='gelu',batch_first=True,norm_first=True)
        self.multi_attention=nn.TransformerEncoder(layer,1,enable_nested_tensor=False)
        self.enriched_fuse=nn.Sequential(nn.Linear(4*width+60,width),nn.GELU(),nn.LayerNorm(width))
        for name in ('multi','liquidity','market'):
            for field in ('mean','std'):
                self.register_buffer(name+'_'+field,torch.tensor(normalization[name][field],dtype=torch.float32))

    def representation(self,raw,present,held,*,multi,multi_present,liquidity,market):
        local=super().representation(raw,present,held)
        if multi.shape!=(len(raw),3,120,16) or multi_present.shape!=multi.shape[:-1]:raise ValueError('Invalid multi-resolution shape')
        if liquidity.shape!=(len(raw),30) or market.shape!=(len(raw),30):raise ValueError('Invalid enrichment vector shape')
        normalized=(multi-self.multi_mean)/self.multi_std
        x=self.multi_project(normalized)*multi_present[...,None]+self.multi_position[None,None]+self.multi_scale[None,:,None]
        x=x.reshape(-1,120,local.shape[-1]);mask=multi_present.reshape(-1,120);safe=mask.clone();safe[:,0]=True
        causal=torch.triu(torch.ones(120,120,device=raw.device,dtype=torch.bool),1)
        encoded=self.multi_attention(x,mask=causal,src_key_padding_mask=~safe)
        pooled=(encoded*mask[...,None]).sum(1)/mask.sum(1).clamp_min(1)[:,None]
        pooled=torch.where(mask.any(1)[:,None],pooled,torch.zeros_like(pooled)).reshape(len(raw),-1)
        activity=(liquidity-self.liquidity_mean)/self.liquidity_std
        broad=(market-self.market_mean)/self.market_std
        return self.enriched_fuse(torch.cat((local,pooled,activity,broad),-1))


def build(row,base,extra,normalization):
    return EnrichedTeacher(TransformerSpec(**row['transformer']),base,extra,normalization)
