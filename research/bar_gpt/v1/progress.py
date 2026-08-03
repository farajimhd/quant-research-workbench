from __future__ import annotations

import sys
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(slots=True)
class TrainingProgressState:
    run_name: str
    device: str
    precision: str
    output_dir: str
    model_parameters: int
    max_samples: int
    epochs_total: int = 1
    epoch_index: int = 1
    epoch_start_origins: int = 0
    epoch_origin_budget: int = 0
    epoch_origins_seen: int = 0
    state: str = "starting"
    samples_seen: int = 0
    batches_seen: int = 0
    optimizer_steps: int = 0
    blocks_seen: int = 0
    units_seen: int = 0
    condition_blocks_seen: int = 0
    planned_units: int = 0
    planned_blocks: int = 0
    gradient_accumulation_steps: int = 1
    cuda_prefetch: bool = False
    loss: float = 0.0
    validation_loss: float | None = None
    learning_rate: float = 0.0
    origins_per_second: float = 0.0
    loader_wait_seconds: float = 0.0
    gpu_seconds: float = 0.0
    active_tickers: str = "-"
    active_dates: str = "-"
    last_checkpoint: str = "-"
    losses: dict[str, float] = field(default_factory=dict)
    last_message: str = ""


class TrainingReporter:
    def __init__(self, state: TrainingProgressState, *, layout: str = "auto") -> None:
        self.state = state
        self.layout = layout
        self.started = time.perf_counter()
        self.messages: deque[str] = deque(maxlen=6)
        self._live: Any | None = None
        self._console: Any | None = None
        self._last_text = 0.0

    def __enter__(self) -> "TrainingReporter":
        use_rich = self.layout == "rich" or (self.layout == "auto" and sys.stdout.isatty())
        if use_rich:
            try:
                from rich.console import Console
                from rich.live import Live

                self._console = Console()
                self._live = Live(self._render(), console=self._console, screen=False, transient=False, auto_refresh=False)
                self._live.start()
            except Exception:
                if self.layout == "rich":
                    raise
        self.state.state = "running"
        self.message("Training initialized")
        return self

    def __exit__(self, exc_type: object, exc: object, _tb: object) -> bool:
        if exc_type is KeyboardInterrupt:
            self.state.state = "interrupted"
            self.message("Interrupt received; checkpointing durable state")
        elif exc is not None:
            self.state.state = "failed"
            self.message(str(exc))
        elif self.state.state == "running":
            self.state.state = "completed"
        self.refresh(force=True)
        if self._live is not None:
            self._live.stop()
        return False

    def update(self, metrics: Mapping[str, Any], *, tickers: tuple[str, ...] = (), dates: tuple[str, ...] = ()) -> None:
        s = self.state
        s.samples_seen = int(metrics.get("train/samples_seen", s.samples_seen))
        s.epoch_origins_seen = max(0, s.samples_seen - s.epoch_start_origins)
        s.batches_seen = int(metrics.get("train/batches_seen", s.batches_seen))
        s.optimizer_steps = int(metrics.get("train/optimizer_steps", s.optimizer_steps))
        s.blocks_seen = int(metrics.get("train/blocks_seen", s.blocks_seen))
        s.units_seen = int(metrics.get("train/units_seen", s.units_seen))
        s.condition_blocks_seen = int(metrics.get("train/condition_blocks_seen", s.condition_blocks_seen))
        s.loss = float(metrics.get("train/loss", s.loss))
        s.learning_rate = float(metrics.get("train/learning_rate", s.learning_rate))
        s.origins_per_second = float(metrics.get("train/origins_per_second", s.origins_per_second))
        s.loader_wait_seconds = float(metrics.get("train/loader_wait_seconds", s.loader_wait_seconds))
        s.gpu_seconds = float(metrics.get("train/gpu_seconds", s.gpu_seconds))
        s.losses = {key.removeprefix("train/loss_"): float(value) for key, value in metrics.items() if key.startswith("train/loss_")}
        if tickers:
            s.active_tickers = ",".join(tickers)
        if dates:
            s.active_dates = f"{min(dates)}..{max(dates)}"
        self.refresh()

    def epoch(self, index: int, start_origins: int) -> None:
        self.state.epoch_index = int(index)
        self.state.epoch_start_origins = int(start_origins)
        self.state.epoch_origins_seen = max(0, self.state.samples_seen - self.state.epoch_start_origins)
        self.message(f"Epoch {self.state.epoch_index}/{self.state.epochs_total} started")

    def validation(self, loss: float) -> None:
        self.state.validation_loss = float(loss)
        self.message(f"Validation completed: loss={loss:.6f}")

    def checkpoint(self, path: str) -> None:
        self.state.last_checkpoint = path
        self.message(f"Checkpoint scheduled: {path}")

    def message(self, text: str) -> None:
        self.state.last_message = str(text)
        line = f"{time.strftime('%H:%M:%S')} {text}"
        self.messages.append(line)
        if self._live is not None:
            self.refresh(force=True)
        elif self.layout != "none":
            print(line, flush=True)

    def refresh(self, *, force: bool = False) -> None:
        if self._live is not None:
            self._live.update(self._render(), refresh=True)
            return
        if self.layout == "none":
            return
        now = time.monotonic()
        if force or now - self._last_text >= 15.0:
            self._last_text = now
            s = self.state
            print(
                f"state={s.state} epoch={s.epoch_index}/{s.epochs_total} "
                f"epoch_origins={s.epoch_origins_seen:,}/{s.epoch_origin_budget:,} "
                f"run_origins={s.samples_seen:,}/{s.max_samples:,} steps={s.optimizer_steps:,} "
                f"blocks={s.blocks_seen:,}/{s.planned_blocks:,} units={s.units_seen:,}/{s.planned_units:,} "
                f"loss={s.loss:.6f} speed={s.origins_per_second:,.1f}/s "
                f"active={s.active_tickers} dates={s.active_dates}",
                flush=True,
            )

    def _render(self) -> Any:
        from rich.console import Group
        from rich.panel import Panel
        from rich.progress import BarColumn, Progress, TextColumn
        from rich.table import Table

        s = self.state
        width = self._console.size.width if self._console is not None else 120
        height = self._console.size.height if self._console is not None else 40
        elapsed = max(1e-9, time.perf_counter() - self.started)
        remaining = max(0, s.max_samples - s.samples_seen) if s.max_samples else 0
        eta = remaining / s.origins_per_second if remaining and s.origins_per_second > 0 else 0.0
        progress = Progress(
            TextColumn(f"[bold]epoch {s.epoch_index}/{s.epochs_total} origin budget"), BarColumn(), TextColumn("{task.completed:,.0f}/{task.total:,.0f}"),
            TextColumn("{task.percentage:>5.1f}%"), expand=True,
        )
        total = max(s.epoch_origin_budget, s.epoch_origins_seen, 1)
        progress.add_task("epoch origins", total=total, completed=min(s.epoch_origins_seen, total))
        summary = Table.grid(expand=True, padding=(0, 2))
        if width >= 100:
            summary.add_column(); summary.add_column(); summary.add_column()
            summary.add_row(f"[bold]state[/] {s.state}", f"[bold]run[/] {s.run_name}", f"[bold]device[/] {s.device} {s.precision}")
            summary.add_row(f"[bold]loss[/] {s.loss:.6f}", f"[bold]validation[/] {s.validation_loss:.6f}" if s.validation_loss is not None else "[bold]validation[/] -", f"[bold]lr[/] {s.learning_rate:.3e}")
            summary.add_row(f"[bold]speed[/] {s.origins_per_second:,.1f}/s", f"[bold]elapsed[/] {_duration(elapsed)}", f"[bold]run ETA[/] {_duration(eta) if eta else '-'}")
            summary.add_row(f"[bold]loader wait[/] {s.loader_wait_seconds:.2f}s", f"[bold]GPU[/] {s.gpu_seconds:.2f}s", f"[bold]parameters[/] {s.model_parameters:,}")
            summary.add_row(
                f"[bold]run origins[/] {s.samples_seen:,}/{s.max_samples:,}",
                f"[bold]blocks[/] {s.blocks_seen:,}/{s.planned_blocks:,} planned",
                f"[bold]updates[/] {s.optimizer_steps:,} ({s.gradient_accumulation_steps} micro/update)",
            )
        else:
            summary.add_column(); summary.add_column()
            summary.add_row(f"[bold]state[/] {s.state}", f"[bold]device[/] {s.device} {s.precision}")
            summary.add_row(f"[bold]loss[/] {s.loss:.6f}", f"[bold]lr[/] {s.learning_rate:.3e}")
            summary.add_row(f"[bold]speed[/] {s.origins_per_second:,.1f}/s", f"[bold]run ETA[/] {_duration(eta) if eta else '-'}")
            summary.add_row(f"[bold]run origins[/] {s.samples_seen:,}/{s.max_samples:,}", f"[bold]updates[/] {s.optimizer_steps:,}")
        active = Table.grid(expand=True, padding=(0, 2))
        active.add_column(style="bold", no_wrap=True); active.add_column(ratio=1)
        active.add_row("tickers", s.active_tickers)
        active.add_row("dates", s.active_dates)
        active.add_row("pipeline", f"CPU queue + {'CUDA prefetch' if s.cuda_prefetch else 'synchronous device handoff'}")
        active.add_row("condition blocks", f"{s.condition_blocks_seen:,}")
        active.add_row("checkpoint", s.last_checkpoint)
        active.add_row("output", s.output_dir)
        losses = Table.grid(expand=True, padding=(0, 2))
        losses.add_column(); losses.add_column(justify="right")
        for key, value in list(s.losses.items())[:8]:
            losses.add_row(key, f"{value:.6f}")
        if not s.losses:
            losses.add_row("waiting for first batch", "-")
        message_limit = 1 if height < 22 else (3 if height < 30 else 6)
        messages = "\n".join(list(self.messages)[-message_limit:]) if self.messages else "No messages"
        primary = Panel(Group(progress, summary), title="BarGPT v1 training", border_style="cyan")
        current = Panel(active, title="Current work and durability", border_style="green")
        recent = Panel(messages, title="Recent messages", border_style="yellow")
        if height < 22:
            return Group(primary, recent)
        if height < 30:
            return Group(primary, current, recent)
        return Group(
            primary,
            current,
            Panel(losses, title="Objectives", border_style="magenta"),
            recent,
        )


def _duration(value: float) -> str:
    seconds = max(0, int(value))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:d}h {minutes:02d}m" if hours else (f"{minutes:d}m {secs:02d}s" if minutes else f"{secs:d}s")
