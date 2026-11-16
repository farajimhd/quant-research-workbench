from dataclasses import dataclass,asdict
import torch
from torch import nn
from ..v5.encoder_v3 import AttentionBlock


@dataclass(frozen=True)
class Config:
    width:int=128
    embedding_width:int=256
    layers:int=6
    heads:int=8
    feedforward:int=512
    decoder_width:int=128
    dropout:float=0.
    visible_bars:int=16
    feature_drop:float=.075

    def __post_init__(self):
        if (self.width not in (128,256,512) or self.embedding_width not in (256,512)
            or self.layers<1 or self.heads<1 or self.width%self.heads
            or self.feedforward<1 or self.decoder_width<1 or not 1<=self.visible_bars<64
            or not 0<=self.feature_drop<=.1 or not 0<=self.dropout<1):
            raise ValueError('Invalid V5 architecture or augmentation')

    def contract(self):return dict(version='raw-bar-token-mae-v5-v1',**asdict(self),
        encoder='visible raw bars + unchanged time channels; positions; mean pool; export',
        decoder='small FP32 position-conditioned MLP; no references or skip connection',
        loss='masked source-valid raw Huber, fixed training-only inverse channel std weights; equal price/activity means',
        initialization='fresh; transformed checkpoints incompatible')


class Encoder(nn.Module):
    def __init__(self,config):
        super().__init__();self.config=config
        d=config.width
        self.values=nn.Linear(27,d);self.missing=nn.Linear(27,d,bias=False)
        self.position=nn.Embedding(64,d)
        self.blocks=nn.ModuleList([AttentionBlock(d,config.heads,config.feedforward,
            config.dropout,'sdpa',0.) for _ in range(config.layers)])
        self.norm=nn.LayerNorm(d)
        e=config.embedding_width
        self.export=nn.Identity() if d==e else nn.Sequential(nn.Linear(d,e),nn.LayerNorm(e))

    def forward(self,visible,valid,positions):
        if (visible.ndim!=3 or visible.shape[-1]!=27 or valid.shape!=visible.shape
            or valid.dtype!=torch.bool or positions.shape!=visible.shape[:2]
            or positions.dtype!=torch.int64):
            raise ValueError('V5 expects visible[B,K,27], source/augmentation masks, positions')
        tokens=self.values(torch.where(valid,visible,0))+self.missing((~valid).to(visible.dtype))
        tokens=tokens+self.position(positions)
        for block in self.blocks:tokens=block(tokens)
        with torch.autocast(device_type=visible.device.type,enabled=False):
            return self.export(self.norm(tokens.float().mean(1)))


class Decoder(nn.Module):
    def __init__(self,config):
        super().__init__();d=config.decoder_width
        self.summary=nn.Linear(config.embedding_width,d);self.positions=nn.Embedding(64,d)
        self.bars=nn.Sequential(nn.LayerNorm(d),nn.Linear(d,d),nn.GELU(),nn.Linear(d,18))

    def forward(self,embedding,positions):
        # Raw dollar outputs must not be quantized to BF16 at the readout.
        with torch.autocast(device_type=embedding.device.type,enabled=False):
            shared=self.summary(embedding.float())
            return self.bars(shared[:,None]+self.positions(positions))


class Autoencoder(nn.Module):
    def __init__(self,config=Config()):
        super().__init__();self.config=config;self.encoder=Encoder(config);self.decoder=Decoder(config)

    def forward(self,visible,valid,positions,target_positions):
        z=self.encoder(visible,valid,positions)
        return z,self.decoder(z,target_positions)

    def from_batch(self,batch):
        return self(batch.visible,batch.valid,batch.positions,batch.target_positions)

    def encode_full(self,batch):
        if batch.visible.shape[1]!=64:raise ValueError('Full V5 extraction requires all64 bars')
        return self.encoder(batch.visible,batch.valid,batch.positions)
