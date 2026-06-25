from __future__ import annotations

import json
import os
import time
import tracemalloc
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator


def _rss_bytes() -> int:
    try:
        import psutil  # type: ignore

        return int(psutil.Process(os.getpid()).memory_info().rss)
    except Exception:
        return 0


@dataclass(slots=True)
class StageStats:
    calls: int = 0
    seconds: float = 0.0
    items: int = 0
    bytes: int = 0
    peak_tracemalloc_bytes: int = 0
    rss_delta_bytes: int = 0

    def add(self, *, seconds: float, items: int = 0, bytes_count: int = 0, peak_bytes: int = 0, rss_delta: int = 0) -> None:
        self.calls += 1
        self.seconds += float(seconds)
        self.items += int(items)
        self.bytes += int(bytes_count)
        self.peak_tracemalloc_bytes = max(int(self.peak_tracemalloc_bytes), int(peak_bytes))
        self.rss_delta_bytes += int(rss_delta)

    def to_dict(self) -> dict[str, Any]:
        rate = self.items / self.seconds if self.seconds > 0 and self.items else 0.0
        mbps = (self.bytes / (1024 * 1024)) / self.seconds if self.seconds > 0 and self.bytes else 0.0
        return {
            "calls": self.calls,
            "seconds": self.seconds,
            "items": self.items,
            "items_per_sec": rate,
            "bytes": self.bytes,
            "mib_per_sec": mbps,
            "peak_tracemalloc_mib": self.peak_tracemalloc_bytes / (1024 * 1024),
            "rss_delta_mib": self.rss_delta_bytes / (1024 * 1024),
        }


@dataclass(slots=True)
class RollingLoaderProfiler:
    """Small profiler designed for the rolling loader's cache stages."""

    enabled: bool = True
    stages: dict[str, StageStats] = field(default_factory=lambda: defaultdict(StageStats))
    counters: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    started_at: float = field(default_factory=time.perf_counter)

    def incr(self, name: str, value: int = 1) -> None:
        self.counters[name] += int(value)

    @contextmanager
    def stage(self, name: str, *, items: int = 0, bytes_count: int = 0) -> Iterator[None]:
        if not self.enabled:
            yield
            return
        if not tracemalloc.is_tracing():
            tracemalloc.start()
        rss_before = _rss_bytes()
        current_before, peak_before = tracemalloc.get_traced_memory()
        started = time.perf_counter()
        try:
            yield
        finally:
            seconds = time.perf_counter() - started
            rss_after = _rss_bytes()
            current_after, peak_after = tracemalloc.get_traced_memory()
            peak_delta = max(0, int(peak_after) - int(peak_before), int(current_after) - int(current_before))
            self.stages[name].add(
                seconds=seconds,
                items=items,
                bytes_count=bytes_count,
                peak_bytes=peak_delta,
                rss_delta=int(rss_after) - int(rss_before) if rss_before and rss_after else 0,
            )

    def snapshot(self) -> dict[str, Any]:
        elapsed = time.perf_counter() - self.started_at
        return {
            "elapsed_seconds": elapsed,
            "rss_mib": _rss_bytes() / (1024 * 1024),
            "counters": dict(sorted(self.counters.items())),
            "stages": {key: value.to_dict() for key, value in sorted(self.stages.items())},
        }


def write_profile_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def format_profile_table(payload: dict[str, Any]) -> str:
    lines = [
        f"elapsed={payload.get('elapsed_seconds', 0.0):.3f}s rss={payload.get('rss_mib', 0.0):.1f}MiB",
        "stages:",
    ]
    for name, stats in payload.get("stages", {}).items():
        lines.append(
            "  "
            + f"{name:<30} calls={stats['calls']:<5} seconds={stats['seconds']:<9.3f} "
            + f"items={stats['items']:<12} rate={stats['items_per_sec']:<10.1f} "
            + f"bytes={stats['bytes']:<12} rss_delta={stats['rss_delta_mib']:.1f}MiB"
        )
    lines.append("counters:")
    for name, value in payload.get("counters", {}).items():
        lines.append(f"  {name:<30} {value}")
    return "\n".join(lines)
