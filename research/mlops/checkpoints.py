from __future__ import annotations

import json
import os
import queue
import shutil
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import torch


@dataclass(slots=True)
class CheckpointPolicy:
    latest_steps: int = 10
    archive_steps: int = 5000
    save_best_train: bool = True
    save_best_val: bool = True
    monitor_train_key: str = "pretrain/loss_total"
    monitor_val_key: str = "validation/pretrain/loss_total"
    skip_latest_if_busy: bool = True


class AsyncCheckpointManager:
    def __init__(
        self,
        checkpoint_dir: Path,
        manifest_path: Path,
        policy: CheckpointPolicy | None = None,
        message_callback: Callable[[str], None] | None = None,
    ) -> None:
        self.checkpoint_dir = checkpoint_dir
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        self.manifest_path = manifest_path
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        self.policy = policy or CheckpointPolicy()
        self.best_train_loss = float("inf")
        self.best_val_loss = float("inf")
        self.message_callback = message_callback
        self.jobs: queue.Queue[tuple[dict[str, Any], list[tuple[Path, str]], dict[str, Any]] | None] = queue.Queue(maxsize=2)
        self.worker = threading.Thread(target=self._worker, name="async-checkpoint-writer", daemon=True)
        self.worker.start()

    def set_message_callback(self, callback: Callable[[str], None] | None) -> None:
        self.message_callback = callback

    def maybe_save(
        self,
        *,
        step: int,
        payload: dict[str, Any],
        train_metrics: dict[str, float] | None = None,
        val_metrics: dict[str, float] | None = None,
        force: bool = False,
    ) -> None:
        reasons: list[tuple[Path, str]] = []
        train_metrics = train_metrics or {}
        val_metrics = val_metrics or {}
        if force or self.policy.latest_steps > 0 and step % self.policy.latest_steps == 0:
            reasons.append((self.checkpoint_dir / "checkpoint_latest.pt", "latest"))
        train_loss = train_metrics.get(self.policy.monitor_train_key)
        if self.policy.save_best_train and train_loss is not None and train_loss < self.best_train_loss:
            self.best_train_loss = float(train_loss)
            reasons.append((self.checkpoint_dir / "checkpoint_best_train.pt", "best_train"))
        val_loss = val_metrics.get(self.policy.monitor_val_key)
        if self.policy.save_best_val and val_loss is not None and val_loss < self.best_val_loss:
            self.best_val_loss = float(val_loss)
            reasons.append((self.checkpoint_dir / "checkpoint_best_val.pt", "best_val"))
        if force or self.policy.archive_steps > 0 and step % self.policy.archive_steps == 0:
            reasons.append((self.checkpoint_dir / f"checkpoint_step_{step:09d}.pt", "archive"))
        if not reasons:
            return
        latest_only = all(reason == "latest" for _, reason in reasons)
        if latest_only and self.policy.skip_latest_if_busy and self.jobs.qsize() > 0:
            self._message(f"Skipped latest checkpoint at step {step}; checkpoint writer is still busy.")
            return
        cpu_payload = to_cpu_payload(payload)
        event = {
            "step": step,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "train_loss": train_loss,
            "val_loss": val_loss,
        }
        self._enqueue(cpu_payload, reasons, event)

    def close(self) -> None:
        self.jobs.put(None)
        self.worker.join()

    def _enqueue(self, payload: dict[str, Any], reasons: list[tuple[Path, str]], event: dict[str, Any]) -> None:
        while True:
            try:
                self.jobs.put((payload, reasons, event), timeout=1)
                return
            except queue.Full:
                self._message("Checkpoint writer queue is full; waiting for previous save to finish.")

    def _worker(self) -> None:
        while True:
            job = self.jobs.get()
            if job is None:
                return
            payload, destinations, event = job
            for path, reason in destinations:
                atomic_torch_save(payload, path)
                self._append_manifest({**event, "reason": reason, "path": str(path)})
                self._message(f"Saved checkpoint {reason}: {path}")

    def _append_manifest(self, event: dict[str, Any]) -> None:
        with self.manifest_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, sort_keys=True, default=str) + "\n")

    def _message(self, text: str) -> None:
        if self.message_callback is not None:
            self.message_callback(text)
        else:
            print(text, flush=True)


def to_cpu_payload(value: Any) -> Any:
    if torch.is_tensor(value):
        return value.detach().cpu().clone()
    if isinstance(value, dict):
        return {key: to_cpu_payload(item) for key, item in value.items()}
    if isinstance(value, list):
        return [to_cpu_payload(item) for item in value]
    if isinstance(value, tuple):
        return tuple(to_cpu_payload(item) for item in value)
    return value


def atomic_torch_save(payload: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}.{time.time_ns()}")
    torch.save(payload, tmp)
    try:
        os.replace(tmp, path)
    except OSError:
        shutil.move(str(tmp), str(path))
