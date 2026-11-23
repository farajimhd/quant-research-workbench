"""Small feature transformer: one bar -> 16D embedding -> the same 18 fields."""
from dataclasses import dataclass
import torch
from torch import nn
from ..phase0_v2.model import AttentionBlock
from .transforms import coordinates, decode, raw_values


@dataclass(frozen=True)
class Config:
    """Architecture knobs; chunking bounds attention memory, not data coverage."""
    embedding: int = 16
    width: int = 32
    heads: int = 4
    layers: int = 2
    chunk_bars: int = 4096
    price_factor: float = 1000.


class Autoencoder(nn.Module):
    """Attend across features inside each bar; never attend across bars.

Each of 27 feature tokens contains its standardized scalar, coordinate-valid
flag and original source-valid flag, plus a learned feature identity. A learned
summary token gathers information through attention. Only its projected 16D
embedding reaches the decoder. Time fields are context, not reconstruction
targets. Frozen training-only calibration is part of the model state.
"""
    def __init__(self, parameters, config=Config()):
        super().__init__()
        if (min(config.width, config.heads, config.layers, config.embedding,
                config.chunk_bars) < 1 or config.width % config.heads):
            raise ValueError('Positive dimensions and width divisible by heads required')
        self.config = config
        for name in ('mean', 'scale'):
            value = torch.as_tensor(parameters[name], dtype=torch.float32).clone()
            if value.shape != (27,) or not torch.isfinite(value).all():
                raise ValueError('Calibration requires 27 finite training-only values')
            if name == 'scale' and not (value > 0).all():
                raise ValueError('Calibration scales must be positive')
            self.register_buffer(name, value)
        self.scalar_token = nn.Linear(3, config.width)
        self.identity = nn.Parameter(torch.randn(27, config.width) * .02)
        self.summary = nn.Parameter(torch.randn(1, 1, config.width) * .02)
        # Reuse the qualified pre-norm attention primitive, with zero dropout.
        self.blocks = nn.Sequential(*[
            AttentionBlock(config.width, config.heads) for _ in range(config.layers)
        ])
        self.readout = nn.Sequential(nn.LayerNorm(config.width),
                                     nn.Linear(config.width, config.embedding))
        self.decoder = nn.Linear(config.embedding, 18)

    def encode(self, values, valid):
        """Accept (...,27) canonical logs/time fields and boolean validity flags."""
        if values.shape != valid.shape or values.shape[-1] != 27 or valid.dtype != torch.bool:
            raise ValueError('Expected matching (...,27) values and boolean masks')
        code, coordinate_valid = coordinates(values[..., :18], valid[..., :18])
        features = torch.cat((code, values[..., 18:]), -1)
        mask = torch.cat((coordinate_valid, valid[..., 18:]), -1)
        # Missing sentinels (including NaN) never enter a learned projection.
        safe = torch.where(mask, features, self.mean)
        scaled = (safe - self.mean) / self.scale
        inputs = torch.stack((scaled, mask.float(), valid.float()), -1)
        shape = values.shape[:-1]
        flat = inputs.reshape(-1, 27, 3)
        outputs = []
        for chunk in flat.split(self.config.chunk_bars):
            tokens = self.scalar_token(chunk) + self.identity
            tokens = torch.cat((self.summary.expand(len(chunk), -1, -1), tokens), 1)
            outputs.append(self.readout(self.blocks(tokens)[:, 0]))
        return torch.cat(outputs).reshape(*shape, self.config.embedding)

    def decode(self, embedding):
        """FP32 affine readout and inverse; no inputs other than the embedding.

The fixed calibration buffers are training-population constants, not per-bar
references. Disable autocast here so small residuals are not rounded to BF16.
"""
        with torch.autocast(device_type=embedding.device.type, enabled=False):
            normalized = self.decoder(embedding.float())
            code = normalized * self.scale[:18] + self.mean[:18]
            logs = decode(code)
        return dict(normalized=normalized, code=code, logs=logs)

    def reconstruct_raw(self, embedding):
        """Standalone decoder-to-native-units path, with no target arguments."""
        return raw_values(self.decode(embedding)['logs'])

    def forward(self, values, valid):
        """Reconstruct each input bar independently, preserving leading dimensions."""
        return self.decode(self.encode(values, valid))
