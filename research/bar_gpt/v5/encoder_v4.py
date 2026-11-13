"""V3 column encoder with an independent, small masked-column MLP readout."""

import torch
from torch import nn, Tensor
from .encoder_v3 import EncoderConfig, ColumnEncoderV3


class ColumnDecoderV4(nn.Module):
    def __init__(self, config: EncoderConfig):
        super().__init__()
        width = config.decoder_width
        self.summary = nn.Linear(config.width, width)
        self.time = nn.Linear(64 * 9 * 2, width)
        self.identity = nn.Embedding(18, width)
        self.output = nn.Sequential(
            nn.LayerNorm(width), nn.Linear(width, width), nn.GELU(),
            nn.Linear(width, 64),
        )

    def forward(self, embedding: Tensor, hidden_ids: Tensor,
                time: Tensor, time_valid: Tensor) -> Tensor:
        context = torch.cat((time, (~time_valid).to(time.dtype)), dim=-1).flatten(1)
        shared = self.summary(embedding) + self.time(context)
        return self.output(shared[:, None] + self.identity(hidden_ids))


class MaskedAutoencoderV4(nn.Module):
    def __init__(self, config: EncoderConfig = EncoderConfig()):
        super().__init__()
        self.config = config
        self.encoder = ColumnEncoderV3(config)
        self.decoder = ColumnDecoderV4(config)

    def forward(self, visible, valid, feature_ids, hidden_ids, time, time_valid):
        embedding = self.encoder(visible, valid, feature_ids, time, time_valid)
        return embedding, self.decoder(embedding, hidden_ids, time, time_valid)
