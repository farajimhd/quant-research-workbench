from __future__ import annotations

import math
from typing import Any

import torch


class SampleWarmupCosineScheduler:
    """Sample-clock linear warmup followed by one monotonic cosine decay."""

    def __init__(
        self,
        optimizer: torch.optim.Optimizer,
        *,
        warmup_samples: int,
        total_samples: int,
        minimum_lr: float,
        warmup_start_ratio: float = 0.1,
    ) -> None:
        if warmup_samples < 0 or total_samples <= 0 or warmup_samples >= total_samples:
            raise ValueError("scheduler requires 0 <= warmup_samples < total_samples")
        if not 0.0 < warmup_start_ratio <= 1.0:
            raise ValueError("warmup_start_ratio must be in (0,1]")
        self.optimizer = optimizer
        self.warmup_samples = int(warmup_samples)
        self.total_samples = int(total_samples)
        self.minimum_lr = float(minimum_lr)
        self.warmup_start_ratio = float(warmup_start_ratio)
        self.base_lrs = [float(group["lr"]) for group in optimizer.param_groups]
        if any(self.minimum_lr < 0 or self.minimum_lr > base for base in self.base_lrs):
            raise ValueError("minimum_lr must be between zero and every base learning rate")
        self.samples_seen = 0
        self.step(0)

    def _lr(self, base_lr: float, samples_seen: int) -> float:
        if self.warmup_samples and samples_seen < self.warmup_samples:
            progress = samples_seen / self.warmup_samples
            return base_lr * (self.warmup_start_ratio + (1.0 - self.warmup_start_ratio) * progress)
        decay_span = max(1, self.total_samples - self.warmup_samples)
        progress = min(1.0, max(0.0, (samples_seen - self.warmup_samples) / decay_span))
        return self.minimum_lr + (base_lr - self.minimum_lr) * 0.5 * (1.0 + math.cos(math.pi * progress))

    def step(self, samples_seen: int) -> None:
        self.samples_seen = max(0, int(samples_seen))
        for group, base_lr in zip(self.optimizer.param_groups, self.base_lrs, strict=True):
            group["lr"] = self._lr(base_lr, self.samples_seen)

    def state_dict(self) -> dict[str, Any]:
        return {"samples_seen": self.samples_seen, "base_lrs": self.base_lrs}

    def load_state_dict(self, state: dict[str, Any] | None) -> None:
        if not state:
            return
        saved_base = [float(value) for value in state.get("base_lrs", self.base_lrs)]
        if saved_base != self.base_lrs:
            raise RuntimeError("scheduler base learning rates do not match the resumed optimizer")
        self.step(int(state.get("samples_seen", 0)))


class SampleCosineRestartScheduler:
    """Sample-clock cosine annealing with decaying warm restarts."""

    def __init__(self, optimizer: torch.optim.Optimizer, *, cycle_samples: int, minimum_lr: float, restart_decay: float = 0.98) -> None:
        if cycle_samples <= 0 or not 0.0 < restart_decay <= 1.0:
            raise ValueError("cycle_samples must be positive and restart_decay must be in (0,1]")
        self.optimizer = optimizer
        self.cycle_samples = int(cycle_samples)
        self.minimum_lr = float(minimum_lr)
        self.restart_decay = float(restart_decay)
        self.base_lrs = [float(group["lr"]) for group in optimizer.param_groups]
        if any(self.minimum_lr < 0 or self.minimum_lr > base for base in self.base_lrs):
            raise ValueError("minimum_lr must be between zero and every base learning rate")
        self.samples_seen = 0
        self.step(0)

    def step(self, samples_seen: int) -> None:
        self.samples_seen = max(0, int(samples_seen))
        cycle = self.samples_seen // self.cycle_samples
        phase = (self.samples_seen % self.cycle_samples) / self.cycle_samples
        amplitude = self.restart_decay ** cycle
        for group, base_lr in zip(self.optimizer.param_groups, self.base_lrs, strict=True):
            peak = self.minimum_lr + (base_lr - self.minimum_lr) * amplitude
            group["lr"] = self.minimum_lr + (peak - self.minimum_lr) * 0.5 * (1.0 + math.cos(math.pi * phase))

    def state_dict(self) -> dict[str, Any]:
        return {"samples_seen": self.samples_seen, "base_lrs": self.base_lrs}

    def load_state_dict(self, state: dict[str, Any] | None) -> None:
        if not state:
            return
        saved_base = [float(value) for value in state.get("base_lrs", self.base_lrs)]
        if saved_base != self.base_lrs:
            raise RuntimeError("scheduler base learning rates do not match the resumed optimizer")
        self.step(int(state.get("samples_seen", 0)))
