from dataclasses import dataclass,asdict
import torch
from torch import nn
from ..v5.encoder_v3 import AttentionBlock


@dataclass(frozen=True)
class Config:
    width:int=512
    embedding_width:int=512
    layers:int=6
    heads:int=8
    feedforward:int=2048
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

    def contract(self):return dict(version='bar-token-reference-mae-v5',**asdict(self),
        encoder='visible bars + one reference token; positioned; mean pool/norm; optional export projection',
        decoder='small position-conditioned MLP; references only through exported bottleneck',
        loss='source-valid hidden-bar balanced standardized Huber + reference Huber; equal3family means',
        initialization='fresh; V4 column-input weights are not silently migrated')


class Encoder(nn.Module):
    def __init__(self,config):
        super().__init__();self.config=config
        d=config.width
        self.values=nn.Linear(27,d);self.missing=nn.Linear(27,d,bias=False)
        self.position=nn.Embedding(64,d)
        self.references=nn.Linear(14,d)
        self.blocks=nn.ModuleList([AttentionBlock(d,config.heads,config.feedforward,
            config.dropout,'sdpa',0.) for _ in range(config.layers)])
        self.norm=nn.LayerNorm(d)
        e=config.embedding_width
        self.export=nn.Identity() if d==e else nn.Sequential(nn.Linear(d,e),nn.LayerNorm(e))

    def forward(self,visible,valid,positions,references,reference_valid):
        if (visible.ndim!=3 or visible.shape[-1]!=27 or valid.shape!=visible.shape
            or valid.dtype!=torch.bool or positions.shape!=visible.shape[:2]
            or positions.dtype!=torch.int64 or references.shape!=(len(visible),7)
            or reference_valid.shape!=references.shape or reference_valid.dtype!=torch.bool):
            raise ValueError('V5 expects visible[B,K,27], source/augmentation masks, positions and7 references')
        tokens=self.values(torch.where(valid,visible,0))+self.missing((~valid).to(visible.dtype))
        tokens=tokens+self.position(positions)
        refs=torch.cat((torch.where(reference_valid,references,0),(~reference_valid).to(references.dtype)),1)
        tokens=torch.cat((tokens,self.references(refs)[:,None]),1)
        for block in self.blocks:tokens=block(tokens)
        return self.export(self.norm(tokens.mean(1)))


class Decoder(nn.Module):
    def __init__(self,config):
        super().__init__();d=config.decoder_width
        self.summary=nn.Linear(config.embedding_width,d);self.positions=nn.Embedding(64,d)
        self.bars=nn.Sequential(nn.LayerNorm(d),nn.Linear(d,d),nn.GELU(),nn.Linear(d,18))
        self.references=nn.Sequential(nn.LayerNorm(d),nn.Linear(d,d),nn.GELU(),nn.Linear(d,7))

    def forward(self,embedding,positions):
        shared=self.summary(embedding)
        return self.bars(shared[:,None]+self.positions(positions)),self.references(shared)


class Autoencoder(nn.Module):
    def __init__(self,config=Config()):
        super().__init__();self.config=config;self.encoder=Encoder(config);self.decoder=Decoder(config)

    def forward(self,visible,valid,positions,references,reference_valid,target_positions):
        z=self.encoder(visible,valid,positions,references,reference_valid)
        bars,refs=self.decoder(z,target_positions)
        return z,bars,refs

    def from_batch(self,batch):
        return self(batch.visible,batch.valid,batch.positions,batch.reference_input,
                    batch.reference_valid,batch.target_positions)

    def encode_full(self,batch):
        if batch.visible.shape[1]!=64:raise ValueError('Full V5 extraction requires all64 bars')
        return self.encoder(batch.visible,batch.valid,batch.positions,batch.reference_input,batch.reference_valid)
