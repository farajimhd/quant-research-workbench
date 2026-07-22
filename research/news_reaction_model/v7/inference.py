from __future__ import annotations

from pathlib import Path
from typing import Any

import torch

from research.news_reaction_model.v7 import HORIZONS
from research.news_reaction_model.v6.config import LoaderConfig as V6LoaderConfig
from research.news_reaction_model.v6.inference import LiveFeatureEncoder as V6LiveFeatureEncoder
from research.news_reaction_model.v7.config import LoaderConfig, ModelConfig
from research.news_reaction_model.v7.data import rows_to_batch
from research.news_reaction_model.v7.model import NewsReactionModelV7, NewsReactionRangeOutput
from research.news_reaction_model.v7.stock_state import STOCK_STATE_DIM
from research.news_reaction_model.v7.ranges import RANGE_SPECS, TARGET_NAMES


def load_model(checkpoint_path: str | Path, *, device: str | torch.device = "cpu") -> NewsReactionModelV7:
    with torch.serialization.safe_globals([type(Path())]):
        state = torch.load(checkpoint_path, map_location=device, weights_only=True)
    raw_config = state.get("config", {}).get("model", {})
    config = ModelConfig(**raw_config) if raw_config else ModelConfig()
    model = NewsReactionModelV7(config).to(device)
    model.load_state_dict(state["model"])
    model.eval()
    return model


class LiveFeatureEncoder:
    """Frozen V6 text encoder plus an explicitly supplied point-in-time state vector."""

    def __init__(self, loader_config: LoaderConfig | None = None) -> None:
        self.loader_config = loader_config or LoaderConfig()
        self.v6 = V6LiveFeatureEncoder(V6LoaderConfig(
            representation_artifact_root=self.loader_config.v6_feature_artifact_root,
            v5_feature_artifact_root=self.loader_config.v5_feature_artifact_root,
            word_vocab_size=self.loader_config.word_vocab_size,
            char_vocab_size=self.loader_config.char_vocab_size,
            numeric_vocab_size=self.loader_config.numeric_vocab_size,
            numeric_dense_dim=self.loader_config.numeric_dense_dim,
            numeric_max_text_chars=self.loader_config.numeric_max_text_chars,
            numeric_context_words=self.loader_config.numeric_context_words,
            numeric_max_mentions=self.loader_config.numeric_max_mentions,
        ))

    def transform(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        output = self.v6.transform(rows)
        for source, row in zip(rows, output):
            state = source.get("stock_state")
            if not isinstance(state, (list, tuple)) or len(state) != STOCK_STATE_DIM:
                raise ValueError(
                    f"V7 live inference requires a {STOCK_STATE_DIM}-value point-in-time stock_state vector. "
                    "It must be materialized from the same authorities as training; missing state is not silently synthesized."
                )
            row["stock_state"] = [float(value) for value in state]
        return output


def trade_plans(output: NewsReactionRangeOutput) -> dict[str, dict[str, torch.Tensor]]:
    """Derive one target-touch plan per horizon from dominant predicted excursion.

    No ordering, stop, or risk rule is inferred. Ties and ranges whose total
    conservative excursion does not clear the horizon threshold abstain.
    """
    plans: dict[str, dict[str, torch.Tensor]] = {}
    for horizon in HORIZONS:
        spec = RANGE_SPECS[horizon]
        predicted: dict[str, torch.Tensor] = {}
        confidence: dict[str, torch.Tensor] = {}
        for target in TARGET_NAMES:
            probabilities = torch.softmax(output.logits[horizon][target].float(), dim=-1)
            confidence[target], predicted[target] = probabilities.max(dim=-1)
        upside_values = torch.tensor(
            [spec.conservative_upside_pct(index) for index in range(spec.classes)],
            device=predicted["high"].device,
        )
        downside_values = torch.tensor(
            [spec.conservative_downside_pct(index) for index in range(spec.classes)],
            device=predicted["low"].device,
        )
        upside = upside_values[predicted["high"]]
        downside = downside_values[predicted["low"]]
        span = upside + downside
        active = (span > spec.minimum_span_pct) & (upside != downside)
        side = torch.where(upside > downside, 1, -1)
        side = torch.where(active, side, 0)
        target_pct = torch.where(side > 0, upside, torch.where(side < 0, -downside, torch.zeros_like(upside)))
        plans[horizon] = {
            "side": side,
            "target_pct": target_pct,
            "upside_pct": upside,
            "downside_pct": downside,
            "span_pct": span,
            "ending_class": predicted["ending"],
            "high_class": predicted["high"],
            "low_class": predicted["low"],
            "ending_confidence": confidence["ending"],
            "high_confidence": confidence["high"],
            "low_confidence": confidence["low"],
        }
    return plans


@torch.inference_mode()
def forecast_rows(
    model: NewsReactionModelV7,
    rows: list[dict[str, Any]],
    *,
    loader_config: LoaderConfig | None = None,
    device: str | torch.device | None = None,
) -> list[dict[str, Any]]:
    config = loader_config or LoaderConfig(
        word_vocab_size=model.config.word_vocab_size,
        char_vocab_size=model.config.char_vocab_size,
        numeric_vocab_size=model.config.numeric_vocab_size,
        numeric_dense_dim=model.config.numeric_dense_dim,
        stock_state_dim=model.config.stock_state_dim,
        horizons=model.config.horizons,
    )
    target_device = torch.device(device) if device is not None else next(model.parameters()).device
    batch = rows_to_batch(rows, config).to(target_device)
    output = model(batch.x)
    plans = trade_plans(output)
    results: list[dict[str, Any]] = []
    for row_index in range(batch.sample_count):
        forecasts: dict[str, Any] = {}
        for horizon in HORIZONS:
            plan = plans[horizon]
            ranges = {}
            for target in TARGET_NAMES:
                class_index = int(plan[f"{target}_class"][row_index])
                lower, upper = RANGE_SPECS[horizon].interval(class_index)
                ranges[target] = {
                    "class": class_index,
                    "lower_pct": lower,
                    "upper_pct": None if upper == float("inf") else upper,
                    "confidence": float(plan[f"{target}_confidence"][row_index]),
                }
            forecasts[horizon] = {
                "position": int(plan["side"][row_index]),
                "target_pct": float(plan["target_pct"][row_index]),
                "upside_pct": float(plan["upside_pct"][row_index]),
                "downside_pct": float(plan["downside_pct"][row_index]),
                "span_pct": float(plan["span_pct"][row_index]),
                "ranges": ranges,
            }
        results.append({
            "canonical_news_id": batch.identity["canonical_news_id"][row_index],
            "ticker": batch.identity["ticker"][row_index],
            "published_at_utc": batch.identity["published_at_utc"][row_index],
            "forecasts": forecasts,
        })
    return results

