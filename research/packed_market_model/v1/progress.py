from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(slots=True)
class PackedProgressState:
    run_name: str
    dataset_id: str
    device: str
    precision: str
    output_dir: str
    model_parameters: int
    max_samples: int = 0
    samples_seen: int = 0
    blocks_seen: int = 0
    loss: float = 0.0
    lr: float = 0.0
    samples_per_second: float = 0.0
    block_seconds: float = 0.0
    loader_wait_seconds: float = 0.0
    gpu_seconds: float = 0.0
    event_count: int = 0
    origin_count: int = 0
    active_task_count: float = 0.0
    loader_state: dict[str, Any] = field(default_factory=dict)
    task_losses: dict[str, float] = field(default_factory=dict)
    validation_metrics: dict[str, float] = field(default_factory=dict)
    last_message: str = ""


class PackedTrainingReporter:
    def __init__(self, *, state: PackedProgressState, layout: str = "auto", refresh_per_second: float = 1.0) -> None:
        self.state = state
        self.layout = layout
        self.refresh_per_second = float(refresh_per_second)
        self.messages: deque[str] = deque(maxlen=8)
        self.started = time.perf_counter()
        self._live: Any | None = None

    def __enter__(self) -> "PackedTrainingReporter":
        if self.layout in {"auto", "rich"}:
            try:
                from rich.console import Console
                from rich.live import Live

                self._live = Live(self._render(), console=Console(), screen=True, auto_refresh=False, refresh_per_second=self.refresh_per_second, transient=False)
                self._live.start()
            except Exception:
                if self.layout == "rich":
                    raise
                self._live = None
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self._live is not None:
            self.refresh(force=True)
            self._live.stop()

    def update(self, metrics: Mapping[str, Any]) -> None:
        s = self.state
        s.samples_seen = int(metrics.get("train/samples_seen_total", s.samples_seen))
        s.blocks_seen = int(metrics.get("train/blocks_seen", s.blocks_seen))
        s.loss = float(metrics.get("train/loss", s.loss))
        s.lr = float(metrics.get("train/learning_rate", s.lr))
        s.samples_per_second = float(metrics.get("train/samples_per_second", s.samples_per_second))
        s.block_seconds = float(metrics.get("train/block_seconds", s.block_seconds))
        s.loader_wait_seconds = float(metrics.get("train/loader_wait_seconds", s.loader_wait_seconds))
        s.gpu_seconds = float(metrics.get("train/gpu_seconds", s.gpu_seconds))
        s.event_count = int(metrics.get("train/event_count", s.event_count))
        s.origin_count = int(metrics.get("train/origin_count", s.origin_count))
        s.active_task_count = float(metrics.get("train/active_task_count", s.active_task_count))
        s.task_losses = {key.replace("train/loss_", ""): float(value) for key, value in metrics.items() if key.startswith("train/loss_")}
        loader_state = {key.replace("loader/", ""): value for key, value in metrics.items() if key.startswith("loader/")}
        if loader_state:
            s.loader_state = loader_state
        self.refresh()

    def validation(self, metrics: Mapping[str, float]) -> None:
        self.state.validation_metrics = dict(metrics)
        self.refresh(force=True)

    def message(self, text: str) -> None:
        self.state.last_message = str(text)
        self.messages.append(f"{time.strftime('%H:%M:%S')} {text}")
        if self._live is not None:
            self.refresh(force=True)
        elif self.layout == "text":
            print(text, flush=True)

    def refresh(self, *, force: bool = False) -> None:
        if self._live is not None:
            self._live.update(self._render(), refresh=True)
        elif self.layout == "text":
            s = self.state
            print(f"samples={s.samples_seen:,} blocks={s.blocks_seen:,} loss={s.loss:.6f} speed={s.samples_per_second:,.1f}/s", flush=True)

    def _render(self) -> Any:
        from rich.console import Group
        from rich.panel import Panel
        from rich.progress import BarColumn, Progress, TextColumn
        from rich.table import Table

        s = self.state
        elapsed = max(0.001, time.perf_counter() - self.started)
        remaining = max(0, int(s.max_samples) - int(s.samples_seen)) if s.max_samples else 0
        eta = remaining / max(s.samples_per_second, 1e-9) if remaining and s.samples_per_second > 0 else 0.0
        progress = Progress(TextColumn("[bold]Samples"), BarColumn(bar_width=None), TextColumn("{task.completed:,.0f}/{task.total:,.0f}"), TextColumn("{task.percentage:>5.1f}%"), expand=True)
        progress.add_task("samples", total=max(s.max_samples, s.samples_seen, 1), completed=min(s.samples_seen, max(s.max_samples, s.samples_seen, 1)))
        summary = Table.grid(expand=False, padding=(0, 4))
        summary.add_column(no_wrap=True)
        summary.add_column(no_wrap=True)
        summary.add_row(f"[bold]Run[/] {s.run_name}", f"[bold]Device[/] {s.device}")
        summary.add_row(f"[bold]Samples[/] {s.samples_seen:,}", f"[bold]Blocks[/] {s.blocks_seen:,}")
        summary.add_row(f"[bold]Loss[/] {s.loss:.6f}", f"[bold]LR[/] {s.lr:.3e}")
        summary.add_row(f"[bold]Speed[/] {s.samples_per_second:,.1f}/s", f"[bold]ETA[/] {_fmt_seconds(eta)}")
        summary.add_row(f"[bold]Events/block[/] {s.event_count:,}", f"[bold]Origins/block[/] {s.origin_count:,}")
        summary.add_row(f"[bold]Block sec[/] {s.block_seconds:.3f}", f"[bold]GPU sec[/] {s.gpu_seconds:.3f}")
        task_table = Table.grid(expand=False, padding=(0, 2))
        task_table.add_column("Task", justify="right", no_wrap=True)
        task_table.add_column("Loss", justify="left", no_wrap=True)
        for key, value in list(s.task_losses.items())[:12]:
            task_table.add_row(key, f"{value:.6f}")
        if not s.task_losses:
            task_table.add_row("waiting", "--")
        loader = Table.grid(expand=False, padding=(0, 2))
        loader.add_column("Metric", justify="right", no_wrap=True)
        loader.add_column("Value", justify="left", no_wrap=True)
        for key, value in list(s.loader_state.items())[:14]:
            loader.add_row(str(key), f"{value}")
        messages = Table.grid(expand=True)
        for msg in list(self.messages)[-8:]:
            messages.add_row(msg)
        return Group(
            Panel(Group(progress, summary), title="Packed Market Training", border_style="cyan"),
            Panel(task_table, title="Grouped Loss", border_style="magenta"),
            Panel(loader, title="Packed Loader State", border_style="green"),
            Panel(messages, title="Messages", border_style="yellow"),
        )


def _fmt_seconds(value: float) -> str:
    seconds = max(0, int(value))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:d}h {minutes:02d}m"
    if minutes:
        return f"{minutes:d}m {secs:02d}s"
    return f"{secs:d}s"
