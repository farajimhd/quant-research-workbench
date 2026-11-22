"""Feature attention, temporal attention, ONE lightweight64x18 decoder."""
import torch
from torch import nn
from ..phase1_v19.model import Autoencoder as PreviousWindow,architecture
from ..phase1_v12.model import Config,gather
from ..phase0_v3.model import Autoencoder as BarAutoencoder
from ..phase1_v11.transforms import decode as inverse_coordinates


class JointAutoencoder(nn.Module):
    def __init__(self,bar,temporal,mean,scale,weight,bias):
        super().__init__();self.bar=bar;self.temporal=temporal
        if hasattr(self.bar,'decoder'):del self.bar.decoder
        self.register_buffer('mean',mean[:18].detach().clone())
        self.register_buffer('scale',scale[:18].detach().clone())
        self.register_buffer('loss_factor',torch.tensor([1000. if j%6<4 else 1. for j in range(18)]))
        self.decoder=nn.Linear(temporal.config.embedding,64*18)
        with torch.no_grad():self.decoder.weight.copy_(weight);self.decoder.bias.copy_(bias)

    def features(self,values,valid):
        latent=self.bar.encode(values,valid)
        time=torch.where(valid[...,18:],(values[...,18:]-self.bar.mean[18:])/self.bar.scale[18:],0)
        return latent,torch.cat((time,valid.float()),-1)

    def decode(self,embedding):
        normalized=self.decoder(embedding.float()).reshape(-1,64,18)
        code=normalized*self.scale+self.mean
        constrained=torch.stack([code[...,j].clamp_min(0) if j%6 in (2,3,4,5) else code[...,j] for j in range(18)],-1)
        logs=inverse_coordinates(constrained,code.new_zeros(len(code),9),constrain=False)
        return dict(normalized=normalized,code=code,logs=logs)

    def forward(self,values,valid,pos):
        # Remove hidden bars before BOTH attention stages, saving feature work.
        latent,metadata=self.features(gather(values,pos),gather(valid,pos))
        embedding=self.temporal(latent,metadata,pos)
        return latent,embedding,self.decode(embedding)


def initialize(teacher,saved,calibration,input_calibration=None):
    bar=BarAutoencoder(dict(mean=teacher.mean.cpu(),scale=teacher.scale.cpu()),teacher.config,input_calibration)
    missing,unexpected=bar.load_state_dict(teacher.state_dict(),strict=False)
    if unexpected or set(missing)!={'activity_indices','input_reference','activity_input_mean','activity_input_scale'}:raise ValueError('Bar parent mismatch')
    window=PreviousWindow(Config(**saved['contract']['config']),calibration['mean'],calibration['scale'],architecture(saved['contract']))
    window.load_state_dict(saved['model'])
    # Compose existing affine maps in FP64, then store ONE FP32 head. No
    # intermediate latent decoder survives in the model or checkpoint.
    w=window.decoder.weight.detach().double().reshape(64,16,-1)
    b=window.decoder.bias.detach().double().reshape(64,16)
    if hasattr(window,'count_head'):
        w=w+window.count_head.weight.detach().double()[:,None,:]*window.count_basis.double()[None,:,None]
        b=b+window.count_head.bias.detach().double()[:,None]*window.count_basis.double()
    d=teacher.decoder.weight.detach().cpu().double();db=teacher.decoder.bias.detach().cpu().double()
    latent_scale=window.encoder.scale.double();latent_mean=window.encoder.mean.double()
    weight=torch.einsum('ij,bjk->bik',d,w*latent_scale[None,:,None]).reshape(64*18,-1).float()
    bias=(torch.einsum('ij,bj->bi',d,b*latent_scale+latent_mean)+db).flatten().float()
    return JointAutoencoder(bar,window.encoder,teacher.mean.cpu(),teacher.scale.cpu(),weight,bias)
