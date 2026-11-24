"""Residual MLP with V4 transforms: one bar -> 32D embedding -> the same 18 fields."""

from dataclasses import dataclass
import torch
from torch import nn
from torch.nn import functional as F
from .transforms import coordinates, decode, raw_values, TRANSFORM_VERSION
from .eligibility import model_validity


@dataclass(frozen=True)
class Config:
    """MLP dimensions; heads is retained only for shared launcher compatibility."""

    embedding: int = 32
    width: int = 128
    heads: int = 4
    layers: int = 2
    chunk_bars: int = 4096
    price_factor: float = 1000.0


class ResidualBlock(nn.Module):
    """Learn a correction to each bar vector, without feature-token attention."""

    def __init__(self, width):
        super().__init__()
        self.norm = nn.LayerNorm(width)
        self.ff = nn.Sequential(nn.Linear(width, 2 * width), nn.GELU(), nn.Linear(2 * width, width))
        self.compute_precision = "fp32"

    def forward(self, values):
        # Keep residual accumulation and normalization in FP32. Optional BF16
        # affects only the dense hidden layers; the decoder remains FP32.
        normalized = self.norm(values)
        dtype = torch.bfloat16 if self.compute_precision == "bf16" else torch.float16
        with torch.autocast(values.device.type, dtype=dtype, enabled=self.compute_precision != "fp32"):
            correction = self.ff(normalized)
        return values + correction.float()


class Autoencoder(nn.Module):
    """One independent bar enters as 27 triples, flattened to 81 values.

    Each triple retains the exact V4 scalar and both validity flags. Residual
    dense layers mix features without allocating an attention matrix per bar.
    Only the exported 32D embedding reaches the 18-output affine decoder.
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
        self.register_buffer("geometry_mask", torch.tensor([j < 18 and j % 6 in (1, 2, 3) for j in range(27)]))
        for name in ("mean", "scale"):
            value = torch.as_tensor(parameters[name], dtype=torch.float32).clone()
            if value.shape != (27,) or not torch.isfinite(value).all():
                raise ValueError("Calibration requires 27 finite training-only values")
            if name == "scale" and not (value > 0).all():
                raise ValueError("Calibration scales must be positive")
            self.register_buffer(name, value)
        self.stem = nn.Sequential(nn.Linear(81, config.width), nn.GELU())
        self.blocks = nn.Sequential(*[ResidualBlock(config.width) for _ in range(config.layers)])
        self.readout = nn.Linear(config.width, config.embedding)
        self.decoder = nn.Linear(config.embedding, 18)

    def get_extra_state(self):
        """Seal coordinate semantics in checkpoints, not just their tensor shapes."""
        return dict(transform_version=TRANSFORM_VERSION)

    def configure_execution(self, attention="sdpa", precision="fp32", summary_only=False):
        """Set hidden-layer precision; attention arguments are profiler compatibility.

        There is no attention or summary query in this model. These two arguments
        are ignored deliberately so the existing bounded profiler can be reused.
        """
        if attention not in ("sdpa", "math") or precision not in ("fp32", "bf16", "fp16"):
            raise ValueError("Unknown execution mode")
        for block in self.blocks:
            block.compute_precision = precision

    def set_extra_state(self, state):
        """Reject weights carrying another interpretation of the same 18 slots."""
        if state.get("transform_version") != TRANSFORM_VERSION:
            raise ValueError("Incompatible checkpoint transform")

    def encode(self, values, valid):
        """Accept (...,27) canonical logs/time fields and boolean validity flags."""
        if values.shape != valid.shape or values.shape[-1] != 27 or valid.dtype != torch.bool:
            raise ValueError("Expected matching (...,27) values and boolean masks")
        valid = model_validity(values, valid)
        code, coordinate_valid = coordinates(values[..., :18], valid[..., :18])
        features = torch.cat((code, values[..., 18:]), -1)
        mask = torch.cat((coordinate_valid, valid[..., 18:]), -1)
        # Missing sentinels (including NaN) never enter a learned projection.
        safe = torch.where(mask, features, self.mean)
        scaled = (safe - self.mean) / self.scale
        # Reversible compression contains rare huge wicks without deleting their
        # information. Decoder targets retain their original coordinate units.
        scaled = torch.where(self.geometry_mask, torch.asinh(scaled), scaled)
        inputs = torch.stack((scaled, mask.float(), valid.float()), -1)
        shape = values.shape[:-1]
        flat = inputs.reshape(-1, 81)
        outputs = []
        for chunk in flat.split(self.config.chunk_bars):
            outputs.append(self.readout(self.blocks(self.stem(chunk))))
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
