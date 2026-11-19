from dataclasses import dataclass
from types import SimpleNamespace
import torch
from torch import nn
from ..phase1_v11.model import Autoencoder
from ..phase1_v11.transforms import PRICES
from ..phase2_v3.model import Config as BaseConfig,ContextEncoder,Forecaster as BaseForecaster,loss


@dataclass(frozen=True)
class Config(BaseConfig):
    decoder_design:str='identical-phase1-v11-heads'


class Phase1Decoder(nn.Module):
    """Reuse the actual V11 decoding method, with no access to input bars."""
    def __init__(self):
        super().__init__()
        self.config=SimpleNamespace(readout='split_scale',center=True)
        self.decoder=nn.Linear(256,64*54)
        self.reference_head=nn.Linear(32,9)
        self.encoder=nn.Module()
        self.encoder.register_buffer('factor',torch.tensor([100. if j in PRICES else 1. for j in range(18)]))

    def details(self,z):
        with torch.autocast(z.device.type,enabled=False):
            return Autoencoder.decode(self,z.float())

    def forward(self,z):return self.details(z)['logs']

    def initialize_phase1(self,state):
        self.decoder.load_state_dict({k.removeprefix('decoder.'):v for k,v in state.items() if k.startswith('decoder.')},strict=True)
        self.reference_head.load_state_dict({k.removeprefix('reference_head.'):v for k,v in state.items() if k.startswith('reference_head.')},strict=True)


class Forecaster(BaseForecaster):
    def __init__(self,config=Config()):
        nn.Module.__init__(self);self.config=config
        self.encoder=ContextEncoder(config);self.decoder=Phase1Decoder()
