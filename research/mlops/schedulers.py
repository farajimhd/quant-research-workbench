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
        return {
            "scheduler_type": "sample_warmup_single_cosine",
            "samples_seen": self.samples_seen,
            "base_lrs": self.base_lrs,
            "warmup_samples": self.warmup_samples,
            "total_samples": self.total_samples,
            "minimum_lr": self.minimum_lr,
            "warmup_start_ratio": self.warmup_start_ratio,
        }

    def load_state_dict(self, state: dict[str, Any] | None) -> None:
        if not state:
            return
        if state.get("scheduler_type") != "sample_warmup_single_cosine":
            raise RuntimeError("scheduler type does not match the resumed run")
        saved_base = [float(value) for value in state.get("base_lrs", self.base_lrs)]
        if saved_base != self.base_lrs:
            raise RuntimeError("scheduler base learning rates do not match the resumed optimizer")
        saved_contract = {
            "warmup_samples": int(state.get("warmup_samples", -1)),
            "total_samples": int(state.get("total_samples", -1)),
            "minimum_lr": float(state.get("minimum_lr", -1)),
            "warmup_start_ratio": float(state.get("warmup_start_ratio", -1)),
        }
        current_contract = {
            "warmup_samples": self.warmup_samples,
            "total_samples": self.total_samples,
            "minimum_lr": self.minimum_lr,
            "warmup_start_ratio": self.warmup_start_ratio,
        }
        if saved_contract != current_contract:
            raise RuntimeError("scheduler configuration does not match the resumed run")
        self.step(int(state.get("samples_seen", 0)))


class SampleCosineRestartScheduler:
    """Sample-clock linear warmup followed by decaying cosine restarts."""

    def __init__(
        self,
        optimizer: torch.optim.Optimizer,
        *,
        cycle_samples: int,
        minimum_lr: float,
        restart_decay: float = 0.98,
        warmup_samples: int = 0,
    ) -> None:
        if warmup_samples < 0 or cycle_samples <= 0 or not 0.0 < restart_decay <= 1.0:
            raise ValueError(
                "warmup_samples must be non-negative, cycle_samples must be positive, "
                "and restart_decay must be in (0,1]"
            )
        self.optimizer = optimizer
        self.warmup_samples = int(warmup_samples)
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
        if self.warmup_samples and self.samples_seen < self.warmup_samples:
            progress = self.samples_seen / self.warmup_samples
            for group, base_lr in zip(self.optimizer.param_groups, self.base_lrs, strict=True):
                group["lr"] = self.minimum_lr + (base_lr - self.minimum_lr) * progress
            return
        decay_samples = self.samples_seen - self.warmup_samples
        cycle = decay_samples // self.cycle_samples
        phase = (decay_samples % self.cycle_samples) / self.cycle_samples
        amplitude = self.restart_decay ** cycle
        for group, base_lr in zip(self.optimizer.param_groups, self.base_lrs, strict=True):
            peak = self.minimum_lr + (base_lr - self.minimum_lr) * amplitude
            group["lr"] = self.minimum_lr + (peak - self.minimum_lr) * 0.5 * (1.0 + math.cos(math.pi * phase))

    def state_dict(self) -> dict[str, Any]:
        return {
            "samples_seen": self.samples_seen,
            "base_lrs": self.base_lrs,
            "warmup_samples": self.warmup_samples,
            "cycle_samples": self.cycle_samples,
            "minimum_lr": self.minimum_lr,
            "restart_decay": self.restart_decay,
        }

    def load_state_dict(self, state: dict[str, Any] | None) -> None:
        if not state:
            return
        saved_base = [float(value) for value in state.get("base_lrs", self.base_lrs)]
        if saved_base != self.base_lrs:
            raise RuntimeError("scheduler base learning rates do not match the resumed optimizer")
        saved_contract = {
            "warmup_samples": int(state.get("warmup_samples", self.warmup_samples)),
            "cycle_samples": int(state.get("cycle_samples", self.cycle_samples)),
            "minimum_lr": float(state.get("minimum_lr", self.minimum_lr)),
            "restart_decay": float(state.get("restart_decay", self.restart_decay)),
        }
        current_contract = {
            "warmup_samples": self.warmup_samples,
            "cycle_samples": self.cycle_samples,
            "minimum_lr": self.minimum_lr,
            "restart_decay": self.restart_decay,
        }
        if saved_contract != current_contract:
            raise RuntimeError("scheduler configuration does not match the resumed run")
        self.step(int(state.get("samples_seen", 0)))


class EpochChunkCosineScheduler:
    """One sample-clock cosine cycle across all allowed replays of a chunk."""

    def __init__(
        self,
        optimizer: torch.optim.Optimizer,
        *,
        minimum_lr: float,
        epoch_decay: float,
        warmup_samples: int = 0,
    ) -> None:
        if warmup_samples < 0 or not 0.0 < epoch_decay <= 1.0:
            raise ValueError(
                "warmup_samples must be non-negative and epoch_decay must be in (0,1]"
            )
        self.optimizer = optimizer
        self.minimum_lr = float(minimum_lr)
        self.epoch_decay = float(epoch_decay)
        self.warmup_samples = int(warmup_samples)
        self.base_lrs = [float(group["lr"]) for group in optimizer.param_groups]
        if any(self.minimum_lr < 0 or self.minimum_lr > base for base in self.base_lrs):
            raise ValueError("minimum_lr must be between zero and every base learning rate")
        self.samples_seen = 0
        self.epoch = 0
        self.chunk_start_samples = 0
        self.chunk_samples = 1
        self.warmup_completed_samples: int | None = None
        self.step(samples_seen=0)

    def _epoch_peak(self, base_lr: float) -> float:
        return max(self.minimum_lr, base_lr * self.epoch_decay**self.epoch)

    @property
    def chunk_progress(self) -> float:
        if self.warmup_samples and self.samples_seen < self.warmup_samples:
            return 0.0
        cosine_start_samples = max(
            self.chunk_start_samples,
            self.warmup_completed_samples or self.chunk_start_samples,
        )
        cosine_samples = max(
            1,
            self.chunk_start_samples + self.chunk_samples - cosine_start_samples,
        )
        return min(
            1.0,
            max(0.0, (self.samples_seen - cosine_start_samples) / cosine_samples),
        )

    def start_chunk(
        self,
        *,
        epoch: int,
        start_samples: int,
        chunk_samples: int,
        samples_seen: int,
    ) -> None:
        if epoch < 0 or start_samples < 0 or chunk_samples <= 0:
            raise ValueError(
                "chunk scheduler requires non-negative epoch/start and positive samples"
            )
        if samples_seen < start_samples:
            raise ValueError("samples_seen cannot precede the active chunk start")
        self.epoch = int(epoch)
        self.chunk_start_samples = int(start_samples)
        self.chunk_samples = int(chunk_samples)
        self.step(samples_seen=samples_seen)

    def step(self, *, samples_seen: int) -> None:
        self.samples_seen = max(0, int(samples_seen))
        if self.warmup_samples and self.samples_seen < self.warmup_samples:
            progress = self.samples_seen / self.warmup_samples
            for group, base_lr in zip(self.optimizer.param_groups, self.base_lrs, strict=True):
                peak = self._epoch_peak(base_lr)
                group["lr"] = self.minimum_lr + (peak - self.minimum_lr) * progress
            return
        if self.warmup_samples and self.warmup_completed_samples is None:
            self.warmup_completed_samples = self.samples_seen
        progress = self.chunk_progress
        cosine = 0.5 * (1.0 + math.cos(math.pi * progress))
        for group, base_lr in zip(self.optimizer.param_groups, self.base_lrs, strict=True):
            peak = self._epoch_peak(base_lr)
            group["lr"] = self.minimum_lr + (peak - self.minimum_lr) * cosine

    def state_dict(self) -> dict[str, Any]:
        return {
            "scheduler_type": "epoch_chunk_cosine",
            "samples_seen": self.samples_seen,
            "epoch": self.epoch,
            "chunk_start_samples": self.chunk_start_samples,
            "chunk_samples": self.chunk_samples,
            "warmup_completed_samples": self.warmup_completed_samples,
            "base_lrs": self.base_lrs,
            "warmup_samples": self.warmup_samples,
            "minimum_lr": self.minimum_lr,
            "epoch_decay": self.epoch_decay,
        }

    def load_state_dict(self, state: dict[str, Any] | None) -> None:
        if not state:
            return
        if state.get("scheduler_type") != "epoch_chunk_cosine":
            raise RuntimeError("scheduler type does not match the resumed run")
        saved_base = [float(value) for value in state.get("base_lrs", self.base_lrs)]
        if saved_base != self.base_lrs:
            raise RuntimeError("scheduler base learning rates do not match the resumed optimizer")
        saved_contract = {
            "warmup_samples": int(state.get("warmup_samples", -1)),
            "minimum_lr": float(state.get("minimum_lr", -1)),
            "epoch_decay": float(state.get("epoch_decay", -1)),
        }
        current_contract = {
            "warmup_samples": self.warmup_samples,
            "minimum_lr": self.minimum_lr,
            "epoch_decay": self.epoch_decay,
        }
        if saved_contract != current_contract:
            raise RuntimeError("scheduler configuration does not match the resumed run")
        self.epoch = int(state.get("epoch", 0))
        self.chunk_start_samples = int(state.get("chunk_start_samples", 0))
        self.chunk_samples = int(state.get("chunk_samples", 1))
        warmup_completed_samples = state.get("warmup_completed_samples")
        self.warmup_completed_samples = (
            None
            if warmup_completed_samples is None
            else int(warmup_completed_samples)
        )
        self.step(samples_seen=int(state.get("samples_seen", 0)))
