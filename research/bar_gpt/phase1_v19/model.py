"""Consistent-mask-scale encoders; decoder receives only learned 256D values."""
import torch
from torch import nn
from ..phase1_v16.model import Autoencoder as Previous, TemporalSlots


class CenteredSlots(TemporalSlots):
    def forward(self, visible_latents, visible_metadata, pos):
        x=torch.cat(((visible_latents-self.mean)/self.scale,visible_metadata),-1)
        with torch.autocast(x.device.type,dtype=torch.bfloat16,
                            enabled=x.is_cuda and self.config.precision=='bf16'):
            h=self.stem(x)+self.position[0,pos]
            h=self.blocks(torch.cat((self.cls.expand(len(x),-1,-1),h),1)).float()
        summary=h[:,0];tokens=h[:,1:]
        residual=tokens-tokens.mean(1,keepdim=True)
        aligned=residual.new_zeros(len(x),64,residual.shape[-1])
        aligned=aligned.scatter(1,pos[...,None].expand_as(residual),residual)
        return self.global_readout(summary)+self.readout(aligned.flatten(1))


class Autoencoder(Previous):
    def __init__(self,c,mean,scale,architecture='scaled'):
        super().__init__(c,mean,scale,'scaled_slots_price')
        if architecture not in ('scaled','unscaled','centered'):raise ValueError(architecture)
        self.architecture=architecture
        self.encoder.count_scaled=architecture=='scaled'
        if architecture=='centered':
            self.encoder.__class__=CenteredSlots
            self.encoder.global_readout=nn.Linear(c.width,c.embedding)


def architecture(contract):
    return contract.get('architecture','scaled' if contract.get('encoder_count_scaled',True) else 'unscaled')


def load_exported_encoder(path,device='cpu'):
    from ..phase1_v12.model import Config
    saved=torch.load(path,map_location='cpu',weights_only=False)
    state=saved['encoder']
    model=Autoencoder(Config(**saved['config']),state['mean'],state['scale'],saved['architecture'])
    model.encoder.load_state_dict(state)
    return model.encoder.to(device).eval()
