from __future__ import annotations

from pathlib import Path
from typing import Any

import torch

from research.news_reaction_model.v10.config import LoaderConfig, ModelConfig
from research.news_reaction_model.v10.model import NewsReactionModelV10
from research.news_reaction_model.v10.opportunity import (
    OPPORTUNITY_CLASS_NAMES,
    OpportunityClass,
)
from research.news_reaction_model.v10.time_features import encode_time_features


class LiveFeatureEncoder:
    """Validate text/state inputs and derive the corrected causal time channel."""

    def __init__(self, loader_config: LoaderConfig) -> None:
        self.loader_config = loader_config

    def encode(self, rows: list[dict[str, Any]], *, device: torch.device) -> dict[str, torch.Tensor]:
        embeddings: list[list[float]] = []
        states: list[list[float]] = []
        times: list[list[float]] = []
        masks: list[list[bool]] = []
        for source in rows:
            embedding = source.get("openai_embedding")
            if not isinstance(embedding, (list, tuple)) or len(embedding) != self.loader_config.openai_embedding_dim:
                raise ValueError(
                    f"V10 live inference requires a {self.loader_config.openai_embedding_dim}-value "
                    "OpenAI embedding for every article."
                )
            state = source.get("stock_state")
            if not isinstance(state, (list, tuple)) or len(state) != self.loader_config.stock_state_dim:
                raise ValueError(
                    f"V10 live inference requires the configured {self.loader_config.stock_state_dim}-value "
                    "point-in-time stock_state vector."
                )
            embedding_values = [float(value) for value in embedding]
            state_values = [float(value) for value in state]
            published_at_utc = source.get("published_at_utc")
            publication_session = source.get("publication_session")
            if not published_at_utc or not publication_session:
                raise ValueError(
                    "V10 live inference requires published_at_utc and publication_session "
                    "to reproduce the causal exchange-time channel."
                )
            embeddings.append(embedding_values)
            states.append(state_values)
            times.append(
                encode_time_features(published_at_utc, publication_session)
            )
            masks.append(
                [
                    any(value != 0.0 for value in embedding_values),
                    any(value != 0.0 for value in state_values),
                    True,
                ]
            )
        return {
            "openai_embedding": torch.tensor(embeddings, dtype=torch.float32, device=device),
            "stock_state": torch.tensor(states, dtype=torch.float32, device=device),
            "time_features": torch.tensor(times, dtype=torch.float32, device=device),
            "channel_mask": torch.tensor(masks, dtype=torch.bool, device=device),
        }


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


def load_model(checkpoint: Path, *, device: torch.device) -> tuple[NewsReactionModelV10, LoaderConfig]:
    with torch.serialization.safe_globals([type(Path())]):
        state = torch.load(checkpoint, map_location=device, weights_only=True)
    loader_config = LoaderConfig(**state["config"]["loader"])
    model = NewsReactionModelV10(ModelConfig(**state["config"]["model"])).to(device)
    model.load_state_dict(state["model"])
    model.eval()
    return model, loader_config


@torch.no_grad()
def predict(
    model: NewsReactionModelV10,
    encoded: dict[str, torch.Tensor],
) -> list[dict[str, Any]]:
    output = model(encoded)
    plans = opportunity_predictions(output)
    rows: list[dict[str, Any]] = []
    batch_size = encoded["openai_embedding"].shape[0]
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
