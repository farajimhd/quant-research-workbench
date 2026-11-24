"""Preserve the 64x32 exported path while masking positions before temporal mixing."""
from dataclasses import dataclass
import torch
from torch import nn
from ..phase1_v27.model import Autoencoder as Previous, Config as PreviousConfig
from ..phase0_v7.model import Autoencoder as BarEncoder


@dataclass(frozen=True)
class Config(PreviousConfig):
    mask_min: int = 4
    mask_max: int = 8
    encoder_lr_factor: float = 0.1
    direction_weight: float = 0.05
    range_weight: float = 0.05
    ohlc_weight: float = 0.05
    mixer_chunk_windows: int = 4096


class Autoencoder(Previous):
    def __init__(self, parameters, config=Config()):
        super().__init__(parameters, config)
        if not 0 < config.mask_min <= config.mask_max < 64:
            raise ValueError("Mask must leave visible context")
        # The decoder stays frozen, but must remain differentiable in its input.
        for module in (self.stem, self.blocks, self.readout):
            module.requires_grad_(True)
        self.mask_token = nn.Parameter(torch.zeros(32))
        self.eval_mask = False

    def load_previous(self, saved):
        result = self.load_state_dict({**saved['model'], '_extra_state': saved['model_extra']}, strict=False)
        if result.unexpected_keys or result.missing_keys != ['mask_token']:
            raise ValueError(f"Incompatible mixer parent: {result}")

    def encode_with_mask(self, values, valid):
        if values.ndim != 3 or values.shape[1:] != (64,27):
            raise ValueError("Expected ordered (batch,64,27) inputs")
        embedding = BarEncoder.encode(self, values, valid)
        if self.training or self.eval_mask:
            # Independent random ranking per window keeps 4..8 original positions
            # hidden. Replacement happens before ANY temporal layer; no hidden
            # raw/time feature or computed embedding can leak to other positions.
            count = torch.randint(self.config.mask_min, self.config.mask_max+1,
                                  (len(values),1), device=values.device)
            positions = torch.rand((len(values),64), device=values.device).topk(self.config.mask_max,dim=1).indices
            hidden = torch.zeros((len(values),64),device=values.device,dtype=torch.bool)
            hidden.scatter_(1,positions,torch.arange(self.config.mask_max,device=values.device)[None,:]<count)
            embedding = torch.where(hidden[...,None], self.mask_token, embedding)
        else:
            hidden = torch.zeros(values.shape[:2], device=values.device, dtype=torch.bool)
        # Windows are independent of each other. Smaller processing chunks can
        # speed LayerNorm backward without changing the 64-bar context or the
        # optimizer batch: the complete batch loss is still reduced only once.
        contextual=torch.cat([self.mixers(chunk) for chunk in embedding.split(self.config.mixer_chunk_windows)])
        return contextual, hidden

    def encode(self, values, valid):
        return self.encode_with_mask(values, valid)[0]

    def forward(self, values, valid):
        embedding,hidden = self.encode_with_mask(values,valid)
        return dict(**self.decode(embedding), embedding=embedding, hidden=hidden)
