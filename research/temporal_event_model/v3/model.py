from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Mapping

import torch
from torch import nn

from research.temporal_event_model.v3.config import (
    BAR_FAMILIES,
    BAR_FEATURE_DIMS,
    CORPORATE_ACTION_FLAGS,
    EVENT_TIME_FEATURE_NAMES,
    EXTERNAL_ARRIVAL_FLAGS,
    INTRADAY_EVENT_FLAGS,
    ModelConfig,
    TIME_ROLE_NAMES,
)


MODALITY_TOKEN_NAMES = (
    "events",
    "ticker_intraday_bars",
    "ticker_daily_bars",
    "global_daily_bars",
    "ticker_news",
    "market_news",
    "sec_filings",
    "xbrl",
    "corporate_actions",
    "scanner_context",
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
        time_encoder = TimeFeatureEncoder(config)
        self.event_encoder = EventEncoder(config, time_encoder)
        self.intraday_bar_encoder = BarContextEncoder(config, time_encoder)
        self.ticker_bar_encoder = BarContextEncoder(config, time_encoder)
        self.global_bar_encoder = BarContextEncoder(config, time_encoder)
        self.text_encoder = TextContextEncoder(config, time_encoder)
        self.xbrl_encoder = XbrlEncoder(config, time_encoder)
        self.corporate_action_encoder = CorporateActionEncoder(config, time_encoder)
        self.scanner_encoder = ScannerContextEncoder(config, time_encoder)
        self.modality_embedding = nn.Parameter(torch.zeros(10, d))
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
        return self._forward_impl(x)[0]

    def forward_with_timings(self, x: Mapping[str, Any], *, sync_cuda: bool = False) -> tuple[TemporalModelOutput, dict[str, float]]:
        return self._forward_impl(x, profile=True, sync_cuda=bool(sync_cuda))

    def _forward_impl(self, x: Mapping[str, Any], *, profile: bool = False, sync_cuda: bool = False) -> tuple[TemporalModelOutput, dict[str, float]]:
        timings: dict[str, float] = {}
        token_map = self._encode_modality_token_map(x, profile=profile, sync_cuda=sync_cuda, timings=timings)
        output, head_timings = self._predict_from_token_map(
            token_map,
            profile=profile,
            sync_cuda=sync_cuda,
            timing_prefix="",
        )
        timings.update(head_timings)
        if profile:
            timings["total_forward"] = sum(float(value) for value in timings.values())
        return output, timings

    def _encode_modality_token_map(
        self,
        x: Mapping[str, Any],
        *,
        profile: bool = False,
        sync_cuda: bool = False,
        timings: dict[str, float] | None = None,
    ) -> dict[str, torch.Tensor]:
        timings = timings if timings is not None else {}

        def timed(name: str, fn: Any) -> Any:
            if not profile:
                return fn()
            _sync_if_requested(sync_cuda)
            started = time.perf_counter()
            value = fn()
            _sync_if_requested(sync_cuda)
            timings[name] = time.perf_counter() - started
            return value

        bars = x.get("bar_inputs", {})
        text = x.get("text_inputs", {})
        return {
            "events": timed("event_encoder", lambda: self.event_encoder(x)),
            "ticker_intraday_bars": timed("intraday_bar_encoder", lambda: self.intraday_bar_encoder(bars.get("ticker_intraday_bars", {}))),
            "ticker_daily_bars": timed("ticker_daily_bar_encoder", lambda: self.ticker_bar_encoder(bars.get("ticker_daily_bars", {}))),
            "global_daily_bars": timed("global_daily_bar_encoder", lambda: self.global_bar_encoder(bars.get("global_daily_bars", {}))),
            "ticker_news": timed("ticker_news_encoder", lambda: self.text_encoder(text.get("ticker_news", {}), group="ticker_news")),
            "market_news": timed("market_news_encoder", lambda: self.text_encoder(text.get("market_news", {}), group="market_news")),
            "sec_filings": timed("sec_filing_encoder", lambda: self.text_encoder(text.get("sec_filings", {}), group="sec_filings")),
            "xbrl": timed("xbrl_encoder", lambda: self.xbrl_encoder(x.get("xbrl_inputs", {}))),
            "corporate_actions": timed("corporate_action_encoder", lambda: self.corporate_action_encoder(x.get("corporate_action_inputs", {}))),
            "scanner_context": timed("scanner_encoder", lambda: self.scanner_encoder(x.get("scanner_inputs", {}))),
        }

    def _predict_from_token_map(
        self,
        tokens: Mapping[str, torch.Tensor],
        *,
        profile: bool = False,
        sync_cuda: bool = False,
        timing_prefix: str = "",
    ) -> tuple[TemporalModelOutput, dict[str, float]]:
        timings: dict[str, float] = {}

        def timed(name: str, fn: Any) -> Any:
            if not profile:
                return fn()
            _sync_if_requested(sync_cuda)
            started = time.perf_counter()
            value = fn()
            _sync_if_requested(sync_cuda)
            timings[f"{timing_prefix}{name}"] = time.perf_counter() - started
            return value

        modality_tokens = timed("stack_cached_tokens", lambda: self._stack_modality_tokens(tokens))
        fused, pooled = timed("fusion", lambda: self._fuse_modality_tokens(modality_tokens))
        intraday = timed("intraday_query", lambda: self.intraday_query_mlp(pooled[:, None, :] + self.intraday_queries[None, :, :]))
        daily = timed("daily_query", lambda: self.daily_query_mlp(pooled[:, None, :] + self.daily_queries[None, :, :]))
        future_bar_values = timed("future_bar_heads", lambda: {family: head(intraday) for family, head in self.future_bar_heads.items()})
        intraday_logits = timed("intraday_classification_heads", lambda: {name: head(intraday).squeeze(-1) for name, head in self.intraday_heads.items()})
        corporate_logits = timed("corporate_action_heads", lambda: {name: head(daily).squeeze(-1) for name, head in self.corporate_action_heads.items()})
        return (
            TemporalModelOutput(
                future_bar_values=future_bar_values,
                intraday_logits=intraday_logits,
                corporate_action_logits=corporate_logits,
                modality_tokens=modality_tokens,
                fused_tokens=fused,
            ),
            timings,
        )

    def _stack_modality_tokens(self, tokens: Mapping[str, torch.Tensor]) -> torch.Tensor:
        reference = _first_tensor(tokens)
        if reference is None:
            raise RuntimeError("No modality tokens were provided.")
        batch_size = int(reference.shape[0])
        device = reference.device
        aligned = [
            _align_token(tokens.get(name), batch_size=batch_size, device=device, width=int(self.config.d_model))
            for name in MODALITY_TOKEN_NAMES
        ]
        modality = torch.stack(aligned, dim=1)
        return modality + self.modality_embedding[: modality.shape[1]].unsqueeze(0)

    def _fuse_modality_tokens(self, modality_tokens: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        fused_tokens = self.fusion_norm(self.fusion(modality_tokens))
        return fused_tokens, fused_tokens.mean(dim=1)

    def encode_modality_tokens(self, x: Mapping[str, Any]) -> dict[str, torch.Tensor]:
        """Encode every production-cacheable modality token.

        Production should cache these named ``[B, d_model]`` tensors and call
        :meth:`predict_from_modality_tokens` whenever the forecast head needs to
        refresh without recomputing unchanged encoders.
        """
        return self._encode_modality_token_map(x)

    def encode_modality_tokens_with_timings(self, x: Mapping[str, Any], *, sync_cuda: bool = False) -> tuple[dict[str, torch.Tensor], dict[str, float]]:
        timings: dict[str, float] = {}
        tokens = self._encode_modality_token_map(x, profile=True, sync_cuda=bool(sync_cuda), timings=timings)
        timings["cache_encode_total"] = sum(float(value) for value in timings.values())
        return tokens, timings

    def predict_from_modality_tokens(self, tokens: Mapping[str, torch.Tensor]) -> TemporalModelOutput:
        return self._predict_from_token_map(tokens)[0]

    def predict_from_modality_tokens_with_timings(
        self,
        tokens: Mapping[str, torch.Tensor],
        *,
        sync_cuda: bool = False,
        timing_prefix: str = "cached_",
    ) -> tuple[TemporalModelOutput, dict[str, float]]:
        output, timings = self._predict_from_token_map(tokens, profile=True, sync_cuda=bool(sync_cuda), timing_prefix=str(timing_prefix))
        timings[f"{timing_prefix}total"] = sum(float(value) for value in timings.values())
        return output, timings

    @torch.inference_mode()
    def encode_events(self, x: Mapping[str, Any]) -> torch.Tensor:
        return self.event_encoder(x)

    @torch.inference_mode()
    def encode_bars(self, x: Mapping[str, Any]) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        bars = x.get("bar_inputs", {})
        return (
            self.intraday_bar_encoder(bars.get("ticker_intraday_bars", {})),
            self.ticker_bar_encoder(bars.get("ticker_daily_bars", {})),
            self.global_bar_encoder(bars.get("global_daily_bars", {})),
        )

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

    @torch.inference_mode()
    def encode_corporate_actions(self, x: Mapping[str, Any]) -> torch.Tensor:
        return self.corporate_action_encoder(x.get("corporate_action_inputs", {}))

    @torch.inference_mode()
    def encode_scanner(self, x: Mapping[str, Any]) -> torch.Tensor:
        return self.scanner_encoder(x.get("scanner_inputs", {}))


class EventEncoder(nn.Module):
    def __init__(self, config: ModelConfig, time_encoder: "TimeFeatureEncoder") -> None:
        super().__init__()
        self.config = config
        d = int(config.d_model)
        self.time_feature_count = int(config.event_time_feature_count)
        self.time_encoder = time_encoder
        self.event_type = nn.Embedding(2, 8)
        self.price_scale = nn.Embedding(2, 8)
        self.tape = nn.Embedding(8, 8)
        self.condition = HashEmbedding(256, 8)
        self.exchange = HashEmbedding(256, 8)
        categorical_dim = 8 + 8 + 8 + 8 + 8 + 8 + 8
        self.numeric = nn.Linear(int(config.event_feature_count), d)
        self.input_mlp = MLP(d + categorical_dim + int(config.time_encoder_dim), d, d, dropout=float(config.dropout))
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
        time_features = _named_features(events, x, EVENT_TIME_FEATURE_NAMES, width=self.time_feature_count)
        numeric_events = _zero_named_features(events, x, EVENT_TIME_FEATURE_NAMES)
        time_token = self.time_encoder(time_features, role="event")
        token = self.input_mlp(torch.cat([self.numeric(torch.nan_to_num(numeric_events)), cat, time_token], dim=-1))
        token = token + self.position(positions)[None, :, :]
        encoded = self.encoder(token, src_key_padding_mask=~mask.bool())
        encoded = self.norm(encoded)
        return masked_mean(encoded, mask.bool(), dim=1)


class BarContextEncoder(nn.Module):
    def __init__(self, config: ModelConfig, time_encoder: "TimeFeatureEncoder") -> None:
        super().__init__()
        d = int(config.d_model)
        h = _side_hidden_dim(config)
        self.time_encoder = time_encoder
        self.time_feature_count = int(config.bar_time_feature_count)
        max_family_width = max(BAR_FEATURE_DIMS.values())
        feature_dim = int(max_family_width) + int(config.time_encoder_dim)
        self.family_embedding = nn.Embedding(len(BAR_FAMILIES), d)
        self.proj = MLP(feature_dim, h, d, dropout=float(config.dropout))

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
            time_features = _required_time_features(
                time_features,
                reference=values,
                width=self.time_feature_count,
                name=f"{family}_time_features",
            )
            time_token = self.time_encoder(time_features, role="bar_start")
            row = torch.cat(
                [
                    _pad_or_trim_last(values.float(), max(BAR_FEATURE_DIMS.values())),
                    time_token,
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
    def __init__(self, config: ModelConfig, time_encoder: "TimeFeatureEncoder") -> None:
        super().__init__()
        d = int(config.d_model)
        h = _side_hidden_dim(config)
        item_dim = max(8, int(config.text_item_dim))
        latent_count = max(1, int(config.text_latents))
        attention_heads = max(1, int(config.text_attention_heads))
        if item_dim % attention_heads != 0:
            raise ValueError(f"text_item_dim={item_dim} must be divisible by text_attention_heads={attention_heads}.")
        self.time_encoder = time_encoder
        self.time_feature_count = int(config.text_time_feature_count)
        self.item_dim = item_dim
        self.latent_count = latent_count
        self.group_to_id = {"ticker_news": 0, "market_news": 1, "sec_filings": 2}
        self.chunk_proj = nn.Sequential(nn.LayerNorm(int(config.text_embedding_dim)), nn.Linear(int(config.text_embedding_dim), item_dim), nn.GELU(), nn.Dropout(float(config.dropout)))
        self.time_proj = MLP(int(config.time_encoder_dim), max(item_dim, h), item_dim, dropout=float(config.dropout))
        self.group_embedding = nn.Embedding(len(self.group_to_id), item_dim)
        self.item_position_embedding = nn.Embedding(max(1, int(max(config.ticker_news_items, config.market_news_items, config.sec_filing_items))), item_dim)
        self.chunk_position_embedding = nn.Embedding(max(1, int(max(config.ticker_news_chunks, config.market_news_chunks, config.sec_filing_chunks))), item_dim)
        self.token_norm = nn.LayerNorm(item_dim)
        self.latent_queries = nn.Parameter(torch.randn(len(self.group_to_id), latent_count, item_dim) * 0.02)
        self.cross_attention = nn.MultiheadAttention(
            embed_dim=item_dim,
            num_heads=attention_heads,
            dropout=float(config.dropout),
            batch_first=True,
        )
        self.attention_norm = nn.LayerNorm(item_dim)
        self.latent_ffn = MLP(item_dim, max(item_dim, h), item_dim, dropout=float(config.dropout))
        self.latent_ffn_norm = nn.LayerNorm(item_dim)
        self.out_proj = MLP(item_dim, h, d, dropout=float(config.dropout))
        self.out_dim = d

    def forward(self, payload: Mapping[str, Any], *, group: str) -> torch.Tensor:
        embeddings = payload.get("embeddings")
        if not torch.is_tensor(embeddings) or embeddings.numel() == 0:
            return _zero_like_batch(payload, self.out_dim)
        chunk_mask = payload.get("chunk_mask")
        item_mask = payload.get("item_mask")
        item_time = payload.get("item_time_features")
        if not torch.is_tensor(chunk_mask):
            chunk_mask = torch.ones(embeddings.shape[:3], dtype=torch.bool, device=embeddings.device)
        else:
            chunk_mask = chunk_mask.to(device=embeddings.device, dtype=torch.bool)
        if not torch.is_tensor(item_mask):
            item_mask = chunk_mask.any(dim=-1)
        else:
            item_mask = item_mask.to(device=embeddings.device, dtype=torch.bool)
        item_time = _required_time_features(
            item_time,
            reference=item_mask,
            width=self.time_feature_count,
            name=f"{group}.item_time_features",
        )
        group_id = self.group_to_id.get(str(group), 0)
        chunks = self.chunk_proj(embeddings.float())
        item_positions = torch.arange(embeddings.shape[1], device=embeddings.device).clamp(max=self.item_position_embedding.num_embeddings - 1)
        chunk_positions = torch.arange(embeddings.shape[2], device=embeddings.device).clamp(max=self.chunk_position_embedding.num_embeddings - 1)
        time_token = self.time_proj(self.time_encoder(item_time, role="text_available")).unsqueeze(2)
        chunks = chunks + time_token
        chunks = chunks + self.group_embedding.weight[group_id].view(1, 1, 1, -1)
        chunks = chunks + self.item_position_embedding(item_positions).view(1, -1, 1, self.item_dim)
        chunks = chunks + self.chunk_position_embedding(chunk_positions).view(1, 1, -1, self.item_dim)
        token_mask = chunk_mask & item_mask.unsqueeze(-1)
        tokens = self.token_norm(chunks).reshape(embeddings.shape[0], -1, self.item_dim)
        token_mask = token_mask.reshape(embeddings.shape[0], -1)
        tokens = tokens * token_mask.unsqueeze(-1).to(dtype=tokens.dtype)
        has_tokens = token_mask.any(dim=1)
        safe_mask = token_mask.clone()
        safe_mask[:, 0] = safe_mask[:, 0] | ~has_tokens
        tokens = tokens.clone()
        tokens[:, 0, :] = torch.where(has_tokens[:, None], tokens[:, 0, :], torch.zeros_like(tokens[:, 0, :]))
        queries = self.latent_queries[group_id].unsqueeze(0).expand(embeddings.shape[0], -1, -1)
        attended, _ = self.cross_attention(
            queries,
            tokens,
            tokens,
            key_padding_mask=~safe_mask,
            need_weights=False,
        )
        latents = self.attention_norm(queries + attended)
        latents = self.latent_ffn_norm(latents + self.latent_ffn(latents))
        out = self.out_proj(latents.mean(dim=1))
        return out * has_tokens.unsqueeze(-1).to(dtype=out.dtype)


class XbrlEncoder(nn.Module):
    def __init__(self, config: ModelConfig, time_encoder: "TimeFeatureEncoder") -> None:
        super().__init__()
        d = int(config.d_model)
        h = _side_hidden_dim(config)
        item_dim = max(8, int(config.xbrl_item_dim))
        latent_count = max(1, int(config.xbrl_latents))
        attention_heads = max(1, int(config.xbrl_attention_heads))
        if item_dim % attention_heads != 0:
            raise ValueError(f"xbrl_item_dim={item_dim} must be divisible by xbrl_attention_heads={attention_heads}.")
        self.time_encoder = time_encoder
        self.time_feature_count = int(config.xbrl_time_feature_count)
        self.period_time_feature_count = int(config.xbrl_period_time_feature_count)
        self.item_dim = item_dim
        self.latent_count = latent_count
        self.scalar_keys = (
            "value",
            "mapping_confidence",
            "fiscal_year",
            "period_end_days",
            "age_days",
            "timestamp_us",
            "time_delta_seconds",
            "time_delta_seconds_log1p_signed",
            "time_age_seconds_log1p",
            "time_utc_second_of_day_sin",
            "time_utc_second_of_day_cos",
            "time_utc_day_of_week_sin",
            "time_utc_day_of_week_cos",
            "time_utc_day_of_year_sin",
            "time_utc_day_of_year_cos",
            "time_years_since_2000",
        )
        self.category_keys = ("fiscal_period_id", "calendar_period_id", "taxonomy_id", "tag_id", "unit_id", "form_id", "row_kind_id", "location_id")
        category_dim = max(1, int(config.xbrl_category_embedding_dim))
        category_sizes = dict(getattr(config, "xbrl_category_vocab_sizes", {}) or {})
        self.category_embeddings = nn.ModuleDict(
            {
                key: nn.Embedding(max(2, int(category_sizes.get(key, 1024))), category_dim, padding_idx=0)
                for key in self.category_keys
            }
        )
        self.scalar_norm = nn.LayerNorm(len(self.scalar_keys))
        numeric_dim = len(self.scalar_keys) + 2 * int(config.time_encoder_dim) + len(self.category_keys) * category_dim
        self.item_proj = MLP(numeric_dim, h, item_dim, dropout=float(config.dropout))
        self.latent_queries = nn.Parameter(torch.randn(latent_count, item_dim) * 0.02)
        self.cross_attention = nn.MultiheadAttention(
            embed_dim=item_dim,
            num_heads=attention_heads,
            dropout=float(config.dropout),
            batch_first=True,
        )
        self.attention_norm = nn.LayerNorm(item_dim)
        self.latent_ffn = MLP(item_dim, max(item_dim, h), item_dim, dropout=float(config.dropout))
        self.latent_ffn_norm = nn.LayerNorm(item_dim)
        self.out_proj = MLP(item_dim, h, d, dropout=float(config.dropout))
        self.out_dim = d

    def forward(self, payload: Mapping[str, Any]) -> torch.Tensor:
        value = payload.get("value")
        mask = payload.get("mask")
        if not torch.is_tensor(value) or value.numel() == 0:
            return _zero_like_batch(payload, self.out_dim)
        if not torch.is_tensor(mask):
            mask = torch.ones(value.shape, dtype=torch.bool, device=value.device)
        else:
            mask = mask.to(device=value.device, dtype=torch.bool)
        time_features = _required_time_features(payload.get("time_features"), reference=value, width=self.time_feature_count, name="xbrl.time_features")
        period_features = _required_time_features(
            payload.get("period_end_time_features"),
            reference=value,
            width=self.period_time_feature_count,
            name="xbrl.period_end_time_features",
        )
        time_token = self.time_encoder(time_features, role="xbrl_available")
        period_token = self.time_encoder(period_features, role="xbrl_period_end")
        cats = torch.cat([_safe_category_embedding(self.category_embeddings[key], _payload_ids(payload, key, value)) for key in self.category_keys], dim=-1)
        scalars = self.scalar_norm(torch.stack([_payload_scalar(payload, key, value) for key in self.scalar_keys], dim=-1))
        item_features = torch.cat([scalars, time_token, period_token, cats], dim=-1)
        items = self.item_proj(item_features)
        items = items * mask.unsqueeze(-1).to(dtype=items.dtype)
        has_items = mask.any(dim=1)
        safe_mask = mask.clone()
        safe_mask[:, 0] = safe_mask[:, 0] | ~has_items
        items = items.clone()
        items[:, 0, :] = torch.where(has_items[:, None], items[:, 0, :], torch.zeros_like(items[:, 0, :]))
        queries = self.latent_queries.unsqueeze(0).expand(value.shape[0], -1, -1)
        attended, _ = self.cross_attention(
            queries,
            items,
            items,
            key_padding_mask=~safe_mask,
            need_weights=False,
        )
        latents = self.attention_norm(queries + attended)
        latents = self.latent_ffn_norm(latents + self.latent_ffn(latents))
        pooled = latents.mean(dim=1)
        out = self.out_proj(pooled)
        return out * has_items.unsqueeze(-1).to(dtype=out.dtype)


class CorporateActionEncoder(nn.Module):
    def __init__(self, config: ModelConfig, time_encoder: "TimeFeatureEncoder") -> None:
        super().__init__()
        d = int(config.d_model)
        h = _side_hidden_dim(config)
        self.time_encoder = time_encoder
        self.time_feature_count = int(config.corporate_action_time_dim)
        self.effective_time_feature_count = int(config.corporate_action_effective_time_dim)
        self.cat = HashEmbedding(2048, 8)
        numeric_dim = int(config.corporate_action_numeric_dim) + 2 * int(config.time_encoder_dim) + 4 * 8
        self.row_proj = MLP(numeric_dim, h, d, dropout=float(config.dropout))
        self.out_dim = d

    def forward(self, payload: Mapping[str, Any]) -> torch.Tensor:
        numeric = payload.get("numeric_features")
        mask = payload.get("mask")
        if not torch.is_tensor(numeric) or numeric.numel() == 0:
            return _zero_like_batch(payload, self.out_dim)
        if not torch.is_tensor(mask):
            mask = torch.ones(numeric.shape[:2], dtype=torch.bool, device=numeric.device)
        time_features = _required_time_features(
            payload.get("time_features"),
            reference=numeric[..., 0],
            width=self.time_feature_count,
            name="corporate_actions.time_features",
        )
        effective = _required_time_features(
            payload.get("effective_time_features"),
            reference=numeric[..., 0],
            width=self.effective_time_feature_count,
            name="corporate_actions.effective_time_features",
        )
        time_token = self.time_encoder(time_features, role="corporate_available")
        effective_token = self.time_encoder(effective, role="corporate_effective")
        cats = torch.cat([self.cat(_payload_ids(payload, key, numeric[..., 0])) for key in ("action_type_id", "dividend_type_id", "currency_id", "frequency_id")], dim=-1)
        row = torch.cat([numeric.float(), time_token, effective_token, cats], dim=-1)
        return masked_mean(self.row_proj(row), mask.bool(), dim=1)


class ScannerContextEncoder(nn.Module):
    def __init__(self, config: ModelConfig, time_encoder: "TimeFeatureEncoder") -> None:
        super().__init__()
        d = int(config.d_model)
        h = _side_hidden_dim(config)
        self.time_encoder = time_encoder
        self.time_feature_count = int(config.bar_time_feature_count)
        self.value_width = max(BAR_FEATURE_DIMS.values())
        self.family_count = len(BAR_FAMILIES)
        self.group_embedding = nn.Embedding(max(1, int(config.scanner_groups)), d)
        self.rank_embedding = HashEmbedding(4096, 8)
        row_dim = self.value_width * self.family_count + int(config.time_encoder_dim) + 8
        self.leader_proj = MLP(row_dim, h, d, dropout=float(config.dropout))
        origin_dim = self.value_width * self.family_count + int(config.time_encoder_dim)
        self.origin_proj = MLP(origin_dim, h, d, dropout=float(config.dropout))
        self.numeric_proj = MLP(int(config.scanner_numeric_dim), h, d, dropout=float(config.dropout))
        self.out = MLP(d * 3, h, d, dropout=float(config.dropout))
        self.out_dim = d

    def forward(self, payload: Mapping[str, Any]) -> torch.Tensor:
        leader_values = payload.get("leader_values")
        if not torch.is_tensor(leader_values) or leader_values.numel() == 0:
            return _zero_like_batch(payload, self.out_dim)
        leader_mask = payload.get("leader_mask")
        leader_time = payload.get("leader_time_features")
        leader_rank = payload.get("leader_rank")
        leader_horizon_mask = payload.get("leader_horizon_mask")
        origin_values = payload.get("origin_values")
        origin_mask = payload.get("origin_mask")
        origin_horizon_mask = payload.get("origin_horizon_mask")
        origin_time = payload.get("origin_time_features")
        numeric = payload.get("numeric_features")
        if not torch.is_tensor(leader_mask):
            leader_mask = torch.ones(leader_values.shape[:3], dtype=torch.bool, device=leader_values.device)
        if not torch.is_tensor(leader_horizon_mask):
            leader_horizon_mask = leader_mask.unsqueeze(-1).expand(*leader_mask.shape, leader_values.shape[3])
        else:
            leader_horizon_mask = leader_horizon_mask.to(device=leader_values.device, dtype=torch.bool) & leader_mask.unsqueeze(-1).bool()
        if not torch.is_tensor(leader_rank):
            leader_rank = torch.zeros(leader_values.shape[:3], dtype=torch.long, device=leader_values.device)
        leader_time = _required_time_features(
            leader_time,
            reference=leader_values[..., 0, 0],
            width=self.time_feature_count,
            name="scanner.leader_time_features",
        )
        leader_value = _pad_or_trim_last(leader_values.float(), self.value_width).reshape(*leader_values.shape[:4], -1)
        leader_time_token = self.time_encoder(leader_time, role="scanner_bar_end")
        rank_token = self.rank_embedding(leader_rank.long()).unsqueeze(3).expand(*leader_value.shape[:4], -1)
        leader_rows = self.leader_proj(torch.cat([leader_value, leader_time_token, rank_token], dim=-1))
        group_ids = torch.arange(leader_values.shape[1], device=leader_values.device).clamp(max=self.group_embedding.num_embeddings - 1)
        leader_rows = leader_rows + self.group_embedding(group_ids)[None, :, None, None, :]
        leader_token = masked_mean(
            leader_rows.reshape(leader_rows.shape[0], -1, leader_rows.shape[-1]),
            leader_horizon_mask.reshape(leader_values.shape[0], -1).bool(),
            dim=1,
        )
        if not torch.is_tensor(origin_values) or origin_values.numel() == 0:
            origin_token = torch.zeros_like(leader_token)
        else:
            if not torch.is_tensor(origin_mask):
                origin_mask = torch.ones(origin_values.shape[:2], dtype=torch.bool, device=origin_values.device)
            if not torch.is_tensor(origin_horizon_mask):
                origin_horizon_mask = origin_mask.unsqueeze(-1).expand(*origin_mask.shape, origin_values.shape[2])
            else:
                origin_horizon_mask = origin_horizon_mask.to(device=origin_values.device, dtype=torch.bool) & origin_mask.unsqueeze(-1).bool()
            origin_time = _required_time_features(
                origin_time,
                reference=origin_values[..., 0, 0],
                width=self.time_feature_count,
                name="scanner.origin_time_features",
            )
            origin_value = _pad_or_trim_last(origin_values.float(), self.value_width).reshape(*origin_values.shape[:3], -1)
            origin_rows = self.origin_proj(torch.cat([origin_value, self.time_encoder(origin_time, role="scanner_bar_end")], dim=-1))
            origin_rows = origin_rows + self.group_embedding(group_ids)[None, :, None, :]
            origin_token = masked_mean(
                origin_rows.reshape(origin_rows.shape[0], -1, origin_rows.shape[-1]),
                origin_horizon_mask.reshape(origin_values.shape[0], -1).bool(),
                dim=1,
            )
        if torch.is_tensor(numeric) and numeric.numel() > 0:
            numeric_rows = self.numeric_proj(numeric.float())
            if not torch.is_tensor(origin_mask):
                numeric_mask = torch.ones(numeric_rows.shape[:2], dtype=torch.bool, device=numeric_rows.device)
            else:
                numeric_mask = origin_mask.to(device=numeric_rows.device, dtype=torch.bool)
            numeric_token = masked_mean(numeric_rows, numeric_mask, dim=1)
        else:
            numeric_token = torch.zeros_like(leader_token)
        return self.out(torch.cat([leader_token, origin_token, numeric_token], dim=-1))


class TimeFeatureEncoder(nn.Module):
    def __init__(self, config: ModelConfig) -> None:
        super().__init__()
        self.input_dim = int(config.time_feature_input_dim)
        self.output_dim = int(config.time_encoder_dim)
        self.role_to_id = {role: index for index, role in enumerate(TIME_ROLE_NAMES)}
        self.role_embedding = nn.Embedding(len(TIME_ROLE_NAMES), self.output_dim)
        self.net = nn.Sequential(
            nn.LayerNorm(self.input_dim),
            nn.Linear(self.input_dim, self.output_dim),
            nn.GELU(),
            nn.Dropout(float(config.dropout)),
            nn.Linear(self.output_dim, self.output_dim),
        )
        self.out_norm = nn.LayerNorm(self.output_dim)

    def forward(self, features: torch.Tensor, *, role: str) -> torch.Tensor:
        if role not in self.role_to_id:
            raise KeyError(f"Unknown time role {role!r}.")
        encoded = self.net(_pad_or_trim_last(features.float(), self.input_dim))
        role_ids = torch.full(features.shape[:-1], self.role_to_id[role], dtype=torch.long, device=features.device)
        return self.out_norm(encoded + self.role_embedding(role_ids))


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


def _side_hidden_dim(config: ModelConfig) -> int:
    value = int(getattr(config, "side_encoder_dim", 0) or 0)
    if value <= 0:
        return int(config.d_model)
    return max(16, int(value))


def _safe_category_embedding(embedding: nn.Embedding, ids: torch.Tensor) -> torch.Tensor:
    clean = ids.long().clamp(min=0)
    clean = torch.where(clean < int(embedding.num_embeddings), clean, torch.zeros_like(clean))
    return embedding(clean)


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


def _named_features(events: torch.Tensor, x: Mapping[str, Any], names: tuple[str, ...], *, width: int) -> torch.Tensor:
    feature_names = tuple(str(v) for v in x.get("event_feature_names", ()))
    missing = [name for name in names if name not in feature_names]
    if missing:
        raise RuntimeError(f"Raw event stream is missing required time features: {', '.join(missing)}")
    values = [events[..., feature_names.index(name)] for name in names]
    out = torch.stack(values, dim=-1).float()
    if out.shape[-1] != int(width):
        raise RuntimeError(f"Raw event time feature width is {out.shape[-1]}, expected {int(width)}.")
    return out


def _zero_named_features(events: torch.Tensor, x: Mapping[str, Any], names: tuple[str, ...]) -> torch.Tensor:
    feature_names = tuple(str(v) for v in x.get("event_feature_names", ()))
    indices = [feature_names.index(name) for name in names if name in feature_names]
    if not indices:
        return events
    out = events.clone()
    out[..., indices] = 0.0
    return out


def _required_time_features(value: Any, *, reference: torch.Tensor, width: int, name: str) -> torch.Tensor:
    if not torch.is_tensor(value):
        raise RuntimeError(f"Missing required time feature tensor: {name}.")
    expected_prefix = reference.shape[:-1] if value.ndim == reference.ndim else reference.shape
    if value.shape[:-1] != expected_prefix:
        raise RuntimeError(f"{name} shape prefix {tuple(value.shape[:-1])} does not match reference shape {tuple(expected_prefix)}.")
    if value.shape[-1] != int(width):
        raise RuntimeError(f"{name} width is {int(value.shape[-1])}, expected {int(width)}.")
    return value.float()


def _payload_ids(payload: Mapping[str, Any], key: str, reference: torch.Tensor) -> torch.Tensor:
    value = payload.get(key)
    if torch.is_tensor(value):
        return value.long()
    return torch.zeros_like(reference, dtype=torch.long)


def _payload_scalar(payload: Mapping[str, Any], key: str, reference: torch.Tensor) -> torch.Tensor:
    value = payload.get(key)
    if not torch.is_tensor(value):
        return torch.zeros_like(reference, dtype=torch.float32)
    out = value.to(device=reference.device, dtype=torch.float32)
    if out.shape != reference.shape:
        if out.numel() == reference.numel():
            out = out.reshape(reference.shape)
        else:
            return torch.zeros_like(reference, dtype=torch.float32)
    return torch.nan_to_num(out, nan=0.0, posinf=0.0, neginf=0.0)


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


def _align_token(token: torch.Tensor | None, *, batch_size: int, device: torch.device, width: int) -> torch.Tensor:
    if token is None or not torch.is_tensor(token) or token.numel() == 0:
        return torch.zeros(int(batch_size), int(width), dtype=torch.float32, device=device)
    if token.ndim != 2:
        token = token.reshape(token.shape[0], -1)
    if token.shape[-1] != int(width):
        token = _pad_or_trim_last(token, int(width))
    if token.shape[0] == int(batch_size) and token.device == device:
        return token
    if token.shape[0] == 1 and int(batch_size) > 1:
        token = token.expand(int(batch_size), -1)
    elif token.shape[0] != int(batch_size):
        token = torch.zeros(int(batch_size), int(width), dtype=token.dtype, device=token.device)
    return token.to(device=device, non_blocking=True)


def _first_tensor(tokens: Mapping[str, torch.Tensor]) -> torch.Tensor | None:
    for name in MODALITY_TOKEN_NAMES:
        token = tokens.get(name)
        if torch.is_tensor(token) and token.ndim >= 2 and token.shape[0] > 0:
            return token
    for token in tokens.values():
        if torch.is_tensor(token) and token.ndim >= 2 and token.shape[0] > 0:
            return token
    return None


def _sync_if_requested(sync_cuda: bool) -> None:
    if sync_cuda and torch.cuda.is_available():
        torch.cuda.synchronize()


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
  B --> IB["Ticker intraday bar encoder"]
  B --> TB["Ticker daily-bar encoder"]
  B --> GB["Global daily-bar encoder"]
  B --> TN["Ticker-news embedding encoder"]
  B --> MN["Market-news embedding encoder"]
  B --> SF["SEC embedding encoder"]
  B --> X["XBRL set encoder"]
  B --> CA["Corporate-action set encoder"]
  B --> SC["Scanner leader-context encoder"]
  E --> F["Fusion transformer"]
  IB --> F
  TB --> F
  GB --> F
  TN --> F
  MN --> F
  SF --> F
  X --> F
  CA --> F
  SC --> F
  F --> IQ["Intraday horizon queries"]
  F --> DQ["Daily corporate-action queries"]
  IQ --> PB["Trade/bid/ask bar heads"]
  IQ --> ES["Event-state and arrival heads"]
  DQ --> CL["Corporate-action daily heads"]
"""
