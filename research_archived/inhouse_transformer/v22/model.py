from __future__ import annotations

import torch
from torch import nn

from research.inhouse_transformer.v22.config import ModelConfig


class EventFieldEncoder(nn.Module):
    def __init__(self, *, input_dim: int, hidden_dim: int, d_model: int, dropout: float) -> None:
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(input_dim, hidden_dim),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, d_model),
        )

    def forward(self, values: torch.Tensor) -> torch.Tensor:
        return self.net(values)


class HierarchicalEventTransformer(nn.Module):
    """Two-encoder quote/trade event model with local chunk and global context attention."""

    def __init__(
        self,
        *,
        quote_feature_count: int,
        trade_feature_count: int,
        chunk_summary_count: int,
        context_chunks: int,
        max_quote_events: int,
        max_trade_events: int,
        max_total_events: int,
        horizon_steps: int,
        target_count: int,
        config: ModelConfig,
    ) -> None:
        super().__init__()
        self.quote_feature_count = quote_feature_count
        self.trade_feature_count = trade_feature_count
        self.chunk_summary_count = chunk_summary_count
        self.context_chunks = context_chunks
        self.max_quote_events = max_quote_events
        self.max_trade_events = max_trade_events
        self.max_total_events = max_total_events
        self.horizon = horizon_steps
        self.target_count = target_count
        self.target_bit_count = config.target_bit_count

        self.quote_encoder = EventFieldEncoder(
            input_dim=quote_feature_count,
            hidden_dim=config.quote_hidden_dim,
            d_model=config.d_model,
            dropout=config.dropout,
        )
        self.trade_encoder = EventFieldEncoder(
            input_dim=trade_feature_count,
            hidden_dim=config.trade_hidden_dim,
            d_model=config.d_model,
            dropout=config.dropout,
        )
        self.event_type_embedding = nn.Embedding(3, config.d_model)
        self.local_position_embedding = nn.Embedding(max_total_events, config.d_model)

        local_layer = nn.TransformerEncoderLayer(
            d_model=config.d_model,
            nhead=config.num_heads,
            dim_feedforward=config.ff_dim,
            dropout=config.dropout,
            batch_first=True,
            activation="gelu",
            norm_first=True,
        )
        self.local_encoder = nn.TransformerEncoder(local_layer, num_layers=config.local_layers)
        self.local_norm = nn.LayerNorm(config.d_model)

        self.chunk_summary_projection = nn.Sequential(
            nn.Linear(chunk_summary_count, config.d_model),
            nn.GELU(),
            nn.Dropout(config.dropout),
            nn.Linear(config.d_model, config.d_model),
        )
        self.chunk_position_embedding = nn.Embedding(context_chunks, config.d_model)

        global_layer = nn.TransformerEncoderLayer(
            d_model=config.d_model,
            nhead=config.num_heads,
            dim_feedforward=config.ff_dim,
            dropout=config.dropout,
            batch_first=True,
            activation="gelu",
            norm_first=True,
        )
        self.global_encoder = nn.TransformerEncoder(global_layer, num_layers=config.global_layers)
        self.global_norm = nn.LayerNorm(config.d_model)

        self.head = nn.Sequential(
            nn.Linear(config.d_model, config.ff_dim),
            nn.GELU(),
            nn.Dropout(config.dropout),
            nn.Linear(config.ff_dim, horizon_steps * target_count * config.target_bit_count),
        )

    def forward(
        self,
        quote_values: torch.Tensor,
        trade_values: torch.Tensor,
        event_kinds: torch.Tensor,
        event_indices: torch.Tensor,
        chunk_summary: torch.Tensor,
    ) -> torch.Tensor:
        batch_size, context_chunks, max_quote_events, quote_feature_count = quote_values.shape
        if context_chunks != self.context_chunks:
            raise ValueError(f"Expected {self.context_chunks} context chunks, got {context_chunks}.")
        if max_quote_events != self.max_quote_events or quote_feature_count != self.quote_feature_count:
            raise ValueError("Quote event tensor shape does not match model config.")
        if trade_values.shape[2] != self.max_trade_events or trade_values.shape[3] != self.trade_feature_count:
            raise ValueError("Trade event tensor shape does not match model config.")
        if event_kinds.shape[2] != self.max_total_events:
            raise ValueError("Event-order tensor shape does not match model config.")

        flat_quote = quote_values.reshape(batch_size * context_chunks, self.max_quote_events, self.quote_feature_count)
        flat_trade = trade_values.reshape(batch_size * context_chunks, self.max_trade_events, self.trade_feature_count)
        quote_emb = self.quote_encoder(flat_quote)
        trade_emb = self.trade_encoder(flat_trade)

        flat_kinds = event_kinds.reshape(batch_size * context_chunks, self.max_total_events).clamp(0, 2)
        flat_indices = event_indices.reshape(batch_size * context_chunks, self.max_total_events)
        quote_indices = flat_indices.clamp(0, self.max_quote_events - 1)
        trade_indices = flat_indices.clamp(0, self.max_trade_events - 1)

        quote_selected = gather_event_embeddings(quote_emb, quote_indices)
        trade_selected = gather_event_embeddings(trade_emb, trade_indices)
        event_tokens = torch.where((flat_kinds == 0).unsqueeze(-1), quote_selected, trade_selected)
        event_tokens = torch.where((flat_kinds == 2).unsqueeze(-1), torch.zeros_like(event_tokens), event_tokens)
        local_pos = torch.arange(self.max_total_events, device=event_tokens.device)
        event_tokens = event_tokens + self.event_type_embedding(flat_kinds) + self.local_position_embedding(local_pos).view(1, -1, event_tokens.shape[-1])
        padding_mask = flat_kinds == 2
        all_padding = padding_mask.all(dim=1)
        if all_padding.any():
            padding_mask = padding_mask.clone()
            padding_mask[all_padding, 0] = False

        local_encoded = self.local_encoder(event_tokens, src_key_padding_mask=padding_mask)
        local_encoded = self.local_norm(local_encoded)
        valid_mask = (~padding_mask).float().unsqueeze(-1)
        pooled = (local_encoded * valid_mask).sum(dim=1) / valid_mask.sum(dim=1).clamp_min(1.0)
        pooled = pooled.reshape(batch_size, context_chunks, -1)

        summary_tokens = self.chunk_summary_projection(chunk_summary)
        chunk_pos = torch.arange(context_chunks, device=pooled.device)
        chunk_tokens = pooled + summary_tokens + self.chunk_position_embedding(chunk_pos).view(1, context_chunks, -1)
        global_tokens = self.global_encoder(chunk_tokens)
        last_token = self.global_norm(global_tokens[:, -1, :])
        return self.head(last_token).reshape(
            batch_size,
            self.horizon,
            self.target_count,
            self.target_bit_count,
        )


def gather_event_embeddings(values: torch.Tensor, indices: torch.Tensor) -> torch.Tensor:
    expanded_indices = indices.unsqueeze(-1).expand(-1, -1, values.shape[-1])
    return torch.gather(values, dim=1, index=expanded_indices)


def forecast_loss(prediction: torch.Tensor, target: torch.Tensor) -> tuple[torch.Tensor, dict[str, float]]:
    total = nn.functional.binary_cross_entropy_with_logits(prediction, target)
    bit_accuracy = ((prediction.detach().sigmoid() >= 0.5) == (target >= 0.5)).float().mean()
    return total, {
        "loss": float(total.detach().cpu()),
        "regression_loss": float(total.detach().cpu()),
        "bit_accuracy_pct": float(bit_accuracy.detach().cpu() * 100.0),
    }

