"""Coordinate tokens: packed192x16 -> width64 attention -> output512."""
import torch
from torch import nn
from torch.nn import functional as F
from .config import Config
from ..v5.encoder_v3 import AttentionBlock

class WideningAttention(nn.Module):
    def __init__(self):
        super().__init__()
        self.norm=nn.LayerNorm(16)
        self.qkv=nn.Linear(16,3*64)
        self.residual=nn.Linear(16,64)

    def forward(self,x):
        b,n,_=x.shape
        q,k,v=self.qkv(self.norm(x)).reshape(b,n,3,4,16).permute(2,0,3,1,4).unbind(0)
        attended=F.scaled_dot_product_attention(q,k,v,dropout_p=0.)
        return self.residual(x)+attended.transpose(1,2).reshape(b,n,64)

class LatentEncoder(nn.Module):
    def __init__(self,config=Config()):
        super().__init__();self.config=config
        self.coordinate_identity=nn.Embedding(512,16)
        self.stem=WideningAttention()
        self.layers=nn.ModuleList([AttentionBlock(64,4,256,0.,'sdpa',0.) for _ in range(6)])
        self.norm=nn.LayerNorm(64)
        self.readout=nn.Sequential(nn.Linear(64,512),nn.GELU())

    def forward(self,tokens,metadata=None,feature_ids=None):
        if feature_ids is None:feature_ids=torch.arange(512,device=tokens.device)
        if (tokens.ndim!=3 or tokens.shape[1]!=16 or tokens.shape[2]!=len(feature_ids)
                or feature_ids.ndim!=1 or feature_ids.dtype!=torch.int64
                or len(feature_ids) not in (192,512)):
            raise ValueError('Expected [B,16,K] with K192 or512 and original coordinate IDs')
        # Drop coordinates BEFORE transposition/stem/attention. Metadata has no path.
        x=tokens.transpose(1,2)+self.coordinate_identity(feature_ids)
        x=self.stem(x)
        for layer in self.layers:x=layer(x)
        return self.readout(self.norm(x)).mean(1)

class Forecaster(nn.Module):
    def __init__(self,config=Config()):
        super().__init__();self.config=config;self.encoder=LatentEncoder(config)
        self.predictor=nn.Sequential(nn.Linear(512,256),nn.GELU(),nn.Linear(256,512))

    def draw_feature_ids(self,device):
        return torch.randperm(512,device=device)[:192].sort().values

    def draw_mask(self,device):
        ids=self.draw_feature_ids(device)
        mask=torch.ones(512,dtype=torch.bool,device=device);mask[ids]=False
        return mask

    def forward_visible(self,tokens,metadata,ids):
        state=self.encoder(tokens.index_select(2,ids),None,ids)
        return state,self.predictor(state)

    def training_prediction(self,tokens,metadata):
        ids=self.draw_feature_ids(tokens.device)
        _,predicted=self.forward_visible(tokens,metadata,ids)
        return predicted,ids

    def forward(self,tokens,metadata=None,mask=None):
        if tokens.ndim!=3 or tokens.shape[1:]!=(16,512):
            raise ValueError('Expected [B,16,512]')
        if mask is None:
            ids=self.draw_feature_ids(tokens.device) if self.training else torch.arange(512,device=tokens.device)
        else:
            if mask.shape!=(512,) or mask.dtype!=torch.bool:raise ValueError('Expected bool512 mask')
            ids=(~mask).nonzero().flatten()
        return self.forward_visible(tokens,metadata,ids)

    @torch.no_grad()
    def encode_context(self,tokens,metadata=None):
        if self.training:raise ValueError('Production extraction requires eval')
        return self.encoder(tokens,None)


def future_loss(predicted,target,visible_ids):
    if predicted.shape!=target.shape or predicted.ndim!=2 or predicted.shape[1]!=512:
        raise ValueError('Expected predicted/target [B,512]')
    if visible_ids.shape!=(192,) or visible_ids.dtype!=torch.int64:
        raise ValueError('Masked training loss requires192 visible original IDs')
    # No dynamic nonzero readback: scatter a loss mask on device, constant denominator.
    weights=torch.ones(512,device=predicted.device,dtype=torch.float32)
    weights.scatter_(0,visible_ids,0.)
    return ((predicted.float()-target.detach().float()).square()*weights).sum()/(len(target)*320)
