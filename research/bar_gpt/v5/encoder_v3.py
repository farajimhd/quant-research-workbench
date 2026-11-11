"""Feature-column masked autoencoder; no loading, scaling, or optimizer logic.

Read ColumnEncoderV3.forward first: a token is one feature across 64 bars.
Only visible market columns enter encoder attention. A single summary embedding
is the decoder's only market-information path; there are no bar/column skip paths.
"""

from dataclasses import dataclass
from functools import lru_cache

import torch
from torch import Tensor, nn
from torch.nn import functional as F


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
    attention: str = "sdpa"
    attention_dropout: float | None = (
        None  # None preserves the original attention dropout.
    )
    architecture: str = (
        "column-latest-bar-local-reference-v3"  # Bind checkpoints to the mandatory latest-bar path.
    )
    attention_layout: str = "batch-first-sdpa-v3"  # Bind profiled execution/RNG layout.


@lru_cache(maxsize=1)
def compiled_flex():
    """Compile lazily once; an unavailable compiler fails explicitly rather than falling back."""
    from torch.nn.attention.flex_attention import flex_attention

    return torch.compile(flex_attention, dynamic=False)


class AttentionBlock(nn.TransformerEncoderLayer):
    """Preserve PyTorch block parameters while exposing an optional compiled Flex backend."""

    def __init__(self, width, heads, feedforward, dropout, backend, attention_dropout):
        super().__init__(
            width,
            heads,
            feedforward,
            dropout,
            activation="gelu",
            batch_first=True,
            norm_first=True,
        )
        if backend not in {"sdpa", "flex"}:
            raise ValueError("Attention backend must be sdpa or flex.")
        self.backend = backend
        self.self_attn.dropout = (
            dropout if attention_dropout is None else attention_dropout
        )
        if backend == "flex" and self.self_attn.dropout != 0:
            raise ValueError(
                "FlexAttention requires explicit attention_dropout=0; residual dropout is unchanged."
            )

    def forward(self, hidden: Tensor) -> Tensor:
        """Full bidirectional attention over packed [B,L,D]; no dense masking placeholders."""
        # Avoid MultiheadAttention's sequence-first transpose/contiguous copies.
        normalized = self.norm1(hidden)
        batch, length, width = normalized.shape
        heads = self.self_attn.num_heads
        qkv = F.linear(
            normalized, self.self_attn.in_proj_weight, self.self_attn.in_proj_bias
        )
        query, key, value = (
            qkv.reshape(batch, length, 3, heads, width // heads)
            .permute(2, 0, 3, 1, 4)
            .unbind(0)
        )
        if self.backend == "sdpa":
            attended = F.scaled_dot_product_attention(
                query,
                key,
                value,
                dropout_p=self.self_attn.dropout if self.training else 0.0,
            )
        else:
            attended = compiled_flex()(query, key, value)
        attended = attended.transpose(1, 2).reshape(batch, length, width)
        hidden = hidden + self.dropout1(self.self_attn.out_proj(attended))
        update = self.linear2(
            self.dropout(self.activation(self.linear1(self.norm2(hidden))))
        )
        return hidden + self.dropout2(update)


def blocks(
    width: int,
    heads: int,
    feedforward: int,
    depth: int,
    dropout: float,
    backend="sdpa",
    attention_dropout=None,
) -> nn.ModuleList:
    """Build independent pre-LayerNorm bidirectional blocks, each [B,L,D] -> [B,L,D]."""
    if min(width, heads, feedforward, depth) < 1 or width % heads:
        raise ValueError(
            "Positive dimensions and width divisible by heads are required."
        )
    return nn.ModuleList(
        [
            AttentionBlock(
                width, heads, feedforward, dropout, backend, attention_dropout
            )
            for _ in range(depth)
        ]
    )  # Construct separately so layers do not start as identical deep copies.


class LatestBarToken(nn.Module):
    """Pool the latest available values of supplied columns into one token [B,1,D].

    The caller passes only the K visible feature columns, never a full raw market
    tensor or reconstruction targets. Semantic identities distinguish, for example,
    a trade close from an ask size. A nonlinear per-feature projection preserves
    that interaction before permutation-invariant mean pooling. The mean keeps
    the token scale comparable between K=7/8 training and K=18 inference.
    """

    def __init__(self, width: int):
        super().__init__()
        self.values = nn.Linear(
            2, width
        )  # Latest scaled value + missingness: [B,K,2] -> [B,K,D].
        self.output = nn.Linear(
            width, width
        )  # Pooled endpoint representation [B,1,D] -> [B,1,D].
        self.norm = nn.LayerNorm(
            width
        )  # Stabilize the additional token before encoder attention.
        self.role = nn.Parameter(
            torch.empty(1, 1, width)
        )  # Explicitly identify this as the latest-bar token.
        nn.init.normal_(self.role, std=0.02)

    def forward(self, visible: Tensor, valid: Tensor, identities: Tensor) -> Tensor:
        """Read scaled values/bool masks [B,K,64] and identity embeddings [B,K,D].

        All inputs are on the model device. Index -1 is the last completed bar t.
        Invalid source values contribute only their missingness and identity.
        Earlier bars and withheld feature columns have no input path here.
        """
        if visible.shape[1] == 0:
            raise ValueError(
                "The latest-bar token requires at least one supplied feature column."
            )
        observed = valid[..., -1]  # [B,K,64] -> [B,K], evidence at t only.
        values = torch.where(
            observed, visible[..., -1], 0
        )  # [B,K]; absent evidence cannot inject a placeholder value.
        endpoint = torch.stack(
            (values, (~observed).to(visible.dtype)), dim=-1
        )  # [B,K] -> [B,K,2].
        features = F.gelu(
            self.values(endpoint) + identities
        )  # [B,K,2] -> [B,K,D], condition on feature meaning.
        pooled = features.mean(
            dim=1, keepdim=True
        )  # [B,K,D] -> [B,1,D], independent of supplied-column ordering.
        return (
            self.norm(self.output(pooled)) + self.role
        )  # [B,1,D], a single endpoint token with a learned role.


class ColumnEncoderV3(nn.Module):
    """Compress visible columns and complete UTC context into one embedding [B,D].

    Feature IDs 0..17 follow shards.phase1_contract.CHANNELS. Temporal order is retained
    by the 64 ordered inputs to each projection, rather than attention over bars.
    All nine time features stay visible as ONE context token, plus missingness.
    A separate latest-bar token pools only supplied columns at index 63.
    At inference supply all 18 columns; training supplies only 7 or 8 columns.
    """

    def __init__(self, config: EncoderConfig = EncoderConfig()):
        super().__init__()
        if config.architecture != "column-latest-bar-local-reference-v3":
            raise ValueError("Unsupported encoder architecture revision.")
        if config.attention_layout != "batch-first-sdpa-v3":
            raise ValueError("Unsupported v3 attention execution layout")
        self.config = config
        self.values = nn.Linear(64, config.width)  # One column [B,K,64] -> [B,K,D].
        self.missing = nn.Linear(
            64, config.width, bias=False
        )  # Missing != observed zero.
        self.identity = nn.Embedding(18, config.width)  # Column IDs [B,K] -> [B,K,D].
        self.latest_bar = LatestBarToken(
            config.width
        )  # Visible endpoint values [B,K,64] -> one [B,1,D] token.
        self.time = nn.Linear(
            64 * 9 * 2, config.width
        )  # Values + masks -> one context token.
        self.summary = nn.Parameter(
            torch.zeros(1, 1, config.width)
        )  # Shared learned readout query.
        nn.init.normal_(self.summary, std=0.02)
        self.blocks = blocks(
            config.width,
            config.heads,
            config.feedforward,
            config.layers,
            config.dropout,
            config.attention,
            config.attention_dropout,
        )
        self.norm = nn.LayerNorm(
            config.width
        )  # Normalize only the exported representation.

    def forward(
        self,
        visible: Tensor,
        valid: Tensor,
        feature_ids: Tensor,
        time: Tensor,
        time_valid: Tensor,
    ) -> Tensor:
        """Encode scaled [B,K,64], bool [B,K,64], int64 IDs [B,K], time [B,64,9].

        All tensors are on the model device. Inputs are prepared and checked by
        prepare_batch; missing entries are zero. Time validity has shape [B,64,9].
        No hidden-column placeholders or attention padding are allocated here.
        """
        identities = self.identity(
            feature_ids
        )  # IDs [B,K] -> [B,K,D], shared semantic identities for both input paths.
        latest = self.latest_bar(
            visible, valid, identities
        )  # [B,K,64] -> [B,1,D]; hidden columns are never supplied.
        hidden = self.values(visible) + self.missing((~valid).to(visible.dtype))
        hidden = hidden + identities  # [B,K,D], retain feature meaning after shuffling.
        context = torch.cat((time, (~time_valid).to(time.dtype)), dim=-1).flatten(1)
        context = self.time(context).unsqueeze(1)  # [B,64*18] -> [B,1,D].
        hidden = torch.cat(
            (self.summary.expand(len(visible), -1, -1), context, latest, hidden), dim=1
        )
        # [summary, time, latest bar, columns]: K+3 tokens, 10/11 in training or 21 with all columns.
        for block in self.blocks:
            hidden = block(
                hidden
            )  # [B,K+3,D], full bidirectional attention inside the supplied window.
        return self.norm(
            hidden[:, 0]
        )  # Summary only: [B,K+3,D] -> [B,D]; large D remains 512.

    def encode_full(
        self, market: Tensor, valid: Tensor, time: Tensor, time_valid: Tensor
    ) -> Tensor:
        """Embed all unmasked features; return [B,D] without invoking a decoder.

        market contains scaled market values [B,64,18] in phase1_contract.CHANNELS
        order, with bool validity of the same shape. time/time_valid contain the
        nine transformed UTC features and bool masks [B,64,9]. These are prepared
        model-device tensors, not raw prices or timestamps; unavailable values
        must be zero, as in prepare_batch. All 18 columns are supplied, although
        source validity can still mark genuinely unavailable observations.

        No no_grad context or train/eval change is imposed: phase-two losses can
        backpropagate through this method. For deployment, use encoder.eval()
        inside torch.inference_mode(); the output is still a single [B,D] vector.
        """
        if (
            market.ndim != 3
            or market.shape[1:] != (64, 18)
            or valid.shape != market.shape
        ):
            raise ValueError(
                "Full embedding requires market values and masks [B,64,18]."
            )
        ids = torch.arange(18, device=market.device).expand(
            len(market), -1
        )  # [18] -> [B,18], canonical identities.
        return self.forward(
            market.transpose(1, 2), valid.transpose(1, 2), ids, time, time_valid
        )  # [B,64,18] -> column tokens [B,18,64] -> embedding [B,D].


class ColumnDecoderV3(nn.Module):
    """Reconstruct requested hidden columns from one summary and known time context.

    Uses a small bidirectional reconstruction network, with the embedding as one
    memory token and hidden feature IDs as queries. No encoder column states enter.
    Output is [B,M,64] in scaled continuous market coordinates.
    """

    def __init__(self, config: EncoderConfig = EncoderConfig()):
        super().__init__()
        width = config.decoder_width
        self.summary = nn.Linear(config.width, width)  # Bottleneck [B,D] -> [B,d].
        self.time = nn.Linear(
            64 * 9 * 2, width
        )  # Known target timing, not market evidence.
        self.identity = nn.Embedding(
            18, width
        )  # Requested column queries [B,M] -> [B,M,d].
        self.blocks = blocks(
            width,
            config.decoder_heads,
            width * 4,
            config.decoder_layers,
            config.dropout,
            config.attention,
            config.attention_dropout,
        )
        self.output = nn.Sequential(nn.LayerNorm(width), nn.Linear(width, 64))

    def forward(
        self, embedding: Tensor, hidden_ids: Tensor, time: Tensor, time_valid: Tensor
    ) -> Tensor:
        """Map [B,D] and IDs [B,M], UTC context/masks [B,64,9] to predictions [B,M,64]."""
        context = torch.cat((time, (~time_valid).to(time.dtype)), dim=-1).flatten(1)
        queries = self.identity(hidden_ids) + self.time(context).unsqueeze(
            1
        )  # [B,M,d].
        hidden = torch.cat(
            (self.summary(embedding).unsqueeze(1), queries), dim=1
        )  # [B,M+1,d].
        for block in self.blocks:
            hidden = block(hidden)
        return self.output(
            hidden[:, 1:]
        )  # Discard summary token; reconstruct M requested columns.


class MaskedAutoencoderV3(nn.Module):
    """Compose the standalone encoder and disposable training decoder."""

    def __init__(self, config: EncoderConfig = EncoderConfig()):
        super().__init__()
        self.config = config
        self.encoder = ColumnEncoderV3(config)
        self.decoder = ColumnDecoderV3(config)

    def forward(
        self,
        visible: Tensor,
        valid: Tensor,
        feature_ids: Tensor,
        hidden_ids: Tensor,
        time: Tensor,
        time_valid: Tensor,
    ) -> tuple[Tensor, Tensor]:
        """Return embedding [B,D] and hidden reconstruction [B,M,64]; no targets are inputs."""
        embedding = self.encoder(visible, valid, feature_ids, time, time_valid)
        return embedding, self.decoder(embedding, hidden_ids, time, time_valid)
