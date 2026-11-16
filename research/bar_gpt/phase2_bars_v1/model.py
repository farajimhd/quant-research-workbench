import torch
from torch import nn
from ..phase2_unmasked_v1.model import LatentEncoder
from .config import Config
from .targets import OUTPUTS,loss,reconstruct,unpack


class Forecaster(nn.Module):
    training_loss=staticmethod(loss)

    def __init__(self,config=Config()):
        super().__init__();self.config=config
        self.encoder=LatentEncoder(config)
        self.predictor=nn.Sequential(nn.Linear(512,256),nn.GELU(),nn.Linear(256,OUTPUTS))
        self.register_buffer('_all_ids',torch.arange(512),persistent=False)

    def forward(self,tokens,metadata=None,mask=None):
        if mask is not None:raise ValueError('Bars V1 uses complete unmasked16x512 context')
        state=self.encoder(tokens,None)
        return state,self.predictor(state)

    def training_prediction(self,tokens,metadata=None):
        return self(tokens,None)[1],self._all_ids

    @torch.no_grad()
    def encode_context(self,tokens,metadata=None):
        if self.training:raise ValueError('Extraction requires eval')
        return self.encoder(tokens,None)

    @torch.no_grad()
    def predict(self,tokens,origin_log_reference):
        if self.training:raise ValueError('Forecast inference requires eval')
        embedding,predicted=self(tokens)
        raw,refs=reconstruct(predicted,origin_log_reference)
        bars,changes=unpack(predicted)
        return dict(embedding=embedding,bars=raw.transpose(1,2),references=refs,
            normalized_bars=bars.transpose(1,2),reference_changes=changes)
