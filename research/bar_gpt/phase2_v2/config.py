"""Approved coordinate-token MAE-predictive v2 scientific contract."""
from dataclasses import asdict, dataclass
from ..v5.phase2_ar_shards.contract import FRAMES
VERSION = 'bargpt-phase2-coordinate-mae-v2'
TOKENS = (8, 4, 2, 1, 1)
HISTORY = tuple(64*n for n in TOKENS)
FRAME_IDS = tuple(f for f,n in enumerate(TOKENS) for _ in range(n))
POSITIONS = tuple(p for n in TOKENS for p in range(n))
METADATA = 9  # Preserved loader evidence; deliberately NOT a v2 model input.

@dataclass(frozen=True)
class Config:
    width: int = 64
    layers: int = 6
    heads: int = 4
    feedforward: int = 256
    predictor_width: int = 256
    mask_rate: float = 320/512
    dropout: float = 0.
    attention_layout: str = 'batch-first-sdpa'

    def __post_init__(self):
        if (self.width,self.layers,self.heads,self.feedforward,self.predictor_width,
            self.mask_rate,self.dropout) != (64,6,4,256,256,320/512,0.):
            raise ValueError('V2 requires the exact approved architecture')
        if self.attention_layout != 'batch-first-sdpa':
            raise ValueError('V2 uses batch-first SDPA')

    def manifest(self):
        return dict(version=VERSION,model=asdict(self),tokens=TOKENS,history=HISTORY,
            frames=FRAMES,window=64,target=64,order='timeframe-major oldest-to-newest',
            input='16x512 frozen windows transposed to512x16 coordinate tokens',
            identity='learned512x16 original coordinate-ID encoding',
            stem='preLN16; QKV16->3x64; four16-wide heads; concat64; residual16->64',
            encoder='six preLN64 blocks; four16-wide heads; FF256 GELU; dropout0',
            readout='LayerNorm64; per-token Linear64->512 GELU; mean across visible tokens',
            summary='output512 only; no input summary/timeframe/metadata tokens',
            augmentation=dict(axis='coordinate tokens',removed=320,retained=192,
                draw='fresh random batch-shared subset; original IDs retained',
                evaluation_tokens=512,production_tokens=512),
            objective='future frozen512 embedding; MSE ONLY320 dropped coordinate IDs',
            target_support='strictly future64 observed1s bars inside same block',
            normalization='exact V4 local-reference policy',
            teacher='immutable SHA-pinned V4 epoch50 encoder/decoder; frozen eval')
