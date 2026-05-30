from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


class JsonlMetricLogger:
    def __init__(self, path: Path, wandb_run: Any | None = None) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.wandb_run = wandb_run

    def log(self, metrics: dict[str, float], step: int) -> None:
        row = {"step": step, "ts": datetime.now().isoformat(timespec="seconds"), **metrics}
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
        if self.wandb_run is not None:
            self.wandb_run.log(metrics, step=step)
