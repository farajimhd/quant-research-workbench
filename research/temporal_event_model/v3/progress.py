from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class TemporalProgressState:
    run_name: str
    dataset_id: str
    device: str
    precision: str
    output_dir: str
    model_parameters: int
    step: int = 0
    epoch: int = 0
    samples_seen: int = 0
    loss: float = 0.0
    active_task_count: float = 0.0
    lr: float = 0.0
    step_seconds: float = 0.0
    samples_per_second: float = 0.0
    loader_wait_seconds: float = 0.0
    gpu_step_seconds: float = 0.0
    materialize_seconds: float = 0.0
    gpu_memory_gib: float = 0.0
    cpu_rss_gib: float = 0.0
    validation_loss: float | None = None
    availability: dict[str, float] = field(default_factory=dict)
    task_losses: dict[str, float] = field(default_factory=dict)
    validation_metrics: dict[str, float] = field(default_factory=dict)
    day_index: int = 0
    day_count: int = 0
    current_day_samples_seen: int = 0
    current_day_sample_count: int = 0
    last_checkpoint: str = ""
    last_message: str = ""


class TemporalTrainingReporter:
    def __init__(self, *, layout: str, state: TemporalProgressState, refresh_per_second: float = 1.0) -> None:
        self.layout = layout
        self.state = state
        self.refresh_per_second = float(refresh_per_second)
        self.min_refresh_interval = 1.0 / max(0.1, self.refresh_per_second)
        self.messages: deque[str] = deque(maxlen=7)
        self.history: deque[float] = deque(maxlen=100)
        self.started = time.perf_counter()
        self._live: Any | None = None
        self._rich = False
        self._last_refresh = 0.0
        self._fallback_reason = ""

    def __enter__(self) -> "TemporalTrainingReporter":
        if self.layout in {"auto", "rich"}:
            try:
                from rich.console import Console
                from rich.live import Live

                self._live = Live(
                    self._render(),
                    console=Console(),
                    refresh_per_second=self.refresh_per_second,
                    transient=False,
                    auto_refresh=False,
                    screen=True,
                    vertical_overflow="visible",
                )
                self._live.start()
                self._rich = True
            except Exception as exc:  # noqa: BLE001
                self._fallback_reason = repr(exc)
                if self.layout == "rich":
                    raise
        if not self._rich and self._fallback_reason:
            print(f"Rich progress unavailable; falling back to text: {self._fallback_reason}", flush=True)
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self._live is not None:
            self.refresh(force=True)
            self._live.stop()

    def update(self, metrics: dict[str, float], *, step: int, validation_metrics: dict[str, float] | None = None) -> None:
        s = self.state
        s.step = int(step)
        s.epoch = int(metrics.get("loader/epoch", s.epoch))
        s.samples_seen = int(metrics.get("train/samples_seen_total", s.samples_seen))
        s.loss = float(metrics.get("train/loss", s.loss))
        s.active_task_count = float(metrics.get("train/active_task_count", s.active_task_count))
        s.lr = float(metrics.get("train/learning_rate", s.lr))
        s.step_seconds = float(metrics.get("train/step_seconds", s.step_seconds))
        s.samples_per_second = float(metrics.get("train/samples_per_second", s.samples_per_second))
        s.loader_wait_seconds = float(metrics.get("train/loader_wait_seconds", s.loader_wait_seconds))
        s.gpu_step_seconds = float(metrics.get("train/gpu_step_seconds", s.gpu_step_seconds))
        s.materialize_seconds = float(metrics.get("train/materialize_seconds", s.materialize_seconds))
        s.gpu_memory_gib = float(metrics.get("train/gpu_memory_allocated_gib", s.gpu_memory_gib))
        s.cpu_rss_gib = float(metrics.get("train/cpu_rss_gib", s.cpu_rss_gib))
        s.task_losses = {key.replace("train/loss_", ""): float(value) for key, value in metrics.items() if key.startswith("train/loss_")}
        s.availability = {key: float(value) for key, value in metrics.items() if key.endswith("_available_fraction") or key.endswith("_valid_fraction")}
        s.day_index = int(metrics.get("schedule/day_index", s.day_index))
        s.day_count = int(metrics.get("schedule/day_count", s.day_count))
        s.current_day_samples_seen = int(metrics.get("schedule/current_day_samples_seen", s.current_day_samples_seen))
        s.current_day_sample_count = int(metrics.get("schedule/current_day_sample_count", s.current_day_sample_count))
        if validation_metrics:
            s.validation_metrics = {key: float(value) for key, value in validation_metrics.items()}
            if "val/loss" in validation_metrics:
                s.validation_loss = float(validation_metrics["val/loss"])
        if s.step_seconds > 0:
            self.history.append(s.step_seconds)
        self.refresh()

    def message(self, text: str) -> None:
        self.state.last_message = str(text)
        self.messages.append(f"{time.strftime('%H:%M:%S')} {text}")
        if self._rich:
            self.refresh(force=True)
        else:
            print(text, flush=True)

    def refresh(self, *, force: bool = False) -> None:
        if self._rich and self._live is not None:
            now = time.perf_counter()
            if not force and now - self._last_refresh < self.min_refresh_interval:
                return
            self._live.update(self._render(), refresh=True)
            self._last_refresh = now
        elif self.layout == "text":
            print(self._text_line(), flush=True)

    def _render(self) -> Any:
        from rich.console import Group
        from rich.panel import Panel
        from rich.table import Table
        from rich.text import Text
        from rich import box

        s = self.state
        summary = Table.grid(expand=True)
        summary.add_column(ratio=1)
        summary.add_column(ratio=1)
        summary.add_row(f"run: {s.run_name}", f"dataset: {s.dataset_id}")
        summary.add_row(f"device: {s.device} {s.precision}", f"params: {s.model_parameters:,}")
        summary.add_row(f"step: {s.step:,}", f"samples: {s.samples_seen:,}")
        summary.add_row(f"days: {s.day_index + 1:,}/{max(s.day_count, 1):,}", f"day samples: {s.current_day_samples_seen:,}/{max(s.current_day_sample_count, 1):,}")
        summary.add_row(f"out: {s.output_dir}", f"elapsed: {_duration(time.perf_counter() - self.started)}")

        losses = Table(title="Loss", box=box.SIMPLE_HEAVY, expand=True)
        losses.add_column("metric")
        losses.add_column("value", justify="right")
        losses.add_row("loss", f"{s.loss:.6f}")
        losses.add_row("active tasks", f"{s.active_task_count:.0f}")
        losses.add_row("lr", f"{s.lr:.3e}")
        for key, value in list(s.task_losses.items())[:10]:
            losses.add_row(key, f"{value:.6f}")

        speed = Table(title="Throughput", box=box.SIMPLE_HEAVY, expand=True)
        speed.add_column("metric")
        speed.add_column("value", justify="right")
        for key, value in (
            ("samples/s", s.samples_per_second),
            ("step sec", s.step_seconds),
            ("loader wait", s.loader_wait_seconds),
            ("gpu step", s.gpu_step_seconds),
            ("materialize", s.materialize_seconds),
            ("gpu GiB", s.gpu_memory_gib),
            ("rss GiB", s.cpu_rss_gib),
        ):
            speed.add_row(key, f"{value:.3f}")

        availability = Table(title="Data Availability", box=box.SIMPLE_HEAVY, expand=True)
        availability.add_column("metric")
        availability.add_column("fraction", justify="right")
        for key, value in list(s.availability.items())[:12]:
            availability.add_row(key.replace("train/", ""), f"{value:.3f}")

        validation = Table(title="Validation", box=box.SIMPLE_HEAVY, expand=True)
        validation.add_column("metric")
        validation.add_column("value", justify="right")
        if s.validation_loss is not None:
            validation.add_row("val/loss", f"{s.validation_loss:.6f}")
        for key, value in list(s.validation_metrics.items())[:12]:
            validation.add_row(key, f"{value:.5f}")

        messages = Text("\n".join(self.messages) if self.messages else s.last_message or "No messages yet")
        return Group(
            Panel(summary, title="Temporal v3", border_style="cyan"),
            Panel(Group(losses, speed), title="Training", border_style="green"),
            Panel(Group(validation, availability), title="Metrics", border_style="magenta"),
            Panel(messages, title="Messages", border_style="yellow"),
        )

    def _text_line(self) -> str:
        s = self.state
        return f"step={s.step:,} loss={s.loss:.5f} samples={s.samples_seen:,} sps={s.samples_per_second:.1f}"


def _duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"
