from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import IntelligenceConfig, load_manifest, model_path


class ModelRegistry:
    def __init__(self, config: IntelligenceConfig) -> None:
        self.config = config
        self.manifest = load_manifest(config.manifest_path)
        self.models = {item["key"]: item for item in self.manifest.get("models", [])}

    def model_info(self, key: str) -> dict[str, Any]:
        return self.models.get(key, {})

    def path_for(self, key: str) -> Path:
        return model_path(self.config, key)

    def exists(self, key: str) -> bool:
        path = self.path_for(key)
        return path.exists() and any(path.iterdir())

    def snapshot(self) -> dict[str, Any]:
        rows = []
        for key, item in sorted(self.models.items()):
            rows.append(
                {
                    **item,
                    "local_path": str(self.path_for(key)),
                    "downloaded": self.exists(key),
                }
            )
        return {
            "artifact_root": str(self.config.model_root),
            "active_sentiment_model": self.config.active_sentiment_model,
            "active_ner_model": self.config.active_ner_model,
            "models": rows,
        }

