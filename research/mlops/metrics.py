from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Callable


class JsonlMetricLogger:
    def __init__(
        self,
        path: Path,
        wandb_run: Any | None = None,
        *,
        wandb_key_mapper: Callable[[str], str] | None = None,
    ) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.wandb_run = wandb_run
        self.wandb_key_mapper = wandb_key_mapper

    def log(self, metrics: dict[str, float], step: int) -> None:
        row = {"step": step, "ts": datetime.now().isoformat(timespec="seconds"), **metrics}
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
        if self.wandb_run is not None:
            wandb_metrics = metrics
            if self.wandb_key_mapper is not None:
                wandb_metrics = {self.wandb_key_mapper(key): value for key, value in metrics.items()}
            self.wandb_run.log(wandb_metrics, step=step)
