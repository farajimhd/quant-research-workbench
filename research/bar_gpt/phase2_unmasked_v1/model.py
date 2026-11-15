"""Exact qualified unmasked baseline with production encoder/head interfaces."""
import torch
from torch import nn
from ..v5.encoder_v3 import AttentionBlock
from .config import Config


def future_loss(predicted,target,ids):
    if predicted.shape!=target.shape or predicted.ndim!=2 or predicted.shape[1]!=512:
        raise ValueError('Expected predicted/target [B,512]')
    if ids.shape!=(512,) or ids.dtype!=torch.int64:
        raise ValueError('Unmasked loss requires all512 coordinates')
    return (predicted.float()-target.detach().float()).square().mean()


class LatentEncoder(nn.Module):
    def __init__(self,config):
        super().__init__()
        self.attention=AttentionBlock(512,8,2048,0.,'sdpa',0.)

    def forward(self,tokens,metadata=None,feature_ids=None):
        if tokens.ndim!=3 or tokens.shape[1:]!=(16,512):
            raise ValueError('Expected sixteen complete512-wide context tokens')
        return self.attention(tokens)[:,7]


class Forecaster(nn.Module):
    training_loss=staticmethod(future_loss)

    def __init__(self,config=Config()):
        super().__init__();self.config=config
        self.encoder=LatentEncoder(config)
        self.predictor=nn.Sequential(nn.Linear(512,256),nn.GELU(),nn.Linear(256,512))
        self.register_buffer('_all_ids',torch.arange(512),persistent=False)

    def draw_feature_ids(self,device):
        if self._all_ids.device!=torch.device(device):raise ValueError('Model/device mismatch')
        return self._all_ids

    def forward(self,tokens,metadata=None,mask=None):
        if mask is not None:raise ValueError('Unmasked experiment does not accept feature masks')
        state=self.encoder(tokens,None)
        return state,self.predictor(state)

    def training_prediction(self,tokens,metadata=None):
        return self(tokens,None)[1],self._all_ids

    @torch.no_grad()
    def encode_context(self,tokens,metadata=None):
        if self.training:raise ValueError('Production extraction requires eval')
        return self.encoder(tokens,None)
