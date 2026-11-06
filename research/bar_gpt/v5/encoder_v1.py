"""Feature-column masked autoencoder; no loading, scaling, or optimizer logic.

Read ColumnEncoderV1.forward first: a token is one feature across 64 bars.
Only visible market columns enter encoder attention. A single summary embedding
is the decoder's only market-information path; there are no bar/column skip paths.
"""

from dataclasses import dataclass

import torch
from torch import Tensor, nn


@dataclass(frozen=True)
class EncoderConfig:
    """Architecture dimensions; defaults are hypotheses to evaluate, not tuned results."""

    width: int = 256
    layers: int = 4
    heads: int = 8
    feedforward: int = 1024
    decoder_width: int = 128
    decoder_layers: int = 2
    decoder_heads: int = 4
    dropout: float = 0.1


def blocks(width: int, heads: int, feedforward: int, depth: int, dropout: float) -> nn.ModuleList:
    """Build independent pre-LayerNorm bidirectional blocks, each [B,L,D] -> [B,L,D]."""
    if min(width, heads, feedforward, depth) < 1 or width % heads:
        raise ValueError("Positive dimensions and width divisible by heads are required.")
    return nn.ModuleList([
        nn.TransformerEncoderLayer(
            width, heads, feedforward, dropout, activation="gelu",
            batch_first=True, norm_first=True,
        ) for _ in range(depth)
    ])  # Construct separately so layers do not start as identical deep copies.


class ColumnEncoderV1(nn.Module):
    """Compress visible columns and complete UTC context into one embedding [B,D].

    Feature IDs 0..20 follow shards.contract.CHANNELS. Temporal order is retained
    by the 64 ordered inputs to each projection, rather than attention over bars.
    All nine time features stay visible as ONE context token, plus missingness.
    At inference supply all 21 columns; training supplies only 8 or 9 columns.
    """

    def __init__(self, config: EncoderConfig = EncoderConfig()):
        super().__init__()
        self.config = config
        self.values = nn.Linear(64, config.width)  # One column [B,K,64] -> [B,K,D].
        self.missing = nn.Linear(64, config.width, bias=False)  # Missing != observed zero.
        self.identity = nn.Embedding(21, config.width)  # Column IDs [B,K] -> [B,K,D].
        self.time = nn.Linear(64 * 9 * 2, config.width)  # Values + masks -> one context token.
        self.summary = nn.Parameter(torch.zeros(1, 1, config.width))  # Shared learned readout query.
        nn.init.normal_(self.summary, std=0.02)
        self.blocks = blocks(config.width, config.heads, config.feedforward, config.layers, config.dropout)
        self.norm = nn.LayerNorm(config.width)  # Normalize only the exported representation.

    def forward(self, visible: Tensor, valid: Tensor, feature_ids: Tensor,
                time: Tensor, time_valid: Tensor) -> Tensor:
        """Encode scaled [B,K,64], bool [B,K,64], int64 IDs [B,K], time [B,64,9].

        All tensors are on the model device. Inputs are prepared and checked by
        prepare_batch; missing entries are zero. Time validity has shape [B,64,9].
        No hidden-column placeholders or attention padding are allocated here.
        """
        hidden = self.values(visible) + self.missing((~valid).to(visible.dtype))
        hidden = hidden + self.identity(feature_ids)  # [B,K,D], retain feature meaning after shuffling.
        context = torch.cat((time, (~time_valid).to(time.dtype)), dim=-1).flatten(1)
        context = self.time(context).unsqueeze(1)  # [B,64*18] -> [B,1,D].
        hidden = torch.cat((self.summary.expand(len(visible), -1, -1), context, hidden), dim=1)
        # Attention sees K+2 tokens (10 or 11 during training, 23 with all columns).
        for block in self.blocks:
            hidden = block(hidden)  # [B,K+2,D], full bidirectional attention inside the supplied window.
        return self.norm(hidden[:, 0])  # Summary only: [B,K+2,D] -> [B,D].


class ColumnDecoderV1(nn.Module):
    """Reconstruct requested hidden columns from one summary and known time context.

    Uses a small bidirectional reconstruction network, with the embedding as one
    memory token and hidden feature IDs as queries. No encoder column states enter.
    Output is [B,M,64] in scaled coordinates; halt binary channels are logits.
    """

    def __init__(self, config: EncoderConfig = EncoderConfig()):
        super().__init__()
        width = config.decoder_width
        self.summary = nn.Linear(config.width, width)  # Bottleneck [B,D] -> [B,d].
        self.time = nn.Linear(64 * 9 * 2, width)  # Known target timing, not market evidence.
        self.identity = nn.Embedding(21, width)  # Requested column queries [B,M] -> [B,M,d].
        self.blocks = blocks(width, config.decoder_heads, width * 4, config.decoder_layers, config.dropout)
        self.output = nn.Sequential(nn.LayerNorm(width), nn.Linear(width, 64))

    def forward(self, embedding: Tensor, hidden_ids: Tensor, time: Tensor, time_valid: Tensor) -> Tensor:
        """Map [B,D] and IDs [B,M], UTC context/masks [B,64,9] to predictions [B,M,64]."""
        context = torch.cat((time, (~time_valid).to(time.dtype)), dim=-1).flatten(1)
        queries = self.identity(hidden_ids) + self.time(context).unsqueeze(1)  # [B,M,d].
        hidden = torch.cat((self.summary(embedding).unsqueeze(1), queries), dim=1)  # [B,M+1,d].
        for block in self.blocks:
            hidden = block(hidden)
        return self.output(hidden[:, 1:])  # Discard summary token; reconstruct M requested columns.


class MaskedAutoencoderV1(nn.Module):
    """Compose the standalone encoder and disposable training decoder."""

    def __init__(self, config: EncoderConfig = EncoderConfig()):
        super().__init__()
        self.config = config
        self.encoder = ColumnEncoderV1(config)
        self.decoder = ColumnDecoderV1(config)

    def forward(self, visible: Tensor, valid: Tensor, feature_ids: Tensor,
                hidden_ids: Tensor, time: Tensor, time_valid: Tensor) -> tuple[Tensor, Tensor]:
        """Return embedding [B,D] and hidden reconstruction [B,M,64]; no targets are inputs."""
        embedding = self.encoder(visible, valid, feature_ids, time, time_valid)
        return embedding, self.decoder(embedding, hidden_ids, time, time_valid)
