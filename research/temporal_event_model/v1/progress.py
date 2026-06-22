from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class ProbeProgressState:
    run_name: str
    device: str
    data_source: str
    encoder_checkpoint: str
    output_dir: str
    batch_size: int
    epochs: int
    total_steps: int
    train_shard_count: int
    validation_shard_count: int
    probe_parameters: int
    frozen_encoder_parameters: int
    step: int = 0
    epoch: int = 0
    shard_position: int = 0
    shard_index: int = 0
    shard_step: int = 0
    shard_steps: int = 0
    samples_seen_total: int = 0
    loss: float = 0.0
    accuracy_pct: float = 0.0
    macro_f1_pct: float = 0.0
    regression_mse: float = 0.0
    classification_loss: float = 0.0
    upside_accuracy_pct: float = 0.0
    downside_accuracy_pct: float = 0.0
    low_tick_mae: float = 0.0
    high_tick_mae: float = 0.0
    low_price_mae_dollars: float = 0.0
    high_price_mae_dollars: float = 0.0
    valid_low_high_order_pct: float = 0.0
    valid_pct: float = 0.0
    abs_return_bps_mean: float = 0.0
    lr: float = 0.0
    samples_per_second: float = 0.0
    step_seconds: float = 0.0
    data_seconds: float = 0.0
    encode_seconds: float = 0.0
    forward_seconds: float = 0.0
    backward_seconds: float = 0.0
    optimizer_seconds: float = 0.0
    validation_loss: float | None = None
    validation_accuracy_pct: float | None = None
    validation_macro_f1_pct: float | None = None
    validation_regression_mse: float | None = None
    validation_classification_loss: float | None = None
    validation_upside_accuracy_pct: float | None = None
    validation_downside_accuracy_pct: float | None = None
    validation_low_tick_mae: float | None = None
    validation_high_tick_mae: float | None = None
    validation_low_price_mae_dollars: float | None = None
    validation_high_price_mae_dollars: float | None = None
    validation_valid_low_high_order_pct: float | None = None
    validation_valid_pct: float | None = None
    validation_seconds: float | None = None
    gpu_allocated_gib: float = 0.0
    gpu_reserved_gib: float = 0.0
    gpu_peak_allocated_gib: float = 0.0
    process_rss_gib: float = 0.0
    last_message: str = ""


class ProbeTrainingReporter:
    def __init__(self, *, layout: str, state: ProbeProgressState, refresh_per_second: float = 1.0) -> None:
        self.layout = layout
        self.state = state
        self.refresh_per_second = refresh_per_second
        self.min_refresh_interval = 1.0 / max(0.1, refresh_per_second)
        self.started = time.perf_counter()
        self.step_history: deque[float] = deque(maxlen=100)
        self.messages: deque[str] = deque(maxlen=8)
        self._rich = False
        self._live = None
        self._console = None
        self._fallback_reason = ""
        self._last_refresh_at = 0.0

    def __enter__(self) -> ProbeTrainingReporter:
        if self.layout in {"auto", "rich"}:
            try:
                from rich.console import Console
                from rich.live import Live

                self._console = Console()
                self._live = Live(
                    self._render(),
                    console=self._console,
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
        state = self.state
        state.step = int(step)
        state.epoch = int(metrics.get("training/epoch", state.epoch))
        state.shard_position = int(metrics.get("training/shard_position", state.shard_position))
        state.shard_index = int(metrics.get("training/shard", state.shard_index))
        state.shard_step = int(metrics.get("training/shard_step", state.shard_step))
        state.shard_steps = int(metrics.get("training/shard_steps", state.shard_steps))
        state.samples_seen_total = int(metrics.get("training/samples_seen_total", state.samples_seen_total))
        state.loss = float(metrics.get("training/loss", state.loss))
        state.accuracy_pct = float(
            self._first_metric(metrics, ("training/path_accuracy_pct", "training/accuracy_pct"), state.accuracy_pct)
        )
        state.macro_f1_pct = float(
            self._first_metric(metrics, ("training/path_macro_f1_pct", "training/macro_f1_pct"), state.macro_f1_pct)
        )
        state.regression_mse = float(metrics.get("training/regression_mse", state.regression_mse))
        state.classification_loss = float(metrics.get("training/classification_loss", state.classification_loss))
        state.upside_accuracy_pct = float(metrics.get("training/upside_accuracy_pct", state.upside_accuracy_pct))
        state.downside_accuracy_pct = float(metrics.get("training/downside_accuracy_pct", state.downside_accuracy_pct))
        state.low_tick_mae = float(metrics.get("training/low_tick_mae", state.low_tick_mae))
        state.high_tick_mae = float(metrics.get("training/high_tick_mae", state.high_tick_mae))
        state.low_price_mae_dollars = float(metrics.get("training/low_price_mae_dollars", state.low_price_mae_dollars))
        state.high_price_mae_dollars = float(metrics.get("training/high_price_mae_dollars", state.high_price_mae_dollars))
        state.valid_low_high_order_pct = float(
            metrics.get("training/valid_low_high_order_pct", state.valid_low_high_order_pct)
        )
        state.valid_pct = float(metrics.get("training/valid_pct", state.valid_pct))
        state.abs_return_bps_mean = float(metrics.get("training/abs_return_bps_mean", state.abs_return_bps_mean))
        state.lr = float(metrics.get("training/lr", state.lr))
        state.samples_per_second = float(metrics.get("training/samples_per_sec", state.samples_per_second))
        state.step_seconds = float(metrics.get("profile/step_seconds", state.step_seconds))
        state.data_seconds = float(metrics.get("profile/data_seconds", state.data_seconds))
        state.encode_seconds = float(metrics.get("profile/encode_seconds", state.encode_seconds))
        state.forward_seconds = float(metrics.get("profile/forward_seconds", state.forward_seconds))
        state.backward_seconds = float(metrics.get("profile/backward_seconds", state.backward_seconds))
        state.optimizer_seconds = float(metrics.get("profile/optimizer_seconds", state.optimizer_seconds))
        state.gpu_allocated_gib = float(metrics.get("profile/gpu_allocated_gib", state.gpu_allocated_gib))
        state.gpu_reserved_gib = float(metrics.get("profile/gpu_reserved_gib", state.gpu_reserved_gib))
        state.gpu_peak_allocated_gib = float(metrics.get("profile/gpu_peak_allocated_gib", state.gpu_peak_allocated_gib))
        state.process_rss_gib = float(metrics.get("profile/process_rss_gib", state.process_rss_gib))
        if state.step_seconds > 0:
            self.step_history.append(state.step_seconds)
        if validation_metrics:
            state.validation_loss = self._metric(validation_metrics, "validation/loss", state.validation_loss)
            state.validation_accuracy_pct = self._first_metric(
                validation_metrics,
                ("validation/path_accuracy_pct", "validation/accuracy_pct"),
                state.validation_accuracy_pct,
            )
            state.validation_macro_f1_pct = self._first_metric(
                validation_metrics,
                ("validation/path_macro_f1_pct", "validation/macro_f1_pct"),
                state.validation_macro_f1_pct,
            )
            state.validation_regression_mse = self._metric(
                validation_metrics, "validation/regression_mse", state.validation_regression_mse
            )
            state.validation_classification_loss = self._metric(
                validation_metrics, "validation/classification_loss", state.validation_classification_loss
            )
            state.validation_upside_accuracy_pct = self._metric(
                validation_metrics, "validation/upside_accuracy_pct", state.validation_upside_accuracy_pct
            )
            state.validation_downside_accuracy_pct = self._metric(
                validation_metrics, "validation/downside_accuracy_pct", state.validation_downside_accuracy_pct
            )
            state.validation_low_tick_mae = self._metric(
                validation_metrics, "validation/low_tick_mae", state.validation_low_tick_mae
            )
            state.validation_high_tick_mae = self._metric(
                validation_metrics, "validation/high_tick_mae", state.validation_high_tick_mae
            )
            state.validation_low_price_mae_dollars = self._metric(
                validation_metrics, "validation/low_price_mae_dollars", state.validation_low_price_mae_dollars
            )
            state.validation_high_price_mae_dollars = self._metric(
                validation_metrics, "validation/high_price_mae_dollars", state.validation_high_price_mae_dollars
            )
            state.validation_valid_low_high_order_pct = self._metric(
                validation_metrics, "validation/valid_low_high_order_pct", state.validation_valid_low_high_order_pct
            )
            state.validation_valid_pct = self._metric(validation_metrics, "validation/valid_pct", state.validation_valid_pct)
            state.validation_seconds = self._metric(validation_metrics, "validation/seconds", state.validation_seconds)
        self.refresh()

    @staticmethod
    def _metric(metrics: dict[str, float], key: str, fallback: float | None) -> float | None:
        value = metrics.get(key)
        return fallback if value is None else float(value)

    @staticmethod
    def _first_metric(metrics: dict[str, float], keys: tuple[str, ...], fallback: float | None) -> float | None:
        for key in keys:
            value = metrics.get(key)
            if value is not None:
                return float(value)
        return fallback

    def message(self, text: str) -> None:
        self.state.last_message = text
        timestamp = time.strftime("%H:%M:%S")
        self.messages.append(f"{timestamp} {text}")
        if self._rich:
            self.refresh()
        else:
            print(text, flush=True)

    def refresh(self, *, force: bool = False) -> None:
        if self._rich and self._live is not None:
            now = time.perf_counter()
            if not force and now - self._last_refresh_at < self.min_refresh_interval:
                return
            self._live.update(self._render(), refresh=True)
            self._last_refresh_at = now
        elif self.layout == "text":
            print(self._text_line(), flush=True)

    def _render(self) -> Any:
        from rich.align import Align
        from rich.console import Group
        from rich.panel import Panel
        from rich.progress import BarColumn, Progress, TextColumn
        from rich.table import Table
        from rich.text import Text

        state = self.state
        elapsed_seconds = max(0.0, time.perf_counter() - self.started)
        avg_step_seconds = self._average_step_seconds()
        remaining_steps = max(0, state.total_steps - state.step)
        shard_remaining_steps = max(0, state.shard_steps - state.shard_step)
        epoch_steps = max(1, state.total_steps // max(1, state.epochs))
        epoch_completed_steps = (max(0, state.epoch - 1) * epoch_steps) + state.shard_step
        epoch_remaining_steps = max(0, epoch_steps - (epoch_completed_steps % epoch_steps))
        run_eta_seconds = remaining_steps * avg_step_seconds if avg_step_seconds > 0 else None
        shard_eta_seconds = shard_remaining_steps * avg_step_seconds if avg_step_seconds > 0 else None
        epoch_eta_seconds = epoch_remaining_steps * avg_step_seconds if avg_step_seconds > 0 else None

        overall = Progress(TextColumn("[bold]Overall"), BarColumn(bar_width=None), TextColumn("{task.percentage:>6.2f}%"), expand=True)
        overall.add_task("overall", total=max(1, state.total_steps), completed=min(state.step, max(1, state.total_steps)))
        shard = Progress(TextColumn("[bold]Shard"), BarColumn(bar_width=None), TextColumn("{task.percentage:>6.2f}%"), expand=True)
        shard.add_task("shard", total=max(1, state.shard_steps), completed=min(state.shard_step, max(1, state.shard_steps)))

        summary = Table.grid(expand=False, padding=(0, 4))
        summary.add_column(justify="left", no_wrap=True)
        summary.add_column(justify="left", no_wrap=True)
        summary.add_row(f"[bold]Run[/] {state.run_name}", f"[bold]Device[/] {state.device}")
        summary.add_row(f"[bold]Step[/] {state.step:,}/{state.total_steps:,}", f"[bold]Batch[/] {state.batch_size:,}")
        summary.add_row(f"[bold]Epoch[/] {state.epoch}/{state.epochs}", f"[bold]Shard[/] {state.shard_position}/{state.train_shard_count} id {state.shard_index}")
        summary.add_row(f"[bold]Samples[/] {state.samples_seen_total:,}", f"[bold]Speed[/] {state.samples_per_second:,.1f}/s")
        summary.add_row(f"[bold]Elapsed[/] {self._format_duration(elapsed_seconds)}", f"[bold]Step avg[/] {avg_step_seconds:.3f}s")
        summary.add_row(f"[bold]Shard ETA[/] {self._format_duration(shard_eta_seconds)}", f"[bold]Epoch ETA[/] {self._format_duration(epoch_eta_seconds)}")
        summary.add_row(f"[bold]Run ETA[/] {self._format_duration(run_eta_seconds)}", f"[bold]Finish[/] {self._format_finish_time(run_eta_seconds)}")
        summary.add_row(f"[bold]Probe params[/] {state.probe_parameters:,}", f"[bold]Frozen encoder[/] {state.frozen_encoder_parameters:,}")
        summary.add_row(f"[bold]Data[/] {state.data_source}", f"[bold]Output[/] {state.output_dir}")

        learning = Table.grid(expand=False, padding=(0, 1))
        learning.add_column("Metric", justify="right", no_wrap=True)
        learning.add_column("Train", justify="left", no_wrap=True)
        learning.add_column("Validation", justify="left", no_wrap=True)
        learning.add_row("loss", f"{state.loss:.6f}", self._optional(state.validation_loss, ".6f"))
        learning.add_row("path acc", f"{state.accuracy_pct:.3f}%", self._optional(state.validation_accuracy_pct, ".3f", suffix="%"))
        learning.add_row("path macro F1", f"{state.macro_f1_pct:.3f}%", self._optional(state.validation_macro_f1_pct, ".3f", suffix="%"))
        learning.add_row(
            "up/down acc",
            f"{state.upside_accuracy_pct:.2f}% / {state.downside_accuracy_pct:.2f}%",
            self._optional_pair(state.validation_upside_accuracy_pct, state.validation_downside_accuracy_pct, ".2f", suffix="%"),
        )
        learning.add_row(
            "loss parts",
            f"reg {state.regression_mse:.4f} / cls {state.classification_loss:.4f}",
            self._optional_pair(state.validation_regression_mse, state.validation_classification_loss, ".4f", labels=("reg", "cls")),
        )
        learning.add_row(
            "price MAE",
            f"low ${state.low_price_mae_dollars:.4f} / high ${state.high_price_mae_dollars:.4f}",
            self._optional_pair(
                state.validation_low_price_mae_dollars,
                state.validation_high_price_mae_dollars,
                ".4f",
                labels=("low $", "high $"),
            ),
        )
        learning.add_row(
            "tick MAE",
            f"low {state.low_tick_mae:.2f} / high {state.high_tick_mae:.2f}",
            self._optional_pair(state.validation_low_tick_mae, state.validation_high_tick_mae, ".2f", labels=("low", "high")),
        )
        learning.add_row(
            "low <= high",
            f"{state.valid_low_high_order_pct:.2f}%",
            self._optional(state.validation_valid_low_high_order_pct, ".2f", suffix="%"),
        )
        learning.add_row("valid labels", f"{state.valid_pct:.2f}%", self._optional(state.validation_valid_pct, ".2f", suffix="%"))
        learning.add_row("abs return", f"{state.abs_return_bps_mean:.2f} bps", "--")
        learning.add_row("lr", f"{state.lr:.3e}", "--")

        profile = Table.grid(expand=False, padding=(0, 1))
        profile.add_column("Stage", justify="right", no_wrap=True)
        profile.add_column("Seconds", justify="left", no_wrap=True)
        profile.add_row("step total", f"{state.step_seconds:.4f}")
        profile.add_row("data", f"{state.data_seconds:.4f}")
        profile.add_row("frozen encoder", f"{state.encode_seconds:.4f}")
        profile.add_row("probe forward", f"{state.forward_seconds:.4f}")
        profile.add_row("backward", f"{state.backward_seconds:.4f}")
        profile.add_row("optimizer", f"{state.optimizer_seconds:.4f}")
        profile.add_row("validation", self._optional(state.validation_seconds, ".3f"))

        memory = Table.grid(expand=False, padding=(0, 1))
        memory.add_column("Metric", justify="right", no_wrap=True)
        memory.add_column("GiB", justify="left", no_wrap=True)
        memory.add_row("GPU peak allocated", f"{state.gpu_peak_allocated_gib:.2f}")
        memory.add_row("GPU reserved", f"{state.gpu_reserved_gib:.2f}")
        memory.add_row("GPU allocated", f"{state.gpu_allocated_gib:.2f}")
        memory.add_row("process RSS", f"{state.process_rss_gib:.2f}")

        retained_messages = list(self.messages) if self.messages else [state.last_message or "running"]
        retained_messages.extend([""] * max(0, self.messages.maxlen - len(retained_messages)))
        messages = Table.grid(expand=True)
        messages.add_column(no_wrap=True, overflow="ellipsis")
        for line in retained_messages[: self.messages.maxlen]:
            messages.add_row(line)

        return Group(
            Panel(summary, title="Linear Probe Run", border_style="cyan"),
            overall,
            shard,
            Panel(Align.center(learning), title="Learning", border_style="magenta"),
            Panel(Align.center(profile), title="Step Profile", border_style="yellow"),
            Panel(Align.center(memory), title="Memory", border_style="blue", height=8),
            Panel(messages, title="Messages", border_style="blue", height=10),
            Text("\n\n\n"),
        )

    def _average_step_seconds(self) -> float:
        if self.step_history:
            return sum(self.step_history) / len(self.step_history)
        return max(0.0, float(self.state.step_seconds))

    @staticmethod
    def _format_duration(seconds: float | None) -> str:
        if seconds is None or seconds <= 0:
            return "--"
        seconds = float(seconds)
        hours, remainder = divmod(int(seconds), 3600)
        minutes, secs = divmod(remainder, 60)
        if hours:
            return f"{hours:d}h {minutes:02d}m"
        if minutes:
            return f"{minutes:d}m {secs:02d}s"
        return f"{secs:d}s"

    @staticmethod
    def _format_finish_time(seconds_from_now: float | None) -> str:
        if seconds_from_now is None or seconds_from_now <= 0:
            return "--"
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(time.time() + float(seconds_from_now)))

    @staticmethod
    def _optional(value: float | None, fmt: str, *, suffix: str = "") -> str:
        if value is None:
            return "--"
        return f"{value:{fmt}}{suffix}"

    @staticmethod
    def _optional_pair(
        left: float | None,
        right: float | None,
        fmt: str,
        *,
        suffix: str = "",
        labels: tuple[str, str] | None = None,
    ) -> str:
        if left is None or right is None:
            return "--"
        left_value = f"{left:{fmt}}{suffix}"
        right_value = f"{right:{fmt}}{suffix}"
        if labels is None:
            return f"{left_value} / {right_value}"
        return f"{labels[0]} {left_value} / {labels[1]} {right_value}"

    def _text_line(self) -> str:
        state = self.state
        return (
            f"step={state.step}/{state.total_steps} epoch={state.epoch}/{state.epochs} "
            f"shard={state.shard_position}/{state.train_shard_count}:{state.shard_step}/{state.shard_steps} "
            f"loss={state.loss:.6f} path_acc={state.accuracy_pct:.3f}% path_f1={state.macro_f1_pct:.3f}% "
            f"reg_mse={state.regression_mse:.4f} cls_loss={state.classification_loss:.4f} "
            f"speed={state.samples_per_second:,.1f}/s step_s={state.step_seconds:.3f}"
        )
