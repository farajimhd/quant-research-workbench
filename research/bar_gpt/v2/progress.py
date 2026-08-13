from __future__ import annotations

import math
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence


TRAINING_OBJECTIVES: tuple[tuple[str, str, str], ...] = (
    ("Total", "train/loss", "loss"),
    ("Autoregressive", "train/loss_autoregressive", "loss"),
    ("Horizon", "train/loss_horizon", "loss"),
    ("AR regression", "train/loss_ar_regression", "loss"),
    ("AR categorical", "train/loss_ar_categorical", "loss"),
    ("AR return class", "train/loss_ar_return_class", "loss"),
    ("Horizon quantile", "train/loss_horizon_quantile", "loss"),
    ("Horizon categorical", "train/loss_horizon_categorical", "loss"),
    ("Gradient norm", "train/gradient_norm", "number"),
    ("Horizon return class", "train/loss_horizon_return_class", "loss"),
    ("Trade OHLC MAE", "train_trade_summary/mae_bps_macro", "bps"),
    ("Close balanced", "train_close_return_class_summary/balanced_accuracy_macro", "percent"),
    ("Close MCC", "train_close_return_class_summary/mcc_macro", "number"),
    ("AR close balanced", "train_ar_close_return_class_summary/balanced_accuracy_macro", "percent"),
    ("AR close MCC", "train_ar_close_return_class_summary/mcc_macro", "number"),
)

VALIDATION_SCORECARD: tuple[tuple[str, str, str], ...] = (
    ("Total loss", "validation_loss/total", "loss"),
    ("AR loss", "validation_loss/autoregressive", "loss"),
    ("Horizon loss", "validation_loss/horizon", "loss"),
    ("Trade OHLC MAE", "validation_trade_summary/mae_bps_macro", "bps"),
    ("Close balanced", "validation_close_return_class_summary/balanced_accuracy_macro", "percent"),
    ("Close MCC", "validation_close_return_class_summary/mcc_macro", "number"),
    ("Trade close MCC", "validation_trade_close_return_class_summary/mcc_macro", "number"),
    ("AR close accuracy", "validation_ar_close_return_class_summary/accuracy_macro", "percent"),
    ("AR close balanced", "validation_ar_close_return_class_summary/balanced_accuracy_macro", "percent"),
    ("AR close MCC", "validation_ar_close_return_class_summary/mcc_macro", "number"),
    ("Availability Brier", "validation_availability/brier_macro", "number"),
    ("Trade rank", "validation_trade_summary/rank_macro", "number"),
)


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
    gradient_norm: float | None = None
    amp_scale: float | None = None
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
    validation_runs_completed: int = 0
    validation_runs_total: int = 0
    next_validation_origins: int = 0
    checkpoint_stage_seconds: float | None = None
    last_checkpoint: str = "-"
    losses: dict[str, float] = field(default_factory=dict)
    eligibility: dict[str, float] = field(default_factory=dict)
    validation_metrics: dict[str, float] = field(default_factory=dict)
    training_metrics: dict[str, float] = field(default_factory=dict)
    last_message: str = ""


class TrainingReporter:
    """Low-overhead training status with a fixed Rich dashboard schema."""

    def __init__(self, state: TrainingProgressState, *, layout: str = "auto") -> None:
        self.state = state
        self.layout = layout
        self.started = time.perf_counter()
        self.messages: deque[str] = deque(maxlen=4)
        self._live: Any | None = None
        self._console: Any | None = None
        self._last_text = 0.0
        self._last_rich = 0.0
        self._rich_refresh_seconds = 0.5

    def __enter__(self) -> "TrainingReporter":
        use_rich = self.layout == "rich" or (self.layout == "auto" and sys.stdout.isatty())
        if use_rich:
            try:
                from rich.console import Console
                from rich.live import Live

                self._console = Console()
                self._live = Live(
                    self.render(), console=self._console, screen=True, transient=False, auto_refresh=False
                )
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
            live = self._live
            live.stop()
            if self._console is not None:
                self._console.print(self.render())
            self._live = None
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
        if "train/gradient_norm" in metrics:
            s.gradient_norm = float(metrics["train/gradient_norm"])
        if "train/amp_scale" in metrics:
            s.amp_scale = float(metrics["train/amp_scale"])
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
        for key, value in metrics.items():
            if key.startswith("train/loss_"):
                s.losses[key.removeprefix("train/loss_")] = float(value)
        if "train/condition_positive_rate" in metrics:
            s.eligibility["condition_positive_rate"] = float(metrics["train/condition_positive_rate"])
        for key, value in metrics.items():
            if key.startswith((
                "train_trade_",
                "train_bid_",
                "train_ask_",
                "train_close_return_class_",
                "train_ar_",
                "train_availability/",
                "train_coverage_",
            )):
                s.training_metrics[str(key)] = float(value)
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

    def phase(self, value: str) -> None:
        self.state.state = str(value)
        self.refresh(force=True)

    def validation(self, metrics: Mapping[str, float]) -> None:
        self.state.validation_metrics = {str(key): float(value) for key, value in metrics.items()}
        if "validation_loss/total" not in self.state.validation_metrics:
            self.state.validation_metrics.update({
                "validation_" + key.removeprefix("monitor_"): value
                for key, value in tuple(self.state.validation_metrics.items())
                if key.startswith("monitor_")
            })
        loss = self.state.validation_metrics.get("validation_loss/total")
        monitor_loss = self.state.validation_metrics.get("monitor_loss/total")
        self.state.validation_loss = loss
        self.state.validation_runs_completed += 1
        if loss is not None:
            self.message(f"Validation completed: loss={loss:.6f}")
        elif monitor_loss is not None:
            self.message(f"Monitor completed: loss={monitor_loss:.6f}")
        else:
            self.message("Evaluation completed: metrics recorded")

    def schedule_validation(self, next_origins: int) -> None:
        self.state.next_validation_origins = max(0, int(next_origins))
        self.refresh()

    def checkpoint(self, path: str) -> None:
        self.state.last_checkpoint = path
        self.message(f"Checkpoint scheduled: {path}")

    def message(self, text: str) -> None:
        self.state.last_message = str(text)
        if str(text).startswith("Saved checkpoint ") and ": " in str(text):
            self.state.last_checkpoint = str(text).split(": ", 1)[1]
        line = f"{time.strftime('%H:%M:%S')} {text}"
        self.messages.append(line)
        if self._live is not None:
            self.refresh(force=True)
        elif self.layout != "none":
            print(line, flush=True)

    def refresh(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if self._live is not None:
            if not force and now - self._last_rich < self._rich_refresh_seconds:
                return
            self._last_rich = now
            self._live.update(self.render(), refresh=True)
            return
        if self.layout == "none":
            return
        if force or now - self._last_text >= 15.0:
            self._last_text = now
            s = self.state
            print(
                f"state={s.state} epoch={s.epoch_index}/{s.epochs_total} "
                f"epoch_origins={s.epoch_origins_seen:,}/{s.epoch_origin_budget:,} "
                f"run_origins={s.samples_seen:,}/{s.max_samples:,} steps={s.optimizer_steps:,} "
                f"blocks={s.blocks_seen:,}/{s.planned_blocks:,} units={s.units_seen:,}/{s.planned_units:,} "
                f"loss={s.loss:.6f} validation={_format_value(s.validation_loss, 'loss')} "
                f"speed={s.origins_per_second:,.1f}/s gpu_duty={s.gpu_duty_cycle * 100:.1f}% "
                f"cache={s.host_cache_batches}/{s.host_cache_capacity} batches "
                f"unit={s.current_unit_ticker}:{s.current_unit_month} "
                f"unit_blocks={s.current_unit_block}/{s.current_unit_blocks} "
                f"active={s.active_tickers} dates={s.active_dates}",
                flush=True,
            )

    def render(self) -> Any:
        """Build the complete dashboard; the panel schema never depends on terminal size."""
        from rich.layout import Layout
        from rich.panel import Panel
        from rich.progress import BarColumn, Progress, TextColumn
        from rich.table import Table

        s = self.state
        elapsed = max(0.0, time.perf_counter() - self.started)
        rate = s.smoothed_origins_per_second or s.origins_per_second
        remaining = max(0, s.max_samples - s.samples_seen) if s.max_samples else 0
        eta = remaining / rate if remaining and rate > 0 else 0.0
        finish_at = time.strftime("%Y-%m-%d %H:%M", time.localtime(time.time() + eta)) if eta else "-"
        unit_remaining = max(0, s.current_unit_blocks - s.current_unit_block) * max(1, s.origin_bars)
        unit_eta = unit_remaining / rate if unit_remaining and rate > 0 else 0.0
        schedule_phase, schedule_progress = _schedule_status(s)

        header = Table.grid(expand=True, padding=(0, 1))
        header.add_column(no_wrap=True); header.add_column(no_wrap=True)
        header.add_column(no_wrap=True); header.add_column(no_wrap=True)
        header.add_row(
            f"[bold]State[/] {_safe(s.state)}",
            f"[bold]Run[/] {_safe(s.run_name)}",
            f"[bold]Device[/] {_safe(s.device)} / {_safe(s.precision)}",
            f"[bold]Overall speed[/] [bold bright_green]{rate:,.0f}[/] [dim]origins/s[/]",
        )
        header.add_row(
            f"[bold]Model[/] {s.model_parameters:,} [dim]parameters[/]",
            f"[bold]Elapsed[/] {_duration(elapsed)}",
            f"[bold]Output[/] {_safe(s.output_dir)}",
            f"[bold]GPU duty[/] {s.gpu_duty_cycle * 100:.2f}[dim]%[/]",
        )

        def progress_line(label: str, completed: int, total: int, color: str) -> Any:
            progress = Progress(
                TextColumn(f"[bold {color}]{label:<12}[/]"),
                BarColumn(complete_style=color, finished_style="green", bar_width=None),
                TextColumn(_ratio_markup(completed, total)),
                TextColumn("{task.percentage:>3.0f}[dim]%[/]"),
                expand=True,
            )
            visual_total = max(total, completed, 1)
            progress.add_task(label, total=visual_total, completed=min(completed, visual_total))
            return progress

        progress_group = Table.grid(expand=True)
        progress_group.add_column()
        progress_group.add_row(progress_line("Epoch", s.epoch_origins_seen, s.epoch_origin_budget, "bright_cyan"))
        progress_group.add_row(progress_line("Full run", s.samples_seen, s.max_samples, "blue"))
        progress_group.add_row(progress_line("Ticker-month", s.current_unit_block, s.current_unit_blocks, "green"))
        eta_table = Table.grid(padding=(0, 2))
        eta_table.add_column(); eta_table.add_column(); eta_table.add_column(); eta_table.add_column()
        eta_table.add_row(
            f"[bold]Epoch[/] {_ratio_markup(s.epoch_index, s.epochs_total)}",
            f"[bold]Run ETA[/] {_duration(eta) if eta else '-'}",
            f"[bold]Finish[/] {finish_at}",
            f"[bold]Unit ETA[/] {_duration(unit_eta) if unit_eta else '-'}",
        )
        progress_group.add_row(eta_table)

        train_values: dict[str, float | None] = {"train/loss": s.loss}
        train_values.update({f"train/loss_{key}": value for key, value in s.losses.items()})
        train_values["train/gradient_norm"] = s.gradient_norm
        train_values["train/condition_positive_rate"] = s.eligibility.get("condition_positive_rate")
        train_values.update(s.training_metrics)
        objective_rows = [(label, train_values.get(key), style) for label, key, style in TRAINING_OBJECTIVES]
        validation_rows = [
            (label, s.validation_metrics.get(key), style) for label, key, style in VALIDATION_SCORECARD
        ]

        runtime_rows = (
            ("Learning rate", s.learning_rate, "scientific"),
            ("Schedule", schedule_phase, "text"),
            ("Schedule progress", schedule_progress / 100.0, "percent"),
            ("AMP scale", s.amp_scale, "number"),
            ("Accumulation", s.gradient_accumulation_steps, "integer"),
            ("Optimizer updates", s.optimizer_steps, "integer"),
            ("Current rate", s.origins_per_second, "rate"),
            ("Smoothed rate", s.smoothed_origins_per_second, "rate"),
            ("Loader wait", s.loader_wait_seconds, "seconds"),
            ("GPU time", s.gpu_seconds, "seconds"),
            ("GPU duty", s.gpu_duty_cycle, "percent"),
            ("Host cache", (s.host_cache_batches, s.host_cache_capacity, " batches"), "ratio"),
            ("CUDA prefetch", "on" if s.cuda_prefetch else "off", "text"),
            ("Microbatches", s.batches_seen, "integer"),
        )
        checkpoint = Path(s.last_checkpoint).name if s.last_checkpoint not in ("", "-") else "-"
        data_rows = (
            ("Origins", (s.samples_seen, s.max_samples, ""), "ratio"),
            ("Blocks", (s.blocks_seen, s.planned_blocks, ""), "ratio"),
            ("Units touched", (s.units_seen, s.planned_units, ""), "ratio"),
            ("Condition blocks", s.condition_blocks_seen, "integer"),
            ("Active tickers", s.active_tickers, "text"),
            ("Active dates", s.active_dates, "text"),
            ("Current unit", f"{s.current_unit_ticker}:{s.current_unit_month}", "text"),
            ("Unit block", (s.current_unit_block, s.current_unit_blocks, ""), "ratio"),
            ("Unit origins", s.current_unit_origins, "integer"),
            ("Validations", (s.validation_runs_completed, s.validation_runs_total, ""), "ratio"),
            ("Next validation", s.next_validation_origins, "integer"),
            ("Checkpoint", checkpoint, "text"),
            ("Origin block", s.origin_bars, "integer"),
            ("Checkpoint staging", s.checkpoint_stage_seconds, "seconds"),
        )

        messages = list(self.messages)
        messages.extend(["-"] * (4 - len(messages)))
        message_table = Table.grid(expand=True)
        message_table.add_column()
        for message in messages[:4]:
            message_table.add_row(message)

        root = Layout(name="root")
        root.split_column(
            Layout(Panel(header, title="BarGPT v2 training", border_style="cyan", padding=(0, 0)), name="header", size=4),
            Layout(Panel(progress_group, title="Progress and ETA", border_style="cyan", padding=(0, 0)), name="progress", size=7),
            Layout(name="scores", size=11),
            Layout(name="operations", size=9),
            Layout(Panel(message_table, title="Recent events", border_style="yellow", padding=(0, 0)), name="messages", minimum_size=6),
        )
        root["scores"].split_row(
            Layout(Panel(_paired_table(objective_rows), title="Training loss and metrics", border_style="magenta", padding=(0, 0))),
            Layout(Panel(_paired_table(validation_rows), title="Validation scorecard", border_style="bright_magenta", padding=(0, 0))),
        )
        root["operations"].split_row(
            Layout(Panel(_paired_table(runtime_rows), title="Optimization and runtime", border_style="blue", padding=(0, 0))),
            Layout(Panel(_paired_table(data_rows), title="Data and durability", border_style="green", padding=(0, 0))),
        )
        return root

    # Retain the private name for callers/tests from the earlier reporter.
    def _render(self) -> Any:
        return self.render()


def _paired_table(rows: Sequence[tuple[str, Any, str]]) -> Any:
    from rich.table import Table

    midpoint = (len(rows) + 1) // 2
    table = Table.grid(expand=True, padding=(0, 0))
    table.add_column(ratio=1)
    table.add_column(ratio=1)
    table.add_row(_metric_column(rows[:midpoint]), _metric_column(rows[midpoint:]))
    return table


def _metric_column(rows: Sequence[tuple[str, Any, str]]) -> Any:
    """Render one exact half-panel column with a two-cell left inset."""
    from rich.table import Table

    column = Table.grid(expand=True, padding=(0, 0))
    column.add_column(width=2)
    column.add_column(style="dim", no_wrap=True)
    column.add_column(justify="right", no_wrap=True)
    column.add_column(ratio=1)
    for label, value, style in rows:
        column.add_row("", label + " ", _format_value(value, style), "")
    return column


def _format_value(value: Any, style: str) -> str:
    if value is None:
        return "-"
    if style == "text":
        return _safe(str(value))
    if style == "ratio":
        numerator, denominator, suffix = value
        return _ratio_markup(int(numerator), int(denominator), str(suffix))
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return _safe(str(value))
    if not math.isfinite(numeric):
        return "-"
    if style == "loss":
        return f"{numeric:.6f}"
    if style == "percent":
        return f"{numeric * 100:.2f}[dim]%[/]"
    if style == "bps":
        return f"{numeric:.3f} [dim]bps[/]"
    if style == "integer":
        return f"{int(numeric):,}"
    if style == "scientific":
        return f"{numeric:.3e}"
    if style == "rate":
        return f"{numeric:,.0f} [dim]origins/s[/]"
    if style == "seconds":
        return f"{numeric:.3f}[dim]s[/]"
    return f"{numeric:.4f}"


def _ratio_markup(numerator: int, denominator: int, suffix: str = "") -> str:
    from rich.markup import escape

    return (
        f"[bold bright_cyan]{numerator:,}[/]"
        f"[bold bright_yellow]/[/]"
        f"[bold bright_magenta]{denominator:,}[/]"
        f"[dim]{escape(suffix)}[/]"
    )


def _safe(value: str) -> str:
    from rich.markup import escape

    return escape(value or "-")


def _duration(value: float) -> str:
    seconds = max(0, int(value))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return (
        f"{hours:d}[dim]h[/] {minutes:02d}[dim]m[/]"
        if hours
        else (
            f"{minutes:d}[dim]m[/] {secs:02d}[dim]s[/]"
            if minutes
            else f"{secs:d}[dim]s[/]"
        )
    )


def _schedule_status(state: TrainingProgressState) -> tuple[str, float]:
    if state.warmup_samples > 0 and state.samples_seen < state.warmup_samples:
        return "warmup", 100.0 * state.samples_seen / state.warmup_samples
    decay_span = max(1, state.schedule_samples - state.warmup_samples)
    progress = 100.0 * (state.samples_seen - state.warmup_samples) / decay_span
    return "cosine", min(100.0, max(0.0, progress))
