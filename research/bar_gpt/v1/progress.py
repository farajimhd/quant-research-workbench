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
    origin_bars: int = 0
    warmup_samples: int = 0
    schedule_samples: int = 0
    unit_plans: dict[str, tuple[int, int]] = field(default_factory=dict)
    loss: float = 0.0
    validation_loss: float | None = None
    learning_rate: float = 0.0
    origins_per_second: float = 0.0
    smoothed_origins_per_second: float = 0.0
    loader_wait_seconds: float = 0.0
    gpu_seconds: float = 0.0
    gpu_duty_cycle: float = 0.0
    host_cache_batches: int = 0
    host_cache_capacity: int = 0
    active_tickers: str = "-"
    active_dates: str = "-"
    current_unit_index: int = -1
    current_unit_block: int = 0
    current_unit_blocks: int = 0
    current_unit_origins: int = 0
    current_unit_ticker: str = "-"
    current_unit_month: str = "-"
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
                self._live = Live(self._render(), console=self._console, screen=True, transient=False, auto_refresh=False)
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
            if self._console is not None:
                self._console.print(self._render())
        return False

    def update(
        self,
        metrics: Mapping[str, Any],
        *,
        tickers: tuple[str, ...] = (),
        dates: tuple[str, ...] = (),
        unit_indices: tuple[int, ...] = (),
        block_offsets: tuple[int, ...] = (),
    ) -> None:
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
        if s.origins_per_second > 0:
            s.smoothed_origins_per_second = (
                s.origins_per_second
                if s.smoothed_origins_per_second <= 0
                else 0.15 * s.origins_per_second + 0.85 * s.smoothed_origins_per_second
            )
        s.loader_wait_seconds = float(metrics.get("train/loader_wait_seconds", s.loader_wait_seconds))
        s.gpu_seconds = float(metrics.get("train/gpu_seconds", s.gpu_seconds))
        s.gpu_duty_cycle = float(metrics.get("train/gpu_duty_cycle", s.gpu_duty_cycle))
        s.host_cache_batches = int(metrics.get("train/host_cache_batches", s.host_cache_batches))
        s.host_cache_capacity = int(metrics.get("train/host_cache_capacity", s.host_cache_capacity))
        s.losses = {key.removeprefix("train/loss_"): float(value) for key, value in metrics.items() if key.startswith("train/loss_")}
        if tickers:
            s.active_tickers = ",".join(tickers)
        if dates:
            s.active_dates = f"{min(dates)}..{max(dates)}"
        if unit_indices and block_offsets:
            s.current_unit_index = int(unit_indices[-1])
            s.current_unit_block = int(block_offsets[-1]) + 1
            ticker = tickers[-1] if tickers else "-"
            month = dates[-1][:7] if dates else "-"
            blocks, origins = s.unit_plans.get(f"{ticker}:{month}", (0, 0))
            s.current_unit_ticker = ticker
            s.current_unit_month = month
            s.current_unit_blocks = int(blocks)
            s.current_unit_origins = int(origins)
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
                f"gpu_duty={s.gpu_duty_cycle * 100:.1f}% "
                f"cache={s.host_cache_batches}/{s.host_cache_capacity} batches "
                f"unit={s.current_unit_ticker}:{s.current_unit_month} "
                f"unit_blocks={s.current_unit_block}/{s.current_unit_blocks} "
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
        rate = s.smoothed_origins_per_second or s.origins_per_second
        remaining = max(0, s.max_samples - s.samples_seen) if s.max_samples else 0
        eta = remaining / rate if remaining and rate > 0 else 0.0
        epoch_progress = Progress(
            TextColumn(f"[bold bright_cyan]Epoch {s.epoch_index}/{s.epochs_total}[/] [dim]origins[/]"),
            BarColumn(complete_style="bright_cyan", finished_style="green"),
            TextColumn("{task.completed:,.0f}/{task.total:,.0f}"),
            TextColumn("[bold]{task.percentage:>5.1f}%[/]"), expand=True,
        )
        total = max(s.epoch_origin_budget, s.epoch_origins_seen, 1)
        epoch_progress.add_task("epoch origins", total=total, completed=min(s.epoch_origins_seen, total))
        unit_progress = Progress(
            TextColumn(
                f"[bold green]{s.current_unit_ticker}[/] [green]{s.current_unit_month}[/] [dim]ticker-month blocks[/]"
            ),
            BarColumn(complete_style="green", finished_style="green"),
            TextColumn("{task.completed:,.0f}/{task.total:,.0f}"),
            TextColumn("{task.percentage:>5.1f}%"), expand=True,
        )
        unit_total = max(s.current_unit_blocks, s.current_unit_block, 1)
        unit_progress.add_task(
            "ticker-month blocks",
            total=unit_total,
            completed=min(s.current_unit_block, unit_total),
        )
        unit_remaining_origins = max(0, s.current_unit_blocks - s.current_unit_block) * max(1, s.origin_bars)
        unit_eta = unit_remaining_origins / rate if unit_remaining_origins and rate > 0 else 0.0
        finish_at = time.strftime("%Y-%m-%d %H:%M", time.localtime(time.time() + eta)) if eta else "-"
        schedule_phase, schedule_progress = _schedule_status(s)
        summary = Table.grid(expand=True, padding=(0, 2))
        if width >= 100:
            summary.add_column(); summary.add_column(); summary.add_column()
            summary.add_row(f"[bold]state[/] {s.state}", f"[bold]run[/] {s.run_name}", f"[bold]device[/] {s.device} {s.precision}")
            summary.add_row(f"[bold]loss[/] {s.loss:.6f}", f"[bold]validation[/] {s.validation_loss:.6f}" if s.validation_loss is not None else "[bold]validation[/] -", f"[bold]lr[/] {s.learning_rate:.3e}")
            summary.add_row(f"[bold]speed[/] {rate:,.0f} origins/s", f"[bold]elapsed[/] {_duration(elapsed)}", f"[bold]ETA / finish[/] {_duration(eta) if eta else '-'} / {finish_at}")
            summary.add_row(
                f"[bold]loader wait[/] {s.loader_wait_seconds:.2f}s",
                f"[bold]GPU[/] {s.gpu_seconds:.2f}s ({s.gpu_duty_cycle * 100:.0f}% duty)",
                f"[bold]host cache[/] {s.host_cache_batches}/{s.host_cache_capacity} batches",
            )
            summary.add_row(
                f"[bold]schedule[/] {schedule_phase} {schedule_progress:.1f}%",
                f"[bold]learning rate[/] {s.learning_rate:.3e}",
                f"[bold]unit ETA[/] {_duration(unit_eta) if unit_eta else '-'}",
            )
        else:
            summary.add_column(); summary.add_column()
            summary.add_row(f"[bold]state[/] {s.state}", f"[bold]device[/] {s.device} {s.precision}")
            summary.add_row(f"[bold]loss[/] {s.loss:.6f}", f"[bold]lr[/] {s.learning_rate:.3e}")
            summary.add_row(f"[bold]speed[/] {rate:,.0f}/s", f"[bold]run ETA[/] {_duration(eta) if eta else '-'}")
            summary.add_row(
                f"[bold]GPU duty[/] {s.gpu_duty_cycle * 100:.0f}%",
                f"[bold]cache[/] {s.host_cache_batches}/{s.host_cache_capacity} batches",
            )
            summary.add_row(f"[bold]LR[/] {s.learning_rate:.3e} {schedule_phase}", f"[bold]updates[/] {s.optimizer_steps:,}")
        active = Table.grid(expand=True, padding=(0, 2))
        active.add_column(style="bold", no_wrap=True); active.add_column(ratio=1)
        active.add_row("durable focus", f"{s.current_unit_ticker}  {s.current_unit_month}  block {s.current_unit_block:,}/{s.current_unit_blocks:,}")
        active.add_row("GPU batch", f"{s.active_tickers}  •  {s.active_dates}")
        active.add_row("coverage", f"{s.samples_seen:,}/{s.max_samples:,} origins  •  {s.blocks_seen:,}/{s.planned_blocks:,} blocks  •  {s.units_seen:,}/{s.planned_units:,} units touched")
        active.add_row(
            "pipeline",
            f"RAM cache {s.host_cache_batches}/{s.host_cache_capacity} batches + "
            f"{'CUDA prefetch' if s.cuda_prefetch else 'synchronous device handoff'}",
        )
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
        primary = Panel(Group(epoch_progress, unit_progress, summary), title="BarGPT v1 training", border_style="cyan")
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


def _schedule_status(state: TrainingProgressState) -> tuple[str, float]:
    if state.warmup_samples > 0 and state.samples_seen < state.warmup_samples:
        return "warmup", 100.0 * state.samples_seen / state.warmup_samples
    decay_span = max(1, state.schedule_samples - state.warmup_samples)
    progress = 100.0 * (state.samples_seen - state.warmup_samples) / decay_span
    return "cosine", min(100.0, max(0.0, progress))
