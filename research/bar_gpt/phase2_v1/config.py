"""Scientific geometry and model contract, separate from execution knobs."""
from dataclasses import asdict, dataclass
from ..v5.phase2_ar_shards.contract import FRAMES

VERSION = 'bargpt-phase2-latent-v1'
TOKENS = (8, 4, 2, 1, 1)
HISTORY = tuple(64 * n for n in TOKENS)
FRAME_IDS = tuple(f for f, n in enumerate(TOKENS) for _ in range(n))
POSITIONS = tuple(p for n in TOKENS for p in range(n))
METADATA = 9  # log1p start/end ages, relative price, six relative activity references


@dataclass(frozen=True)
class Config:
    width: int = 512
    layers: int = 6
    heads: int = 8
    feedforward: int = 2048
    predictor_width: int = 256
    mask_rate: float = .2
    dropout: float = 0.

    def __post_init__(self):
        if (self.width != 512 or self.layers < 1 or self.heads < 1
                or self.width % self.heads or self.feedforward < 1
                or self.predictor_width < 1 or not 0 <= self.mask_rate < 1
                or not 0 <= self.dropout < 1):
            raise ValueError('Invalid latent-v1 architecture')

    def manifest(self):
        return dict(version=VERSION, model=asdict(self), tokens=TOKENS,
                    history=HISTORY, frames=FRAMES, window=64, target=64,
                    target_support='strictly future observed 1s bars inside the same block',
                    order='timeframe-major oldest-to-newest', metadata=METADATA,
                    objective='future frozen embedding MSE only',
                    normalization='exact V4 local-reference policy',
                    teacher='immutable SHA-pinned V4 encoder; eval; no gradients')
