"""User-selected tested baseline, separate from stopped coordinate-MAE V2."""
from dataclasses import dataclass,asdict
from ..phase2_v2.config import TOKENS,HISTORY

VERSION='bargpt-phase2-unmasked-v1'

@dataclass(frozen=True)
class Config:
    width:int=512
    layers:int=1
    heads:int=8
    feedforward:int=2048
    predictor_width:int=256
    mask_rate:float=0.
    dropout:float=0.
    attention_layout:str='batch-first-sdpa'

    def __post_init__(self):
        if tuple(asdict(self).values())!=(512,1,8,2048,256,0.,0.,'batch-first-sdpa'):
            raise ValueError('Unmasked V1 requires the exact qualified baseline architecture')

    def manifest(self):
        return dict(version=VERSION,model=asdict(self),tokens=TOKENS,history=HISTORY,
            input='16 complete frozen V4 embeddings of512 features; no transpose or masking',
            order='timeframe-major oldest-to-newest',target=64,window=64,
            encoder='one preLN512 block; eight64-wide heads; FF2048 GELU; dropout0',
            readout='existing latest1s row7; no added summary/metadata/timeframe/position tokens',
            predictor='Linear512->256 GELU Linear256->512',
            augmentation=dict(removed=0,retained=512,evaluation_tokens=16,production_tokens=16),
            objective='MSE all512 coordinates of strictly future64 observed1s frozen V4 embedding',
            normalization='exact V4 local-reference policy; no extra latent centering',
            teacher='immutable SHA-pinned V4 epoch50 encoder/decoder; frozen eval',
            initialization='fresh student, AdamW and scheduler; no pilot or collapsed V2 weights')
