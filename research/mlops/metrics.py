from __future__ import annotations

import json
import queue
import threading
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


class AsyncJsonlMetricLogger:
    """Move durable metric writes and W&B submission off the training thread."""

    def __init__(
        self,
        path: Path,
        wandb_run: Any | None = None,
        *,
        wandb_key_mapper: Callable[[str], str] | None = None,
    ) -> None:
        self._logger = JsonlMetricLogger(path, wandb_run, wandb_key_mapper=wandb_key_mapper)
        self._queue: queue.SimpleQueue[tuple[dict[str, float], int] | None] = queue.SimpleQueue()
        self._failure: BaseException | None = None
        self._thread = threading.Thread(target=self._run, name="metric-writer", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            while True:
                item = self._queue.get()
                if item is None:
                    return
                metrics, step = item
                self._logger.log(metrics, step)
        except BaseException as exc:
            self._failure = exc

    def _raise_if_failed(self) -> None:
        if self._failure is not None:
            raise RuntimeError("background metric writer failed") from self._failure

    def log(self, metrics: dict[str, float], step: int) -> None:
        self._raise_if_failed()
        self._queue.put((dict(metrics), int(step)))

    def close(self, timeout: float = 300.0) -> None:
        self._queue.put(None)
        self._thread.join(timeout=max(0.0, float(timeout)))
        if self._thread.is_alive():
            raise TimeoutError("background metric writer did not drain before timeout")
        self._raise_if_failed()
