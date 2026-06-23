from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator


@dataclass(slots=True)
class StageTimer:
    name: str
    seconds: float = 0.0
    count: int = 0

    def add(self, seconds: float, count: int = 0) -> None:
        self.seconds += float(seconds)
        self.count += int(count)


@dataclass(slots=True)
class DataPrepProfile:
    provider_name: str
    batch_id: int = 0
    stages: dict[str, StageTimer] = field(default_factory=dict)
    rows_read: int = 0
    valid_rows: int = 0
    chunks_created: int = 0
    encoder_batches_created: int = 0
    embeddings_created: int = 0
    samples_created: int = 0
    labels_created: int = 0
    output_batches_created: int = 0
    total_seconds: float = 0.0

    def stage_seconds(self, name: str) -> float:
        timer = self.stages.get(name)
        return 0.0 if timer is None else float(timer.seconds)

    def samples_per_second(self) -> float:
        return float(self.samples_created / self.total_seconds) if self.total_seconds > 0 else 0.0

    def batches_per_second(self) -> float:
        return float(self.output_batches_created / self.total_seconds) if self.total_seconds > 0 else 0.0

    def to_metrics(self, prefix: str = "data") -> dict[str, float]:
        metrics = {
            f"{prefix}/rows_read": float(self.rows_read),
            f"{prefix}/valid_rows": float(self.valid_rows),
            f"{prefix}/chunks_created": float(self.chunks_created),
            f"{prefix}/encoder_batches_created": float(self.encoder_batches_created),
            f"{prefix}/embeddings_created": float(self.embeddings_created),
            f"{prefix}/samples_created": float(self.samples_created),
            f"{prefix}/labels_created": float(self.labels_created),
            f"{prefix}/output_batches_created": float(self.output_batches_created),
            f"{prefix}/total_seconds": float(self.total_seconds),
            f"{prefix}/samples_per_second": self.samples_per_second(),
            f"{prefix}/batches_per_second": self.batches_per_second(),
        }
        for name, timer in self.stages.items():
            metrics[f"{prefix}/{name}_seconds"] = float(timer.seconds)
            if timer.count:
                metrics[f"{prefix}/{name}_count"] = float(timer.count)
        return metrics


class DataPrepProfiler:
    """Cheap per-batch/block data-preparation profiler.

    The profiler aggregates stage timings. It never logs per-row data and is
    safe to leave enabled in long training runs.
    """

    def __init__(self, provider_name: str, *, batch_id: int = 0, enabled: bool = True) -> None:
        self.profile = DataPrepProfile(provider_name=provider_name, batch_id=int(batch_id))
        self.enabled = bool(enabled)
        self._start = time.perf_counter()

    @contextmanager
    def stage(self, name: str, *, count: int = 0) -> Iterator[None]:
        if not self.enabled:
            yield
            return
        started = time.perf_counter()
        try:
            yield
        finally:
            self.add_stage(name, time.perf_counter() - started, count=count)

    def add_stage(self, name: str, seconds: float, *, count: int = 0) -> None:
        timer = self.profile.stages.setdefault(name, StageTimer(name=name))
        timer.add(seconds, count=count)

    def finish(self) -> DataPrepProfile:
        self.profile.total_seconds = time.perf_counter() - self._start
        return self.profile


def aggregate_profiles(profiles: list[DataPrepProfile], *, provider_name: str = "aggregate") -> DataPrepProfile:
    out = DataPrepProfile(provider_name=provider_name)
    if not profiles:
        return out
    out.batch_id = profiles[-1].batch_id
    for profile in profiles:
        out.rows_read += profile.rows_read
        out.valid_rows += profile.valid_rows
        out.chunks_created += profile.chunks_created
        out.encoder_batches_created += profile.encoder_batches_created
        out.embeddings_created += profile.embeddings_created
        out.samples_created += profile.samples_created
        out.labels_created += profile.labels_created
        out.output_batches_created += profile.output_batches_created
        out.total_seconds += profile.total_seconds
        for name, timer in profile.stages.items():
            out.stages.setdefault(name, StageTimer(name=name)).add(timer.seconds, timer.count)
    return out

