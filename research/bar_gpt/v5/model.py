"""BarGPT v5: continuous bar tokens and a shared causal sequence encoder.

Read in this order: BarGPT.forward, CausalBlock.forward, then constructors.
This file contains only the model. Data loading, numerical transforms, target
construction, losses, training, and checkpoint management belong outside it.

One call processes one source/timeframe, potentially across many tickers. Calls
share the same Transformer weights. Combining streams across timeframes and
tickers is the later downstream model's responsibility.
"""

from dataclasses import dataclass

import torch
from torch import Tensor, nn


# IDs describe the stream; they are not a discrete vocabulary of bar patterns.
SOURCES = ("trade", "bid", "ask")
TIMEFRAMES = ("1s", "10s", "1m", "1h", "1d")


@dataclass
class BarGPTOutput:
    """Named outputs, with B=batch, L=sequence length, D=embedding width.

    hidden: [B, L, D], one causal embedding per scheduled bar.
    summary: [B, D], the last non-padding hidden state in each sequence.
    next_prices: [B, L, 4], next-bar OHLC regression predictions.
    next_activity: [B, L, 2], next-bar size/volume and count predictions.
    next_field_logits: [B, L, 6], next-bar field-availability logits.

    Prediction at index t targets scheduled bar t+1, not visible bar t.
    Numerical outputs are unconstrained predictions in the target transform's
    coordinates, which must be specified outside this model. Availability uses
    raw logits, suitable for a later binary-cross-entropy-with-logits loss.
    All per-bar outputs are zero at batch-padding positions. Missing market
    observations are still real scheduled tokens and receive predictions.
    """

    hidden: Tensor
    summary: Tensor
    next_prices: Tensor
    next_activity: Tensor
    next_field_logits: Tensor


class CausalBlock(nn.Module):
    """Pre-normalized attention and feed-forward residual block.

    Inputs and output have shape [B, L, D]. Normalization operates only over D,
    so it cannot read future bars. Attention reads the current and earlier
    non-padding tokens; the feed-forward network operates on each token alone.
    """

    def __init__(self, width: int, heads: int, feedforward_width: int, dropout: float):
        """Build attention (D=width, split across heads) and a D→F→D MLP."""
        super().__init__()
        self.attention_norm = nn.LayerNorm(width)  # Normalize before attention.
        self.attention = nn.MultiheadAttention(
            width, heads, dropout=dropout, batch_first=True
        )  # batch_first keeps the public [B, L, D] convention throughout.
        self.feedforward_norm = nn.LayerNorm(width)  # Normalize before the MLP.
        self.feedforward = nn.Sequential(
            nn.Linear(width, feedforward_width),  # [B, L, D] → [B, L, F].
            nn.GELU(),  # Nonlinearity lets the MLP learn more than a projection.
            nn.Dropout(dropout),  # Regularize intermediate feature activations.
            nn.Linear(feedforward_width, width),  # [B, L, F] → [B, L, D].
        )
        self.residual_dropout = nn.Dropout(dropout)  # Regularize residual updates.

    def forward(self, hidden: Tensor, future_mask: Tensor, padding_mask: Tensor) -> Tensor:
        """Update [B,L,D]; True masks forbid future [L,L] or padded [B,L] keys."""
        normalized = self.attention_norm(hidden)  # [B, L, D], per-token only.
        attended, _ = self.attention(
            normalized, normalized, normalized,  # Self-attention: same Q, K, V.
            attn_mask=future_mask,  # Prevent every query from seeing future bars.
            key_padding_mask=padding_mask,  # Exclude batch padding from memory.
            need_weights=False,  # Only embeddings are needed, not attention maps.
        )  # [B, L, D] → [B, L, D].
        hidden = hidden + self.residual_dropout(attended)  # Preserve a residual path.
        update = self.feedforward(self.feedforward_norm(hidden))  # [B, L, D].
        return hidden + self.residual_dropout(update)  # Second residual, [B, L, D].


class BarGPT(nn.Module):
    """Encode continuous bars; predict the next bar through disposable heads.

    Source fields come from research/market_shards/v1/data.py, in this order:
      trade: open, high, low, close, volume, trade_count;
      bid/ask: open, high, low, close, close_size, event_count.
    Trade notional is deliberately excluded. Inputs are transformed upstream;
    this model does not invent scaling or create additional market indicators.

    Current dataset windows are trade 60×1s, 30×10s, 30×1m, 16×1h, 20×1d;
    bid and ask each have 60×1s. Variable sequence lengths use the same weights.
    A timeframe embedding supplies duration identity; positional embeddings
    supply order, and caller-supplied causal time features describe clock/gaps.
    A different timeframe does not imply identical physical market dynamics.

    Architecture defaults (6 blocks, D=256, 8 heads, F=1024) are starting
    hypotheses, not experimentally established optimal settings.
    """

    def __init__(
        self,
        time_feature_dim: int,
        width: int = 256,
        layers: int = 6,
        heads: int = 8,
        feedforward_width: int = 1024,
        max_length: int = 60,
        dropout: float = 0.1,
    ):
        """Create the core model without any loader or training configuration.

        time_feature_dim: number T of externally defined causal time features.
            Required explicitly because their exact transformation is separate.
        width: embedding width D; must be divisible by attention head count.
        layers: number of shared causal blocks.
        heads: number of attention heads per block.
        feedforward_width: hidden width F within each block's token-wise MLP.
        max_length: learned ordinal-position capacity; 60 covers current windows.
        dropout: training-time dropout probability; disabled by model.eval().
        """
        super().__init__()
        if min(time_feature_dim, width, layers, heads, feedforward_width, max_length) < 1:
            raise ValueError("All architecture dimensions must be positive.")
        if width % heads != 0:
            raise ValueError("width must be divisible by heads.")
        if not 0 <= dropout < 1:
            raise ValueError("dropout must be in [0, 1).")

        self.time_feature_dim = time_feature_dim
        self.max_length = max_length
        self.bar_projections = nn.ModuleDict({
            source: nn.Linear(12, width) for source in SOURCES
        })  # Six values + six validity indicators: [B, L, 12] → [B, L, D].
        self.source_embedding = nn.Embedding(len(SOURCES), width)  # Source → [D].
        self.timeframe_embedding = nn.Embedding(len(TIMEFRAMES), width)  # Frame → [D].
        self.position_embedding = nn.Embedding(max_length, width)  # Ordinal → [D].
        self.time_projection = nn.Linear(time_feature_dim, width, bias=False)
        # The time projection maps [B, L, T] → [B, L, D] without defining clocks.
        self.blocks = nn.ModuleList([
            CausalBlock(width, heads, feedforward_width, dropout)
            for _ in range(layers)
        ])  # These blocks are shared by every source and timeframe.
        self.final_norm = nn.LayerNorm(width)  # Normalize exported embeddings.
        self.price_heads = nn.ModuleDict({
            source: nn.Linear(width, 4) for source in SOURCES
        })  # [B, L, D] → [B, L, 4], source-specific OHLC predictions.
        self.activity_heads = nn.ModuleDict({
            source: nn.Linear(width, 2) for source in SOURCES
        })  # [B, L, D] → [B, L, 2], differing trade/quote activity semantics.
        self.availability_heads = nn.ModuleDict({
            source: nn.Linear(width, 6) for source in SOURCES
        })  # [B, L, D] → [B, L, 6], separate future field-validity logits.

    def forward(
        self,
        values: Tensor,
        field_valid: Tensor,
        time_features: Tensor,
        token_valid: Tensor,
        *,
        source: str,
        timeframe: str,
    ) -> BarGPTOutput:
        """Turn a batch of causal bar sequences into embeddings and predictions.

        values: floating [B,L,6], upstream-transformed source fields in the order
            documented above. Valid fields must be finite; invalid fields may
            contain NaN and are masked before the learned projection.
        field_valid: bool [B,L,6], source evidence exists for each input field.
        time_features: floating [B,L,T], externally prepared, finite causal
            clock/gap features. No future availability or target data belongs here.
        token_valid: bool [B,L], True for scheduled bars (even if all six fields
            are missing), False only for right-side batch padding. Every sequence
            must have at least one scheduled token. Gaps must not be compressed.
        source: 'trade', 'bid', or 'ask', homogeneous within this call.
        timeframe: '1s', '10s', '1m', '1h', or '1d', homogeneous within this call.

        All tensors must be on the model's device; values/time_features must use
        its floating dtype (or a compatible autocast context). B and L are positive.
        Each hidden state sees its available prefix, not a full L-bar context.
        See BarGPTOutput for output shapes and next-scheduled-bar semantics.
        """
        if source not in SOURCES or timeframe not in TIMEFRAMES:
            raise ValueError("Unsupported source or timeframe.")
        if values.ndim != 3 or values.shape[-1] != 6:
            raise ValueError("values must have shape [B, L, 6].")
        batch, length, _ = values.shape
        if batch < 1 or not 1 <= length <= self.max_length:
            raise ValueError("Require B >= 1 and 1 <= L <= max_length.")
        if field_valid.shape != values.shape or field_valid.dtype != torch.bool:
            raise ValueError("field_valid must be boolean [B, L, 6].")
        if token_valid.shape != (batch, length) or token_valid.dtype != torch.bool:
            raise ValueError("token_valid must be boolean [B, L].")
        if time_features.shape != (batch, length, self.time_feature_dim):
            raise ValueError("time_features must have shape [B, L, time_feature_dim].")
        if not token_valid[:, 0].all() or (token_valid[:, 1:] & ~token_valid[:, :-1]).any():
            raise ValueError("Each sequence needs a nonempty, right-padded prefix.")
        # Reject malformed observations rather than silently repairing real data.
        if not torch.isfinite(values[field_valid & token_valid.unsqueeze(-1)]).all():
            raise ValueError("Valid input fields must be finite.")
        if not torch.isfinite(time_features[token_valid]).all():
            raise ValueError("Time features of scheduled tokens must be finite.")

        observed = field_valid & token_valid.unsqueeze(-1)  # [B,L,6], exclude padding.
        clean_values = values.masked_fill(~observed, 0)  # Missing NaNs cannot propagate.
        bar_inputs = torch.cat((clean_values, observed.to(values.dtype)), dim=-1)
        # [B,L,12]: validity separates a genuine numerical zero from missing data.
        hidden = self.bar_projections[source](bar_inputs)  # [B,L,12] → [B,L,D].
        positions = torch.arange(length, device=values.device)  # [L], ordinal order.
        hidden = hidden + self.position_embedding(positions)  # Broadcast [L,D].
        hidden = hidden + self.source_embedding.weight[SOURCES.index(source)]
        # Broadcast [D] to [B,L,D] to retain source identity in the shared backbone.
        hidden = hidden + self.timeframe_embedding.weight[TIMEFRAMES.index(timeframe)]
        # Broadcast [D] to [B,L,D] to distinguish the physical timeframe.
        clean_time = time_features.masked_fill(~token_valid.unsqueeze(-1), 0)
        hidden = hidden + self.time_projection(clean_time)  # [B,L,T] → [B,L,D].

        future_mask = torch.ones(
            length, length, dtype=torch.bool, device=values.device
        ).triu(1)
        # True above the diagonal forbids future keys; the current bar stays visible.
        for block in self.blocks:
            hidden = block(hidden, future_mask, ~token_valid)  # [B,L,D] unchanged.
        hidden = self.final_norm(hidden).masked_fill(~token_valid.unsqueeze(-1), 0)
        # Padding queries are discarded; they could never influence valid queries.
        last_index = token_valid.sum(dim=1) - 1  # [B], final scheduled-token index.
        summary = hidden[torch.arange(batch, device=values.device), last_index]
        # [B,D]: final causal state summarizes each sequence's non-padding prefix.

        output_mask = ~token_valid.unsqueeze(-1)  # [B,L,1], broadcast over head width.
        return BarGPTOutput(
            hidden=hidden,
            summary=summary,
            next_prices=self.price_heads[source](hidden).masked_fill(output_mask, 0),
            next_activity=self.activity_heads[source](hidden).masked_fill(output_mask, 0),
            next_field_logits=self.availability_heads[source](hidden).masked_fill(
                output_mask, 0
            ),
        )  # Direct heads are removable later; hidden and summary are the representation.
