"""First causal multiscale BarGPT implementation."""

MODEL_FAMILY = "bar_gpt"
MODEL_VERSION = "v1"

__all__ = [
    "BarGPTConfig",
    "BarGPTEncoder",
    "BarGPTOutput",
    "BarGPTV1",
    "DataConfig",
    "ExperimentConfig",
    "PackedBarEmbeddingAdapter",
    "TrainConfig",
]


def __getattr__(name: str):
    """Keep the ClickHouse builder independent from optional training imports."""
    if name in {"BarGPTConfig", "DataConfig", "ExperimentConfig", "TrainConfig"}:
        from research.bar_gpt.v1 import config

        return getattr(config, name)
    if name in {"BarGPTOutput", "BarGPTV1"}:
        from research.bar_gpt.v1 import model

        return getattr(model, name)
    if name == "BarGPTEncoder":
        from research.bar_gpt.v1.inference import BarGPTEncoder

        return BarGPTEncoder
    if name == "PackedBarEmbeddingAdapter":
        from research.bar_gpt.v1.integration import PackedBarEmbeddingAdapter

        return PackedBarEmbeddingAdapter
    raise AttributeError(name)
