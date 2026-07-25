from __future__ import annotations

from pathlib import Path
from typing import Any

import torch

from research.news_reaction_model.v15.config import LoaderConfig, ModelConfig
from research.news_reaction_model.v15.data import rows_to_batch
from research.news_reaction_model.v15.model import NewsReactionModelV15
from research.news_reaction_model.v15.opportunity import (
    OPPORTUNITY_CLASS_NAMES,
    OpportunityClass,
)


class LiveFeatureEncoder:
    """Encode the exact V15 current-article and optional prior-context contract.

    The upstream causal context provider must supply fixed-size
    prior_openai_embeddings, prior_context_features, and prior_context_mask
    arrays built with the V15 context contract. Omitting them is a valid
    cold-start example and produces an all-masked context, never an implicit
    database lookup or a future-data fallback.
    """

    def __init__(self, loader_config: LoaderConfig) -> None:
        self.loader_config = loader_config

    def encode(self, rows: list[dict[str, Any]], *, device: torch.device) -> dict[str, torch.Tensor]:
        return rows_to_batch(rows, self.loader_config).to(device).x


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


def load_model(checkpoint: Path, *, device: torch.device) -> tuple[NewsReactionModelV15, LoaderConfig]:
    with torch.serialization.safe_globals([type(Path())]):
        state = torch.load(checkpoint, map_location=device, weights_only=True)
    loader_config = LoaderConfig(**state["config"]["loader"])
    model = NewsReactionModelV15(ModelConfig(**state["config"]["model"])).to(device)
    model.load_state_dict(state["model"])
    model.eval()
    return model, loader_config


@torch.no_grad()
def predict(
    model: NewsReactionModelV15,
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
