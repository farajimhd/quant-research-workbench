"""Self-contained encoder artifact: no decoder weights or target-derived inputs."""
from dataclasses import asdict
from pathlib import Path
import torch
from torch import nn
from .model import Autoencoder,Config,Transform
from ..v5.transforms_v1 import BarInputTransformV1


class EncoderOnly(nn.Module):
    def __init__(self, model):
        super().__init__()
        self.config=model.config
        self.input_width=model.input_width
        self.backbone=model.backbone
        self.export=model.export
        self.register_buffer('log_mean',model.transform.mean.clone())
        self.register_buffer('log_scale',model.transform.scale.clone())

    def forward(self,values,valid,positions):
        return Autoencoder.encode(self,values,valid,positions)

    @torch.no_grad()
    def encode_raw(self,raw,valid,clocks,frames):
        transformed=BarInputTransformV1().to(raw.device)(raw,valid,clocks,frames)
        logs=Transform.logs(raw,valid)
        market=logs.float()
        transformed.values[...,:18]=torch.where(valid,market,0)
        positions=torch.arange(64,device=raw.device).expand(len(raw),-1)
        return self(transformed.values,transformed.valid,positions)


def save_encoder(model,path,**provenance):
    path=Path(path)
    if path.exists():raise FileExistsError(path)
    module=EncoderOnly(model)
    torch.save(dict(version='bargpt-v7-encoder-only-1',config=asdict(model.config),
        state={k:v.detach().cpu() for k,v in module.state_dict().items()},provenance=provenance),path)


def load_encoder(path,device='cpu'):
    payload=torch.load(path,map_location='cpu',weights_only=False)
    if payload['version']!='bargpt-v7-encoder-only-1':raise ValueError('Unsupported export')
    # The shared constructor ensures identical encoder architecture. The returned
    # module contains no decoder and requires no decoder parameters in its artifact.
    shell=Autoencoder(Config(**payload['config']),payload['state']['log_mean'],payload['state']['log_scale'])
    encoder=EncoderOnly(shell);encoder.load_state_dict(payload['state'],strict=True)
    return encoder.to(device).eval()
