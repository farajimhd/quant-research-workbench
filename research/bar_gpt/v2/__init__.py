"""Second causal multiscale BarGPT implementation."""

MODEL_FAMILY = "bar_gpt"
MODEL_VERSION = "v2"


def assert_checkpoint_version(payload: object) -> None:
    """Fail closed before loading weights from another BarGPT contract."""
    if not isinstance(payload, dict):
        raise RuntimeError("checkpoint payload must be a mapping")
    family = payload.get("model_family")
    version = payload.get("model_version")
    if family != MODEL_FAMILY or version != MODEL_VERSION:
        raise RuntimeError(
            f"checkpoint version mismatch: expected {MODEL_FAMILY}/{MODEL_VERSION}, "
            f"observed {family}/{version}"
        )

__all__ = [
    "BarGPTConfig",
    "BarGPTEncoder",
    "BarGPTOutput",
    "BarGPTV2",
    "DataConfig",
    "ExperimentConfig",
    "PackedBarEmbeddingAdapter",
    "TrainConfig",
    "assert_checkpoint_version",
]


def __getattr__(name: str):
    """Keep the ClickHouse builder independent from optional training imports."""
    if name in {"BarGPTConfig", "DataConfig", "ExperimentConfig", "TrainConfig"}:
        from research.bar_gpt.v2 import config

        return getattr(config, name)
    if name in {"BarGPTOutput", "BarGPTV2"}:
        from research.bar_gpt.v2 import model

        return getattr(model, name)
    if name == "BarGPTEncoder":
        from research.bar_gpt.v2.inference import BarGPTEncoder

        return BarGPTEncoder
    if name == "PackedBarEmbeddingAdapter":
        from research.bar_gpt.v2.integration import PackedBarEmbeddingAdapter

        return PackedBarEmbeddingAdapter
    raise AttributeError(name)
