from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

import torch
import torch.nn.functional as F
from torch import nn

from research.news_reaction_model.v19.config import ModelConfig
from research.news_reaction_model.v19.targets import (
    DIRECTION_NAMES,
    FLOW_NAMES,
    PATH_NAMES,
    TrainingStatistics,
)


class TaskTower(nn.Module):
    def __init__(self, width: int, hidden: int, dropout: float) -> None:
        super().__init__()
        self.norm = nn.LayerNorm(width)
        self.net = nn.Sequential(
            nn.Linear(width, hidden),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(hidden, width),
            nn.Dropout(dropout),
        )

    def forward(self, value: torch.Tensor) -> torch.Tensor:
        return value + self.net(self.norm(value))


@dataclass(slots=True)
class EpisodeResponseOutput:
    direction_logits: torch.Tensor
    path_logits: torch.Tensor
    flow_logits: torch.Tensor
    regression: torch.Tensor
    regression_components: torch.Tensor
    regression_scales: torch.Tensor
    article_embedding: torch.Tensor


class NewsReactionModelV19(nn.Module):
    TOKEN_CURRENT = 0
    TOKEN_TEXT = 1
    TOKEN_STOCK = 2
    TOKEN_TIME = 3
    TOKEN_EPISODE = 4
    TOKEN_REGIME = 5
    TOKEN_PRIOR = 6

    def __init__(
        self,
        config: ModelConfig,
        training_statistics: TrainingStatistics,
    ) -> None:
        super().__init__()
        self.config = config
        self.training_statistics = training_statistics
        d = config.d_model
        self.current_token = nn.Parameter(torch.zeros(1, 1, d))
        nn.init.normal_(self.current_token, std=0.02)
        self.text_projection = nn.Sequential(
            nn.LayerNorm(config.openai_embedding_dim),
            nn.Linear(config.openai_embedding_dim, d),
            nn.GELU(),
        )
        self.stock_projection = nn.Sequential(
            nn.LayerNorm(config.stock_state_dim),
            nn.Linear(config.stock_state_dim, d),
            nn.GELU(),
        )
        self.time_projection = nn.Sequential(
            nn.Linear(config.time_feature_dim, d),
            nn.GELU(),
        )
        self.episode_projection = nn.Sequential(
            nn.LayerNorm(config.current_episode_feature_dim),
            nn.Linear(config.current_episode_feature_dim, d),
            nn.GELU(),
        )
        self.regime_projection = nn.Sequential(
            nn.Linear(2, d),
            nn.GELU(),
        )
        self.price_embedding = nn.Embedding(config.price_regimes, d)
        self.session_embedding = nn.Embedding(config.publication_sessions, d)
        self.context_projection = nn.Sequential(
            nn.LayerNorm(config.context_feature_dim),
            nn.Linear(config.context_feature_dim, d),
            nn.GELU(),
        )
        self.context_fusion = nn.Sequential(
            nn.LayerNorm(2 * d),
            nn.Linear(2 * d, d),
            nn.GELU(),
        )
        self.token_type = nn.Embedding(7, d)
        self.context_position = nn.Embedding(config.context_size, d)
        layer = nn.TransformerEncoderLayer(
            d_model=d,
            nhead=config.attention_heads,
            dim_feedforward=config.feedforward_dim,
            dropout=config.dropout,
            activation="gelu",
            batch_first=True,
            norm_first=True,
        )
        self.encoder = nn.TransformerEncoder(
            layer,
            num_layers=config.transformer_layers,
            norm=nn.LayerNorm(d),
            enable_nested_tensor=False,
        )
        self.output_norm = nn.LayerNorm(d)

        self.direction_tower = TaskTower(d, config.tower_hidden_dim, config.dropout)
        self.direction_head = nn.Linear(d, len(DIRECTION_NAMES))
        self.regression_tower = TaskTower(d, config.tower_hidden_dim, config.dropout)
        self.regression_head = nn.Linear(d, 3)
        self.flow_tower = TaskTower(d, config.tower_hidden_dim, config.dropout)
        self.flow_head = nn.Linear(d, len(FLOW_NAMES))
        self.path_condition = nn.Sequential(
            nn.LayerNorm(d + len(DIRECTION_NAMES) + 3),
            nn.Linear(d + len(DIRECTION_NAMES) + 3, d),
            nn.GELU(),
        )
        self.path_tower = TaskTower(d, config.tower_hidden_dim, config.dropout)
        self.path_head = nn.Linear(d, len(PATH_NAMES))

        self.register_buffer(
            "regression_scales",
            torch.tensor(training_statistics.regression_scales, dtype=torch.float32),
            persistent=True,
        )

    def _type(self, token: int, batch: int, device: torch.device) -> torch.Tensor:
        values = torch.full((batch,), token, device=device, dtype=torch.long)
        return self.token_type(values).unsqueeze(1)

    def encode_article(self, x: dict[str, Any]) -> torch.Tensor:
        batch = x["openai_embedding"].shape[0]
        device = x["openai_embedding"].device
        price = x["price_regime"].long()
        session = x["publication_session"].long()
        regime = self.regime_projection(
            torch.cat((x["anchor_log"], x["context_fraction"]), dim=-1)
        )
        regime = regime + self.price_embedding(price) + self.session_embedding(session)
        current = self.current_token.expand(batch, -1, -1)
        tokens = torch.cat(
            (
                current + self._type(self.TOKEN_CURRENT, batch, device),
                self.text_projection(x["openai_embedding"]).unsqueeze(1)
                + self._type(self.TOKEN_TEXT, batch, device),
                self.stock_projection(x["stock_state"]).unsqueeze(1)
                + self._type(self.TOKEN_STOCK, batch, device),
                self.time_projection(x["time_features"]).unsqueeze(1)
                + self._type(self.TOKEN_TIME, batch, device),
                self.episode_projection(x["current_episode_features"]).unsqueeze(1)
                + self._type(self.TOKEN_EPISODE, batch, device),
                regime.unsqueeze(1) + self._type(self.TOKEN_REGIME, batch, device),
            ),
            dim=1,
        )
        prior = self.context_fusion(
            torch.cat(
                (
                    self.text_projection(x["prior_openai_embeddings"]),
                    self.context_projection(x["prior_context_features"]),
                ),
                dim=-1,
            )
        )
        positions = torch.arange(self.config.context_size, device=device)
        prior = (
            prior
            + self.context_position(positions).unsqueeze(0)
            + self.token_type(
                torch.full(
                    (self.config.context_size,),
                    self.TOKEN_PRIOR,
                    dtype=torch.long,
                    device=device,
                )
            ).unsqueeze(0)
        )
        tokens = torch.cat((tokens, prior), dim=1)
        current_padding = torch.zeros((batch, 6), dtype=torch.bool, device=device)
        current_padding[:, 1] = ~x["channel_mask"][:, 0].bool()
        current_padding[:, 2] = ~x["channel_mask"][:, 1].bool()
        padding = torch.cat((current_padding, ~x["prior_context_mask"].bool()), dim=1)
        encoded = self.encoder(tokens, src_key_padding_mask=padding)
        return self.output_norm(encoded[:, 0])

    def _regression(
        self,
        shared: torch.Tensor,
        x: dict[str, Any],
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        raw = self.regression_head(self.regression_tower(shared))
        normalized = torch.stack(
            (raw[:, 0], F.softplus(raw[:, 1]), F.softplus(raw[:, 2])),
            dim=1,
        )
        scales = self.regression_scales[
            x["price_regime"].long(),
            x["publication_session"].long(),
        ].to(normalized.dtype)
        terminal = normalized[:, 0] * scales[:, 0]
        upper = normalized[:, 1] * scales[:, 1]
        lower = normalized[:, 2] * scales[:, 2]
        regression = torch.stack((terminal + upper, terminal - lower, terminal), dim=1)
        return regression, normalized, scales

    def forward(self, x: dict[str, Any]) -> EpisodeResponseOutput:
        shared = self.encode_article(x)
        direction_logits = self.direction_head(self.direction_tower(shared))
        regression, normalized, scales = self._regression(shared, x)
        flow_logits = self.flow_head(self.flow_tower(shared))
        path_input = torch.cat(
            (
                shared,
                torch.softmax(direction_logits.float(), dim=-1).to(shared.dtype).detach(),
                normalized.detach(),
            ),
            dim=1,
        )
        path_hidden = self.path_tower(self.path_condition(path_input))
        return EpisodeResponseOutput(
            direction_logits=direction_logits,
            path_logits=self.path_head(path_hidden),
            flow_logits=flow_logits,
            regression=regression,
            regression_components=normalized,
            regression_scales=scales,
            article_embedding=shared,
        )

    def shared_parameters(self) -> Iterable[nn.Parameter]:
        modules = (
            self.text_projection,
            self.stock_projection,
            self.time_projection,
            self.episode_projection,
            self.regime_projection,
            self.price_embedding,
            self.session_embedding,
            self.context_projection,
            self.context_fusion,
            self.token_type,
            self.context_position,
            self.encoder,
            self.output_norm,
        )
        yield self.current_token
        for module in modules:
            yield from module.parameters()

    def modules_for_task(self, task: str) -> tuple[nn.Module, ...]:
        if task == "direction":
            return self.direction_tower, self.direction_head
        if task == "regression":
            return self.regression_tower, self.regression_head
        if task == "flow":
            return self.flow_tower, self.flow_head
        if task == "path":
            return self.path_condition, self.path_tower, self.path_head
        raise KeyError(task)

    def set_trainable_tasks(
        self,
        *,
        shared: bool,
        tasks: set[str],
    ) -> None:
        for parameter in self.parameters():
            parameter.requires_grad_(False)
        if shared:
            for parameter in self.shared_parameters():
                parameter.requires_grad_(True)
        for task in tasks:
            for module in self.modules_for_task(task):
                for parameter in module.parameters():
                    parameter.requires_grad_(True)


def build_model_mermaid() -> str:
    return "\n".join(
        [
            "flowchart LR",
            '  current["Current query token"] --> encoder["Two-layer episode transformer"]',
            '  text["OpenAI text token"] --> encoder',
            '  stock["Point-in-time stock token"] --> encoder',
            '  time["Exchange-time token"] --> encoder',
            '  episode["Episode-state token"] --> encoder',
            '  regime["Anchor-price and session regime token"] --> encoder',
            '  prior["Up to eight causal prior-node tokens"] --> encoder',
            '  encoder --> shared["Shared current-node representation"]',
            '  shared --> direction["Direction tower"]',
            '  shared --> regression["Coherent high, low, terminal tower"]',
            '  shared --> flow["Flow tower"]',
            '  direction --> path["Conditioned path tower"]',
            '  regression --> path',
        ]
    )
