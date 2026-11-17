"""Encoder-only artifact with learned latent coordinates and fixed train statistics."""
from dataclasses import asdict
from pathlib import Path
import torch
from .model import Encoder,Config
from .data import cache


def encoder_only(model):
    result=Encoder(model.config,model.input_mean,model.input_scale)
    result.load_state_dict({k:v for k,v in model.state_dict().items() if k in result.state_dict()},strict=True)
    return result.to(model.input_mean.device).eval()


def save(model,path,**provenance):
    path=Path(path)
    if path.exists():raise FileExistsError(path)
    encoder=encoder_only(model)
    torch.save(dict(version='v8-all-learned-encoder-only',config=asdict(model.config),state=encoder.state_dict(),provenance=provenance),path)


def load(path,device='cpu'):
    item=torch.load(path,map_location='cpu',weights_only=False)
    if item['version']!='v8-all-learned-encoder-only':raise ValueError('Wrong encoder contract')
    encoder=Encoder(Config(**item['config']),item['state']['input_mean'],item['state']['input_scale'])
    encoder.load_state_dict(item['state'],strict=True)
    return encoder.to(device).eval()
