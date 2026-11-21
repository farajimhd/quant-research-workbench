import torch
from torch import nn
from torch.nn import functional as F
from ..phase1_v15.model import Autoencoder as Base
from ..phase1_v12.model import Encoder, objective


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
        if arm not in ('base', 'body', 'linear_body', 'slots_body', 'scaled_slots_body','scaled_slots_price'):
            raise ValueError('Unknown direction experiment')
        self.arm = arm
        if arm in ('slots_body','scaled_slots_body','scaled_slots_price'):
            self.encoder.__class__ = TemporalSlots
            self.encoder.count_scaled=arm.startswith('scaled_slots')
            self.encoder.readout = nn.Linear(c.width*64, c.embedding)
        if arm in ('linear_body', 'slots_body', 'scaled_slots_body','scaled_slots_price'):
            del self.decoder, self.queries
            self.decoder = nn.Linear(c.embedding, 64*16)

    def decode(self, embedding):
        if self.arm in ('linear_body', 'slots_body', 'scaled_slots_body','scaled_slots_price'):
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
        target = torch.log(raw[..., b].double()/raw[..., b+3].double().clamp_min(1e-300))
        target = torch.where(mask, target, 0).float()
        err = (code[..., b+1]-target)*10000.  # log-basis-point units
        element = F.smooth_l1_loss(err, torch.zeros_like(err), reduction='none')
        loss = loss+(torch.where(mask, element, 0)*weights).sum()/(mask*weights).sum().clamp_min(1)
    total=base+.1*loss/3
    if model.arm=='scaled_slots_price':
        price=base.new_zeros(())
        for b in (0,6,12):
            mask=source_valid[...,b+3]
            target=torch.where(mask,raw[...,b+3].double().clamp_min(1e-300).log(),0).float()
            element=F.smooth_l1_loss((code[...,b]-target)*1000.,torch.zeros_like(target),reduction='none')
            price=price+(torch.where(mask,element,0)*weights).sum()/(mask*weights).sum().clamp_min(1)
        total=total+.01*price/3
    return total, loss/3
