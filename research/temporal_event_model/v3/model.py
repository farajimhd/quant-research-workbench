from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import torch
from torch import nn

from research.temporal_event_model.v3.config import (
    BAR_FAMILIES,
    BAR_FEATURE_DIMS,
    CORPORATE_ACTION_FLAGS,
    EXTERNAL_ARRIVAL_FLAGS,
    INTRADAY_EVENT_FLAGS,
    ModelConfig,
)


@dataclass(slots=True)
class TemporalModelOutput:
    future_bar_values: dict[str, torch.Tensor]
    intraday_logits: dict[str, torch.Tensor]
    corporate_action_logits: dict[str, torch.Tensor]
    modality_tokens: torch.Tensor
    fused_tokens: torch.Tensor


class TemporalEventModelV3(nn.Module):
    def __init__(self, config: ModelConfig) -> None:
        super().__init__()
        self.config = config
        d = int(config.d_model)
        self.event_encoder = EventEncoder(config)
        self.ticker_bar_encoder = BarContextEncoder(config)
        self.global_bar_encoder = BarContextEncoder(config)
        self.text_encoder = TextContextEncoder(config)
        self.xbrl_encoder = XbrlEncoder(config)
        self.corporate_action_encoder = CorporateActionEncoder(config)
        self.modality_embedding = nn.Parameter(torch.zeros(8, d))
        fusion_layer = nn.TransformerEncoderLayer(
            d_model=d,
            nhead=int(config.fusion_heads),
            dim_feedforward=4 * d,
            dropout=float(config.dropout),
            batch_first=True,
            norm_first=True,
            activation="gelu",
        )
        self.fusion = nn.TransformerEncoder(fusion_layer, num_layers=int(config.fusion_layers))
        self.fusion_norm = nn.LayerNorm(d)
        self.intraday_queries = nn.Parameter(torch.randn(int(config.intraday_horizons), d) * 0.02)
        self.daily_queries = nn.Parameter(torch.randn(len(config.corporate_action_days), d) * 0.02)
        self.intraday_query_mlp = MLP(d, d, d, dropout=float(config.dropout))
        self.daily_query_mlp = MLP(d, d, d, dropout=float(config.dropout))
        self.future_bar_heads = nn.ModuleDict(
            {family: nn.Linear(d, BAR_FEATURE_DIMS[family]) for family in BAR_FAMILIES}
        )
        self.intraday_heads = nn.ModuleDict(
            {name: nn.Linear(d, 1) for name in (*INTRADAY_EVENT_FLAGS, *EXTERNAL_ARRIVAL_FLAGS)}
        )
        self.corporate_action_heads = nn.ModuleDict({name: nn.Linear(d, 1) for name in CORPORATE_ACTION_FLAGS})
        self.apply(_init_weights)

    def forward(self, x: Mapping[str, Any]) -> TemporalModelOutput:
        event_token = self.event_encoder(x)
        batch_size = int(event_token.shape[0])
        device = event_token.device
        tokens = [
            event_token,
            self.ticker_bar_encoder(x.get("bar_inputs", {}).get("ticker_daily_bars", {})),
            self.global_bar_encoder(x.get("bar_inputs", {}).get("global_daily_bars", {})),
            self.text_encoder(x.get("text_inputs", {}).get("ticker_news", {}), group="ticker_news"),
            self.text_encoder(x.get("text_inputs", {}).get("market_news", {}), group="market_news"),
            self.text_encoder(x.get("text_inputs", {}).get("sec_filings", {}), group="sec_filings"),
            self.xbrl_encoder(x.get("xbrl_inputs", {})),
            self.corporate_action_encoder(x.get("corporate_action_inputs", {})),
        ]
        tokens = [_align_token(token, batch_size=batch_size, device=device) for token in tokens]
        modality_tokens = torch.stack(tokens, dim=1)
        modality_tokens = modality_tokens + self.modality_embedding[: modality_tokens.shape[1]].unsqueeze(0)
        fused = self.fusion_norm(self.fusion(modality_tokens))
        pooled = fused.mean(dim=1)
        intraday = self.intraday_query_mlp(pooled[:, None, :] + self.intraday_queries[None, :, :])
        daily = self.daily_query_mlp(pooled[:, None, :] + self.daily_queries[None, :, :])
        future_bar_values = {family: head(intraday) for family, head in self.future_bar_heads.items()}
        intraday_logits = {name: head(intraday).squeeze(-1) for name, head in self.intraday_heads.items()}
        corporate_logits = {name: head(daily).squeeze(-1) for name, head in self.corporate_action_heads.items()}
        return TemporalModelOutput(
            future_bar_values=future_bar_values,
            intraday_logits=intraday_logits,
            corporate_action_logits=corporate_logits,
            modality_tokens=modality_tokens,
            fused_tokens=fused,
        )

    @torch.inference_mode()
    def encode_events(self, x: Mapping[str, Any]) -> torch.Tensor:
        return self.event_encoder(x)

    @torch.inference_mode()
    def encode_bars(self, x: Mapping[str, Any]) -> tuple[torch.Tensor, torch.Tensor]:
        bars = x.get("bar_inputs", {})
        return self.ticker_bar_encoder(bars.get("ticker_daily_bars", {})), self.global_bar_encoder(bars.get("global_daily_bars", {}))

    @torch.inference_mode()
    def encode_text(self, x: Mapping[str, Any]) -> dict[str, torch.Tensor]:
        text = x.get("text_inputs", {})
        return {
            "ticker_news": self.text_encoder(text.get("ticker_news", {}), group="ticker_news"),
            "market_news": self.text_encoder(text.get("market_news", {}), group="market_news"),
            "sec_filings": self.text_encoder(text.get("sec_filings", {}), group="sec_filings"),
        }

    @torch.inference_mode()
    def encode_xbrl(self, x: Mapping[str, Any]) -> torch.Tensor:
        return self.xbrl_encoder(x.get("xbrl_inputs", {}))


class EventEncoder(nn.Module):
    def __init__(self, config: ModelConfig) -> None:
        super().__init__()
        self.config = config
        d = int(config.d_model)
        self.event_type = nn.Embedding(2, 8)
        self.price_scale = nn.Embedding(2, 8)
        self.tape = nn.Embedding(8, 8)
        self.condition = HashEmbedding(256, 8)
        self.exchange = HashEmbedding(256, 8)
        categorical_dim = 8 + 8 + 8 + 8 + 8 + 8 + 8
        self.numeric = nn.Linear(int(config.event_feature_count), d)
        self.input_mlp = MLP(d + categorical_dim, d, d, dropout=float(config.dropout))
        self.position = nn.Embedding(int(config.event_stream_length), d)
        layer = nn.TransformerEncoderLayer(
            d_model=d,
            nhead=int(config.event_heads),
            dim_feedforward=4 * d,
            dropout=float(config.dropout),
            batch_first=True,
            norm_first=True,
            activation="gelu",
        )
        self.encoder = nn.TransformerEncoder(layer, num_layers=int(config.event_layers))
        self.norm = nn.LayerNorm(d)

    def forward(self, x: Mapping[str, Any]) -> torch.Tensor:
        events = x["raw_event_stream"].float()
        mask = x.get("raw_event_mask")
        if mask is None or not torch.is_tensor(mask):
            mask = torch.ones(events.shape[:2], dtype=torch.bool, device=events.device)
        meta = _feature(events, x, "event_meta", 0).long()
        primary_scale = ((meta >> 1) & 1).clamp(0, 1)
        secondary_scale = ((meta >> 2) & 1).clamp(0, 1)
        tape = ((meta >> 3) & 7).clamp(0, 7)
        exchange_primary = _feature(events, x, "exchange_primary", 5).long()
        exchange_secondary = _feature(events, x, "exchange_secondary", 6).long()
        condition_tokens = [
            _feature(events, x, f"condition_token_{index}", 6 + index).long()
            for index in range(1, 6)
        ]
        condition_emb = torch.stack([self.condition(token) for token in condition_tokens], dim=0).mean(dim=0)
        cat = torch.cat(
            [
                self.event_type((meta & 1).clamp(0, 1)),
                self.price_scale(primary_scale),
                self.price_scale(secondary_scale),
                self.tape(tape),
                self.exchange(exchange_primary),
                self.exchange(exchange_secondary),
                condition_emb,
            ],
            dim=-1,
        )
        positions = torch.arange(events.shape[1], device=events.device).clamp(max=self.position.num_embeddings - 1)
        token = self.input_mlp(torch.cat([self.numeric(torch.nan_to_num(events)), cat], dim=-1))
        token = token + self.position(positions)[None, :, :]
        encoded = self.encoder(token, src_key_padding_mask=~mask.bool())
        encoded = self.norm(encoded)
        return masked_mean(encoded, mask.bool(), dim=1)


class BarContextEncoder(nn.Module):
    def __init__(self, config: ModelConfig) -> None:
        super().__init__()
        d = int(config.d_model)
        max_family_width = max(BAR_FEATURE_DIMS.values())
        feature_dim = int(max_family_width) + int(config.bar_time_feature_count)
        self.family_embedding = nn.Embedding(len(BAR_FAMILIES), d)
        self.proj = MLP(feature_dim, d, d, dropout=float(config.dropout))

    def forward(self, payload: Mapping[str, Any]) -> torch.Tensor:
        family_tokens: list[torch.Tensor] = []
        family_masks: list[torch.Tensor] = []
        for family_index, family in enumerate(BAR_FAMILIES):
            values = payload.get(f"{family}_values")
            if not torch.is_tensor(values) or values.numel() == 0:
                continue
            mask = payload.get(f"{family}_mask")
            time_features = payload.get(f"{family}_time_features")
            if not torch.is_tensor(mask):
                mask = torch.ones(values.shape[:-1], dtype=torch.bool, device=values.device)
            if not torch.is_tensor(time_features):
                time_features = torch.zeros(*values.shape[:-1], 0, device=values.device, dtype=values.dtype)
            row = torch.cat(
                [
                    _pad_or_trim_last(values.float(), max(BAR_FEATURE_DIMS.values())),
                    _pad_or_trim_last(time_features.float(), self.proj.in_dim - max(BAR_FEATURE_DIMS.values())),
                ],
                dim=-1,
            )
            token = self.proj(row) + self.family_embedding.weight[family_index]
            family_tokens.append(token.reshape(token.shape[0], -1, token.shape[-1]))
            family_masks.append(mask.reshape(mask.shape[0], -1).bool())
        if not family_tokens:
            return _zero_like_batch(payload, self.proj.out_dim)
        tokens = torch.cat(family_tokens, dim=1)
        masks = torch.cat(family_masks, dim=1)
        return masked_mean(tokens, masks, dim=1)


class TextContextEncoder(nn.Module):
    def __init__(self, config: ModelConfig) -> None:
        super().__init__()
        d = int(config.d_model)
        self.chunk_proj = nn.Sequential(nn.LayerNorm(int(config.text_embedding_dim)), nn.Linear(int(config.text_embedding_dim), d), nn.GELU(), nn.Dropout(float(config.dropout)))
        self.item_proj = MLP(d + 13, d, d, dropout=float(config.dropout))

    def forward(self, payload: Mapping[str, Any], *, group: str) -> torch.Tensor:
        embeddings = payload.get("embeddings")
        if not torch.is_tensor(embeddings) or embeddings.numel() == 0:
            return _zero_like_batch(payload, self.item_proj.out_dim)
        chunk_mask = payload.get("chunk_mask")
        item_mask = payload.get("item_mask")
        item_time = payload.get("item_time_features")
        if not torch.is_tensor(chunk_mask):
            chunk_mask = torch.ones(embeddings.shape[:3], dtype=torch.bool, device=embeddings.device)
        if not torch.is_tensor(item_mask):
            item_mask = chunk_mask.any(dim=-1)
        chunks = self.chunk_proj(embeddings.float())
        items = masked_mean(chunks, chunk_mask.bool(), dim=2)
        if not torch.is_tensor(item_time):
            item_time = torch.zeros(items.shape[0], items.shape[1], 13, device=items.device, dtype=items.dtype)
        if item_time.shape[-1] != 13:
            item_time = _pad_or_trim_last(item_time.float(), 13)
        items = self.item_proj(torch.cat([items, item_time.float()], dim=-1))
        return masked_mean(items, item_mask.bool(), dim=1)


class XbrlEncoder(nn.Module):
    def __init__(self, config: ModelConfig) -> None:
        super().__init__()
        d = int(config.d_model)
        self.cat = HashEmbedding(8192, 8)
        numeric_dim = 3 + int(config.xbrl_time_feature_count) + int(config.xbrl_period_time_feature_count) + 8 * 8
        self.row_proj = MLP(numeric_dim, d, d, dropout=float(config.dropout))
        self.out_dim = d

    def forward(self, payload: Mapping[str, Any]) -> torch.Tensor:
        value = payload.get("value")
        mask = payload.get("mask")
        if not torch.is_tensor(value) or value.numel() == 0:
            return _zero_like_batch(payload, self.out_dim)
        if not torch.is_tensor(mask):
            mask = torch.ones(value.shape, dtype=torch.bool, device=value.device)
        confidence = payload.get("mapping_confidence")
        if not torch.is_tensor(confidence):
            confidence = torch.zeros_like(value)
        time_features = _payload_feature(payload, "time_features", value, width=13)
        period_features = _payload_feature(payload, "period_end_time_features", value, width=7)
        cat_keys = ("fiscal_period_id", "calendar_period_id", "taxonomy_id", "tag_id", "unit_id", "form_id", "row_kind_id", "location_id")
        cats = torch.cat([self.cat(_payload_ids(payload, key, value)) for key in cat_keys], dim=-1)
        row = torch.cat([value.float().unsqueeze(-1), torch.log1p(value.float().abs()).unsqueeze(-1), confidence.float().unsqueeze(-1), time_features, period_features, cats], dim=-1)
        projected = self.row_proj(row)
        return masked_mean(projected, mask.bool(), dim=1)


class CorporateActionEncoder(nn.Module):
    def __init__(self, config: ModelConfig) -> None:
        super().__init__()
        d = int(config.d_model)
        self.cat = HashEmbedding(2048, 8)
        numeric_dim = int(config.corporate_action_numeric_dim) + int(config.corporate_action_time_dim) + int(config.corporate_action_effective_time_dim) + 4 * 8
        self.row_proj = MLP(numeric_dim, d, d, dropout=float(config.dropout))
        self.out_dim = d

    def forward(self, payload: Mapping[str, Any]) -> torch.Tensor:
        numeric = payload.get("numeric_features")
        mask = payload.get("mask")
        if not torch.is_tensor(numeric) or numeric.numel() == 0:
            return _zero_like_batch(payload, self.out_dim)
        if not torch.is_tensor(mask):
            mask = torch.ones(numeric.shape[:2], dtype=torch.bool, device=numeric.device)
        time_features = _payload_feature(payload, "time_features", numeric[..., 0], width=13)
        effective = _payload_feature(payload, "effective_time_features", numeric[..., 0], width=13)
        cats = torch.cat([self.cat(_payload_ids(payload, key, numeric[..., 0])) for key in ("action_type_id", "dividend_type_id", "currency_id", "frequency_id")], dim=-1)
        row = torch.cat([numeric.float(), time_features, effective, cats], dim=-1)
        return masked_mean(self.row_proj(row), mask.bool(), dim=1)


class HashEmbedding(nn.Module):
    def __init__(self, num_embeddings: int, embedding_dim: int) -> None:
        super().__init__()
        self.embedding = nn.Embedding(int(num_embeddings), int(embedding_dim))

    def forward(self, ids: torch.Tensor) -> torch.Tensor:
        return self.embedding(torch.remainder(ids.long().clamp(min=0), self.embedding.num_embeddings))


class MLP(nn.Module):
    def __init__(self, in_dim: int, hidden_dim: int, out_dim: int, *, dropout: float) -> None:
        super().__init__()
        self.in_dim = int(in_dim)
        self.out_dim = int(out_dim)
        self.net = nn.Sequential(
            nn.Linear(int(in_dim), int(hidden_dim)),
            nn.LayerNorm(int(hidden_dim)),
            nn.GELU(),
            nn.Dropout(float(dropout)),
            nn.Linear(int(hidden_dim), int(out_dim)),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x)


def masked_mean(x: torch.Tensor, mask: torch.Tensor, *, dim: int) -> torch.Tensor:
    mask_f = mask.to(dtype=x.dtype).unsqueeze(-1)
    total = (x * mask_f).sum(dim=dim)
    denom = mask_f.sum(dim=dim).clamp(min=1.0)
    return total / denom


def _feature(events: torch.Tensor, x: Mapping[str, Any], name: str, fallback_index: int) -> torch.Tensor:
    names = tuple(str(v) for v in x.get("event_feature_names", ()))
    index = names.index(name) if name in names else int(fallback_index)
    index = max(0, min(index, events.shape[-1] - 1))
    return events[..., index]


def _payload_feature(payload: Mapping[str, Any], key: str, reference: torch.Tensor, *, width: int) -> torch.Tensor:
    value = payload.get(key)
    if torch.is_tensor(value):
        return _pad_or_trim_last(value.float(), width)
    return torch.zeros(*reference.shape, int(width), dtype=torch.float32, device=reference.device)


def _payload_ids(payload: Mapping[str, Any], key: str, reference: torch.Tensor) -> torch.Tensor:
    value = payload.get(key)
    if torch.is_tensor(value):
        return value.long()
    return torch.zeros_like(reference, dtype=torch.long)


def _pad_or_trim_last(value: torch.Tensor, width: int) -> torch.Tensor:
    if value.shape[-1] == int(width):
        return value
    if value.shape[-1] > int(width):
        return value[..., : int(width)]
    pad = torch.zeros(*value.shape[:-1], int(width) - value.shape[-1], dtype=value.dtype, device=value.device)
    return torch.cat([value, pad], dim=-1)


def _zero_like_batch(payload: Mapping[str, Any], width: int) -> torch.Tensor:
    for value in payload.values() if isinstance(payload, Mapping) else ():
        if torch.is_tensor(value) and value.ndim:
            return torch.zeros(value.shape[0], int(width), dtype=torch.float32, device=value.device)
    return torch.zeros(1, int(width), dtype=torch.float32)


def _align_token(token: torch.Tensor, *, batch_size: int, device: torch.device) -> torch.Tensor:
    if token.shape[0] == int(batch_size) and token.device == device:
        return token
    if token.shape[0] == 1 and int(batch_size) > 1:
        token = token.expand(int(batch_size), -1)
    elif token.shape[0] != int(batch_size):
        token = torch.zeros(int(batch_size), token.shape[-1], dtype=token.dtype, device=token.device)
    return token.to(device=device, non_blocking=True)


def _init_weights(module: nn.Module) -> None:
    if isinstance(module, nn.Linear):
        nn.init.xavier_uniform_(module.weight)
        if module.bias is not None:
            nn.init.zeros_(module.bias)
    elif isinstance(module, nn.Embedding):
        nn.init.normal_(module.weight, mean=0.0, std=0.02)


def build_model_mermaid() -> str:
    return """flowchart LR
  B["Ticker-month batch"] --> E["Event encoder"]
  B --> TB["Ticker daily-bar encoder"]
  B --> GB["Global daily-bar encoder"]
  B --> TN["Ticker-news embedding encoder"]
  B --> MN["Market-news embedding encoder"]
  B --> SF["SEC embedding encoder"]
  B --> X["XBRL set encoder"]
  B --> CA["Corporate-action set encoder"]
  E --> F["Fusion transformer"]
  TB --> F
  GB --> F
  TN --> F
  MN --> F
  SF --> F
  X --> F
  CA --> F
  F --> IQ["Intraday horizon queries"]
  F --> DQ["Daily corporate-action queries"]
  IQ --> PB["Trade/bid/ask bar heads"]
  IQ --> ES["Event-state and arrival heads"]
  DQ --> CL["Corporate-action daily heads"]
"""
