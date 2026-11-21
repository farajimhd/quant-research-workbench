import torch
from torch import nn
from torch.nn import functional as F
from ..phase1_v15.model import Autoencoder as Base
from ..phase1_v12.model import Encoder, objective
from ..phase1_v15.model import CountAttention


class TemporalSlots(Encoder):
    """Learn one 256D projection of position-aligned visible token states."""
    def forward(self, visible_latents, visible_metadata, pos):
        x = torch.cat(((visible_latents-self.mean)/self.scale, visible_metadata), -1)
        with torch.autocast(x.device.type, dtype=torch.bfloat16,
                            enabled=x.is_cuda and self.config.precision == 'bf16'):
            h = self.stem(x)+self.position[0, pos]
            h = self.blocks(torch.cat((self.cls.expand(len(x), -1, -1), h), 1))[:, 1:]
        aligned=h.new_zeros(len(h),64,h.shape[-1],dtype=torch.float32)
        aligned=aligned.scatter(1,pos[...,None].expand_as(h),h.float())
        if getattr(self,'count_scaled',False):aligned=aligned*(64/pos.shape[1])
        return self.readout(aligned.flatten(1))


class Autoencoder(Base):
    def __init__(self, c, mean, scale, arm='body'):
        super().__init__(c, mean, scale, 'stable_balanced')
        if arm not in ('base', 'body', 'linear_body', 'slots_body', 'scaled_slots_body','scaled_slots_price','scaled_slots_price_strong'):
            raise ValueError('Unknown direction experiment')
        self.arm = arm
        if arm in ('slots_body','scaled_slots_body','scaled_slots_price','scaled_slots_price_strong'):
            self.encoder.__class__ = TemporalSlots
            self.encoder.count_scaled=arm.startswith('scaled_slots')
            self.encoder.readout = nn.Linear(c.width*64, c.embedding)
        if arm in ('linear_body', 'slots_body', 'scaled_slots_body','scaled_slots_price','scaled_slots_price_strong'):
            del self.decoder, self.queries
            self.decoder = nn.Linear(c.embedding, 64*16)

    def decode(self, embedding):
        if self.arm in ('linear_body', 'slots_body', 'scaled_slots_body','scaled_slots_price','scaled_slots_price_strong'):
            return self.decoder(embedding.float()).reshape(-1, 64, 16)
        return super().decode(embedding)


def direction_objective(model, teacher, pred, latent, valid, pos, raw, source_valid):
    base, _ = objective(model, pred, latent, valid, pos)
    if model.arm == 'base':
        return base, base.new_zeros(())
    # Phase0's linear decoder maps the reconstructed latent to log(O/C).
    # This avoids subtracting two large FP32 log-price outputs. References are
    # never passed into either decoder; raw targets are used only in the loss.
    code = teacher.decoder(model.denormalize(pred).float())*teacher.scale[:18]+teacher.mean[:18]
    loss = base.new_zeros(())
    known = torch.zeros_like(valid).scatter(1, pos, True)
    weights = torch.where(known, 1., .1) if pos.shape[1] < 64 else torch.ones_like(valid, dtype=torch.float32)
    for b in (0, 6, 12):
        mask = source_valid[..., b] & source_valid[..., b+3]
        target = torch.log(torch.where(mask,raw[..., b].double(),1)/torch.where(mask,raw[..., b+3].double(),1)).float()
        err = (code[..., b+1]-target)*10000.  # log-basis-point units
        element = F.smooth_l1_loss(err, torch.zeros_like(err), reduction='none')
        loss = loss+(torch.where(mask, element, 0)*weights).sum()/(mask*weights).sum().clamp_min(1)
    total=base+.1*loss/3
    if model.arm in ('scaled_slots_price','scaled_slots_price_strong'):
        price=base.new_zeros(())
        for b in (0,6,12):
            mask=source_valid[...,b+3]
            target=torch.where(mask,raw[...,b+3].double(),1).log().float()
            element=F.smooth_l1_loss((code[...,b]-target)*1000.,torch.zeros_like(target),reduction='none')
            price=price+(torch.where(mask,element,0)*weights).sum()/(mask*weights).sum().clamp_min(1)
        total=total+(.1 if model.arm=='scaled_slots_price_strong' else .01)*price/3
    return total, loss/3


def load_exported_encoder(path,device='cpu'):
    """Construct only the learned encoder; no decoder or per-example extras."""
    from ..phase1_v12.model import Config
    saved=torch.load(path,map_location='cpu',weights_only=False)
    c=Config(**saved['config']);state=saved['encoder'];arm=saved['arm']
    if arm not in ('base','body','linear_body','slots_body','scaled_slots_body','scaled_slots_price','scaled_slots_price_strong'):raise ValueError('Unknown exported architecture')
    slots=arm in ('slots_body','scaled_slots_body','scaled_slots_price','scaled_slots_price_strong')
    encoder=(TemporalSlots if slots else Encoder)(c,state['mean'],state['scale'])
    if slots:
        encoder.count_scaled=arm.startswith('scaled_slots');encoder.readout=nn.Linear(c.width*64,c.embedding)
    for i in range(c.layers):encoder.blocks[i]=CountAttention(c.width,c.heads,stable=True,balanced=True)
    encoder.load_state_dict(state);return encoder.to(device).eval()
