"""Connect all 64 bars without changing the frozen Phase0 information path."""
from dataclasses import dataclass
import torch
from torch import nn
from ..phase0_v7.model import Autoencoder as BarAutoencoder, Config as BarConfig


@dataclass(frozen=True)
class Config(BarConfig):
    """The inherited fields describe the frozen bar model, not mixer capacity."""
    mixer_layers: int = 4
    mixer_hidden: int = 128
    bars: int = 64


class MixerBlock(nn.Module):
    """Alternate time mixing and feature mixing, with identity initialization.

    Transposing B x 64 x 32 to B x 32 x 64 makes a dense layer see every
    chronological position. Its weights are shared across embedding channels.
    Feature mixing then combines the channels independently at each position.
    """
    def __init__(self, config):
        super().__init__()
        self.time_norm = nn.LayerNorm(config.embedding)
        self.time = nn.Sequential(nn.Linear(config.bars, config.mixer_hidden), nn.GELU(),
                                  nn.Linear(config.mixer_hidden, config.bars))
        self.feature_norm = nn.LayerNorm(config.embedding)
        self.feature = nn.Sequential(nn.Linear(config.embedding, config.mixer_hidden), nn.GELU(),
                                     nn.Linear(config.mixer_hidden, config.embedding))
        self.precision = "fp32"
        for branch in (self.time, self.feature):
            nn.init.zeros_(branch[-1].weight)
            nn.init.zeros_(branch[-1].bias)

    def forward(self, embedding):
        # Normalization affects the learned correction only. The residual keeps
        # absolute scale already encoded by Phase0; no per-window statistics are
        # needed by the decoder or exported separately.
        with torch.autocast(embedding.device.type, dtype=torch.bfloat16, enabled=self.precision == "bf16"):
            delta = self.time(self.time_norm(embedding).transpose(1, 2)).transpose(1, 2)
        embedding = embedding + delta.float()
        with torch.autocast(embedding.device.type, dtype=torch.bfloat16, enabled=self.precision == "bf16"):
            delta = self.feature(self.feature_norm(embedding))
        return embedding + delta.float()


class Autoencoder(BarAutoencoder):
    """Export 64x32 contextual embeddings; the frozen decoder sees only these.

    Phase0 is frozen before mixers are registered. Gradients pass through its
    decoder into the mixer, but no Phase0 parameter is optimized. There is no
    raw-input or original-embedding bypass around the exported representation.
    """
    def __init__(self, parameters, config=Config()):
        if config.embedding != 32 or config.bars != 64 or config.mixer_layers < 1:
            raise ValueError("This experiment requires 64 bars and 32D Phase0 embeddings")
        super().__init__(parameters, config)
        self.requires_grad_(False)
        self.mixers = nn.Sequential(*[MixerBlock(config) for _ in range(config.mixer_layers)])

    def load_phase0(self, saved):
        """Fail closed if the parent differs in shape or transform semantics."""
        expected = {k: v for k, v in self.config.__dict__.items() if k in BarConfig.__dataclass_fields__}
        actual = dict(saved["contract"]["config"])
        expected.pop("chunk_bars"); actual.pop("chunk_bars")
        if actual != expected:
            raise ValueError("Frozen Phase0 architecture differs")
        result = self.load_state_dict({**saved["model"], "_extra_state": saved["model_extra"]}, strict=False)
        if result.unexpected_keys or any(not name.startswith("mixers.") for name in result.missing_keys):
            raise ValueError(f"Invalid Phase0 initialization: {result}")

    def configure_execution(self, attention="math", precision="fp32", summary_only=True):
        """Keep the pinned Phase0 path FP32; profile mixer-only BF16 separately."""
        if precision not in ("fp32", "bf16"):
            raise ValueError("Only FP32 or BF16 mixer execution is supported")
        for block in self.mixers:
            block.precision = precision

    def encode(self, values, valid):
        if values.ndim != 3 or values.shape[1] != self.config.bars:
            raise ValueError("Expected (batch,64,27) ordered bars")
        # This is the only no-grad region. Wrapping decode in no_grad would
        # accidentally stop all learning, even though its weights are frozen.
        with torch.no_grad():
            bar_embeddings = super().encode(values, valid)
        return self.mixers(bar_embeddings)

    def decode_embeddings(self, embedding):
        """Standalone reconstruction requires only exported contextual embeddings."""
        return self.decode(embedding)
