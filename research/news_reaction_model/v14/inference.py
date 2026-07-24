from __future__ import annotations

from pathlib import Path
from typing import Any

import torch

from research.news_reaction_model.v6.config import LoaderConfig as V6LoaderConfig
from research.news_reaction_model.v6.inference import LiveFeatureEncoder as V6LiveFeatureEncoder
from research.news_reaction_model.v14.config import LoaderConfig, ModelConfig
from research.news_reaction_model.v14.data import rows_to_batch
from research.news_reaction_model.v14.model import NewsReactionModelV14
from research.news_reaction_model.v14.opportunity import (
    OPPORTUNITY_CLASS_NAMES,
    OpportunityClass,
)


class LiveFeatureEncoder:
    """Apply the exact persisted V6 lexical/numeric contract used by V7/V14."""

    def __init__(self, loader_config: LoaderConfig | None = None) -> None:
        self.loader_config = loader_config or LoaderConfig()
        self.v6 = V6LiveFeatureEncoder(
            V6LoaderConfig(
                representation_artifact_root=self.loader_config.v6_feature_artifact_root,
                v5_feature_artifact_root=self.loader_config.v5_feature_artifact_root,
                word_vocab_size=self.loader_config.word_vocab_size,
                char_vocab_size=self.loader_config.char_vocab_size,
                numeric_vocab_size=self.loader_config.numeric_vocab_size,
                numeric_dense_dim=self.loader_config.numeric_dense_dim,
                numeric_max_text_chars=self.loader_config.numeric_max_text_chars,
                numeric_context_words=self.loader_config.numeric_context_words,
                numeric_max_mentions=self.loader_config.numeric_max_mentions,
            )
        )

    def encode(
        self,
        rows: list[dict[str, Any]],
        *,
        device: torch.device,
    ) -> dict[str, torch.Tensor]:
        transformed = self.v6.transform(rows)
        for source, row in zip(rows, transformed):
            state = source.get("stock_state")
            if not isinstance(state, (list, tuple)) or len(state) != self.loader_config.stock_state_dim:
                raise ValueError(
                    f"V14 live inference requires the configured "
                    f"{self.loader_config.stock_state_dim}-value point-in-time stock_state vector."
                )
            if not source.get("published_at_utc") or not source.get("publication_session"):
                raise ValueError(
                    "V14 live inference requires published_at_utc and publication_session "
                    "to reproduce the causal exchange-time channel."
                )
            row["stock_state"] = [float(value) for value in state]
        return rows_to_batch(transformed, self.loader_config).to(device).x


def opportunity_predictions(output: Any) -> dict[str, dict[str, torch.Tensor]]:
    plans: dict[str, dict[str, torch.Tensor]] = {}
    for horizon, logits in output.logits.items():
        probabilities = torch.softmax(logits.float(), dim=-1)
        confidence, predicted_class = probabilities.max(dim=-1)
        position = torch.zeros_like(predicted_class, dtype=torch.int8)
        position = torch.where(
            predicted_class == int(OpportunityClass.UPSIDE_DOMINANT),
            torch.ones_like(position),
            position,
        )
        position = torch.where(
            predicted_class == int(OpportunityClass.DOWNSIDE_DOMINANT),
            -torch.ones_like(position),
            position,
        )
        plans[horizon] = {
            "class": predicted_class,
            "confidence": confidence,
            "position": position,
            "probabilities": probabilities,
        }
    return plans


def load_model(
    checkpoint: Path,
    *,
    device: torch.device,
) -> tuple[NewsReactionModelV14, LoaderConfig]:
    with torch.serialization.safe_globals([type(Path())]):
        state = torch.load(checkpoint, map_location=device, weights_only=True)
    loader_config = LoaderConfig(**state["config"]["loader"])
    model = NewsReactionModelV14(ModelConfig(**state["config"]["model"])).to(device)
    model.load_state_dict(state["model"])
    model.eval()
    return model, loader_config


@torch.no_grad()
def predict(
    model: NewsReactionModelV14,
    encoded: dict[str, torch.Tensor],
) -> list[dict[str, Any]]:
    output = model(encoded)
    plans = opportunity_predictions(output)
    rows: list[dict[str, Any]] = []
    batch_size = encoded["word_ids"].shape[0]
    for row_index in range(batch_size):
        horizons: dict[str, Any] = {}
        for horizon, plan in plans.items():
            predicted_class = int(plan["class"][row_index])
            horizons[horizon] = {
                "opportunity_class": predicted_class,
                "opportunity": OPPORTUNITY_CLASS_NAMES[predicted_class],
                "confidence": float(plan["confidence"][row_index]),
                "position": int(plan["position"][row_index]),
                "probabilities": {
                    name: float(plan["probabilities"][row_index, class_index])
                    for class_index, name in enumerate(OPPORTUNITY_CLASS_NAMES)
                },
            }
        rows.append({"horizons": horizons})
    return rows
