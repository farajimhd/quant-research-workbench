from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from research.mlops.paths import default_run_root
from research.news_reaction_model.v16.config import LoaderConfig as V16LoaderConfig
from research.news_reaction_model.v16.config import ModelConfig as V16ModelConfig
from research.news_reaction_model.v17 import MODEL_FAMILY, MODEL_VERSION, RESPONSE_WINDOWS


def default_target_root() -> Path:
    return Path(
        os.environ.get(
            "NEWS_REACTION_V17_TARGET_ROOT",
            r"D:\market-data\prepared\news_reaction_model\v17\market_response_targets_v1",
        )
    )


@dataclass(slots=True)
class LoaderConfig(V16LoaderConfig):
    target_root: Path = field(default_factory=default_target_root)
    target_table: str = "news_market_response_outcomes_v1"
    target_version: str = "news_market_response_targets_v17"
    response_windows: tuple[str, ...] = RESPONSE_WINDOWS

    def __post_init__(self) -> None:
        V16LoaderConfig.__post_init__(self)
        self.target_root = Path(self.target_root)
        self.response_windows = tuple(self.response_windows)
        if self.response_windows != RESPONSE_WINDOWS:
            raise ValueError("V17 response-window contract is immutable.")


@dataclass(slots=True)
class ModelConfig(V16ModelConfig):
    response_windows: tuple[str, ...] = RESPONSE_WINDOWS
    response_window_dim: int = 32

    def __post_init__(self) -> None:
        V16ModelConfig.__post_init__(self)
        self.response_windows = tuple(self.response_windows)


@dataclass(slots=True)
class TrainConfig:
    output_root: Path = default_run_root(
        MODEL_FAMILY, MODEL_VERSION, "train", "v16-input-response-archetypes"
    )
    run_name: str = ""
    epochs: int = 50
    learning_rate: float = 3e-4
    weight_decay: float = 0.01
    grad_clip_norm: float = 1.0
    scheduler_restarts: int = 49
    scheduler_eta_min: float = 1e-6
    scheduler_cycle_decay: float = 0.98
    amp: bool = True
    amp_dtype: str = "bf16"
    seed: int = 17
    wandb_project: str = "news-reaction-model-v3"
    wandb_entity: str = "mehdifaraji"
    wandb_mode: str = "auto"
    evaluate_at_end: bool = True

    def __post_init__(self) -> None:
        self.output_root = Path(self.output_root)


@dataclass(slots=True)
class ExperimentConfig:
    loader: LoaderConfig = field(default_factory=LoaderConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
    train: TrainConfig = field(default_factory=TrainConfig)
