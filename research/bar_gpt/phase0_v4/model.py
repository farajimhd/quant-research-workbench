"""Small feature transformer: one bar -> 16D embedding -> the same 18 fields."""

from dataclasses import dataclass
import torch
from torch import nn
from torch.nn import functional as F
from .transforms import coordinates, decode, raw_values, TRANSFORM_VERSION


@dataclass(frozen=True)
class Config:
    """Architecture knobs; chunking bounds attention memory, not data coverage."""

    embedding: int = 16
    width: int = 32
    heads: int = 4
    layers: int = 2
    chunk_bars: int = 4096
    price_factor: float = 1000.0


class AttentionBlock(nn.Module):
    """Let each feature select useful information from the other feature tokens.

    Attention mixes information BETWEEN tokens; the feed-forward network then
    processes each token independently. Residual additions retain the incoming
    representation, so a block can learn a small correction instead of rebuilding
    everything. Pre-normalization keeps the inputs to both sublayers well-scaled.
    """

    def __init__(self, width, heads):
        super().__init__()
        self.heads = heads
        self.norm1 = nn.LayerNorm(width)
        self.qkv = nn.Linear(width, 3 * width)
        self.projection = nn.Linear(width, width)
        self.norm2 = nn.LayerNorm(width)
        self.ff = nn.Sequential(nn.Linear(width, 2 * width), nn.GELU(), nn.Linear(2 * width, width))

    def forward(self, tokens):
        """Input/output shape: (independent bars, feature tokens, model width)."""
        bars, features, width = tokens.shape
        # Q asks what a token needs, K describes what each token offers, and V
        # carries the information to combine. One projection produces all three.
        projected = self.qkv(self.norm1(tokens))
        projected = projected.reshape(bars, features, 3, self.heads, width // self.heads)
        query, key, value = projected.permute(2, 0, 3, 1, 4).unbind(0)
        # The attention axis is features, NEVER bars. Multiple heads can learn
        # different relationships (e.g. prices versus activity) in parallel.
        attended = F.scaled_dot_product_attention(query, key, value, dropout_p=0.0)
        attended = attended.transpose(1, 2).reshape(bars, features, width)
        tokens = tokens + self.projection(attended)
        return tokens + self.ff(self.norm2(tokens))


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
        if parameters.get("transform_version") != TRANSFORM_VERSION:
            raise ValueError("Incompatible calibration: refit for the trade-ratio transform")
        if (
            min(config.width, config.heads, config.layers, config.embedding, config.chunk_bars) < 1
            or config.width % config.heads
        ):
            raise ValueError("Positive dimensions and width divisible by heads required")
        self.config = config
        for name in ("mean", "scale"):
            value = torch.as_tensor(parameters[name], dtype=torch.float32).clone()
            if value.shape != (27,) or not torch.isfinite(value).all():
                raise ValueError("Calibration requires 27 finite training-only values")
            if name == "scale" and not (value > 0).all():
                raise ValueError("Calibration scales must be positive")
            self.register_buffer(name, value)
        self.scalar_token = nn.Linear(3, config.width)
        self.identity = nn.Parameter(torch.randn(27, config.width) * 0.02)
        self.summary = nn.Parameter(torch.randn(1, 1, config.width) * 0.02)
        # Each block sees only this bar. No sequence of neighboring bars enters.
        self.blocks = nn.Sequential(
            *[AttentionBlock(config.width, config.heads) for _ in range(config.layers)]
        )
        self.readout = nn.Sequential(
            nn.LayerNorm(config.width), nn.Linear(config.width, config.embedding)
        )
        self.decoder = nn.Linear(config.embedding, 18)

    def get_extra_state(self):
        """Seal coordinate semantics in checkpoints, not just their tensor shapes."""
        return dict(transform_version=TRANSFORM_VERSION)

    def set_extra_state(self, state):
        """Reject weights carrying another interpretation of the same 18 slots."""
        if state.get("transform_version") != TRANSFORM_VERSION:
            raise ValueError("Incompatible checkpoint transform")

    def encode(self, values, valid):
        """Accept (...,27) canonical logs/time fields and boolean validity flags."""
        if values.shape != valid.shape or values.shape[-1] != 27 or valid.dtype != torch.bool:
            raise ValueError("Expected matching (...,27) values and boolean masks")
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
        return raw_values(self.decode(embedding)["logs"])

    def forward(self, values, valid):
        """Reconstruct each input bar independently, preserving leading dimensions."""
        embedding = self.encode(values, valid)
        result = self.decode(embedding)
        # Returning the already-computed embedding makes collapse diagnostics
        # possible without a second encoder pass. The decoder still sees only it.
        result["embedding"] = embedding
        return result
