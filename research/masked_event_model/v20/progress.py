from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class TrainingProgressState:
    run_name: str
    device: str
    data_source: str
    batch_size: int
    max_steps: int
    epochs: int
    model_parameters: int
    output_dir: str
    step: int = 0
    epoch: int = 0
    epoch_progress_pct: float = 0.0
    shard_index: int = 0
    shard_count: int = 0
    shard_step: int = 0
    shard_steps: int = 0
    epoch_step: int = 0
    planned_epoch_steps: int = 0
    planned_total_steps: int = 0
    samples_seen_total: int = 0
    loss: float = 0.0
    event_bit_acc_pct: float = 0.0
    event_bit_acc_lift_pct: float = 0.0
    event_balanced_bit_acc_pct: float = 0.0
    event_bit_majority_baseline_pct: float = 0.0
    byte_exact_acc_pct: float = 0.0
    byte_exact_lift_pct: float = 0.0
    byte_mode_baseline_pct: float = 0.0
    event_mask_ratio_pct: float = 0.0
    event_visible_events: int = 0
    event_masked_events: int = 0
    event_mask_policy_id: int = 0
    event_soft_byte_psnr_db: float | None = None
    event_hard_byte_psnr_db: float | None = None
    lr: float = 0.0
    step_seconds: float = 0.0
    samples_per_second: float = 0.0
    epoch_loss_mean: float | None = None
    data_wait_seconds: float = 0.0
    shard_load_seconds: float = 0.0
    shard_shuffle_seconds: float = 0.0
    transfer_seconds: float = 0.0
    mask_seconds: float = 0.0
    forward_seconds: float = 0.0
    metrics_seconds: float = 0.0
    backward_seconds: float = 0.0
    optimizer_seconds: float = 0.0
    inference_encode_seconds: float = 0.0
    inference_encode_ms_per_sample: float = 0.0
    decoder_chunk_size: int = 0
    header_decoder_chunks: int = 0
    event_decoder_chunks: int = 0
    gpu_allocated_gib: float = 0.0
    gpu_reserved_gib: float = 0.0
    gpu_peak_allocated_gib: float = 0.0
    gpu_free_gib: float = 0.0
    gpu_total_gib: float = 0.0
    process_rss_gib: float = 0.0
    system_memory_available_gib: float = 0.0
    system_memory_used_gib: float = 0.0
    validation_loss: float | None = None
    validation_event_soft_byte_psnr_db: float | None = None
    validation_event_hard_byte_psnr_db: float | None = None
    validation_seconds: float | None = None
    semantic_metrics: dict[str, float] = field(default_factory=dict)
    validation_semantic_metrics: dict[str, float] = field(default_factory=dict)
    profiler_active: bool = False
    last_message: str = ""


class TrainingReporter:
    def __init__(self, *, layout: str, state: TrainingProgressState, refresh_per_second: float = 1.0) -> None:
        self.layout = layout
        self.state = state
        self.refresh_per_second = refresh_per_second
        self.min_refresh_interval = 1.0 / max(0.1, refresh_per_second)
        self.started = time.perf_counter()
        self.history: deque[float] = deque(maxlen=100)
        self.messages: deque[str] = deque(maxlen=6)
        self._rich = False
        self._live = None
        self._console = None
        self._fallback_reason = ""
        self._last_refresh_at = 0.0
        self._bottom_padding_lines = 5

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

    def _latest_step_seconds(self) -> float:
        if self.history:
            return sum(self.history) / len(self.history)
        return max(0.0, float(self.state.step_seconds))

    def __enter__(self) -> "TrainingReporter":
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
        state.step = step
        state.epoch = int(metrics.get("train/epoch", state.epoch))
        state.epoch_progress_pct = float(metrics.get("train/epoch_progress_pct", state.epoch_progress_pct))
        state.shard_index = int(metrics.get("train/shard_index", state.shard_index))
        state.shard_count = int(metrics.get("train/shards_per_epoch", state.shard_count))
        state.shard_step = int(metrics.get("train/shard_step", state.shard_step))
        state.shard_steps = int(metrics.get("train/shard_steps", state.shard_steps))
        state.epoch_step = int(metrics.get("train/epoch_step", state.epoch_step))
        state.planned_epoch_steps = int(metrics.get("train/epoch_steps_planned", state.planned_epoch_steps))
        state.planned_total_steps = int(metrics.get("train/total_steps_planned", state.planned_total_steps))
        state.samples_seen_total = int(metrics.get("train/samples_seen_total", state.samples_seen_total))
        state.loss = float(metrics.get("pretrain/loss_total", state.loss))
        state.event_bit_acc_pct = float(metrics.get("pretrain/event_bit_acc_pct", state.event_bit_acc_pct))
        state.event_bit_acc_lift_pct = float(metrics.get("pretrain/event_bit_acc_lift_pct", state.event_bit_acc_lift_pct))
        state.event_balanced_bit_acc_pct = float(metrics.get("pretrain/event_balanced_bit_acc_pct", state.event_balanced_bit_acc_pct))
        state.event_bit_majority_baseline_pct = float(metrics.get("pretrain/event_bit_majority_baseline_pct", state.event_bit_majority_baseline_pct))
        state.byte_exact_acc_pct = float(metrics.get("pretrain/event_byte_exact_acc_pct", state.byte_exact_acc_pct))
        state.byte_exact_lift_pct = float(metrics.get("pretrain/event_byte_exact_lift_pct", state.byte_exact_lift_pct))
        state.byte_mode_baseline_pct = float(metrics.get("pretrain/event_byte_mode_baseline_pct", state.byte_mode_baseline_pct))
        state.event_mask_ratio_pct = float(metrics.get("mask/event_mask_ratio_pct", state.event_mask_ratio_pct))
        state.event_visible_events = int(metrics.get("mask/event_visible_events", state.event_visible_events))
        state.event_masked_events = int(metrics.get("mask/event_masked_events", state.event_masked_events))
        state.event_mask_policy_id = int(metrics.get("mask/event_mask_policy_id", state.event_mask_policy_id))
        if "pretrain/event_soft_byte_psnr_db" in metrics:
            state.event_soft_byte_psnr_db = float(metrics["pretrain/event_soft_byte_psnr_db"])
        if "pretrain/event_hard_byte_psnr_db" in metrics:
            state.event_hard_byte_psnr_db = float(metrics["pretrain/event_hard_byte_psnr_db"])
        state.lr = float(metrics.get("train/lr", state.lr))
        if "train/epoch_loss_mean" in metrics:
            state.epoch_loss_mean = float(metrics["train/epoch_loss_mean"])
        state.step_seconds = float(metrics.get("train/step_seconds", state.step_seconds))
        if "train/samples_per_second" in metrics:
            state.samples_per_second = float(metrics["train/samples_per_second"])
        elif state.step_seconds > 0:
            state.samples_per_second = state.batch_size / state.step_seconds
        state.data_wait_seconds = float(metrics.get("profile/data_wait_seconds", state.data_wait_seconds))
        state.shard_load_seconds = float(metrics.get("profile/data/shard_load_seconds", state.shard_load_seconds))
        state.shard_shuffle_seconds = float(metrics.get("profile/data/shard_shuffle_seconds", state.shard_shuffle_seconds))
        state.transfer_seconds = float(metrics.get("profile/transfer_seconds", state.transfer_seconds))
        state.mask_seconds = float(metrics.get("profile/mask_seconds", state.mask_seconds))
        state.forward_seconds = float(metrics.get("profile/forward_loss_seconds", state.forward_seconds))
        state.metrics_seconds = float(metrics.get("profile/metrics_seconds", state.metrics_seconds))
        state.backward_seconds = float(metrics.get("profile/backward_seconds", state.backward_seconds))
        state.optimizer_seconds = float(metrics.get("profile/optimizer_seconds", state.optimizer_seconds))
        state.inference_encode_seconds = float(metrics.get("profile/inference_encode_seconds", state.inference_encode_seconds))
        state.inference_encode_ms_per_sample = float(metrics.get("profile/inference_encode_ms_per_sample", state.inference_encode_ms_per_sample))
        state.decoder_chunk_size = int(metrics.get("profile/decoder_chunk_size", state.decoder_chunk_size))
        state.header_decoder_chunks = int(metrics.get("profile/header_decoder_chunks", state.header_decoder_chunks))
        state.event_decoder_chunks = int(metrics.get("profile/event_decoder_chunks", state.event_decoder_chunks))
        state.gpu_allocated_gib = float(metrics.get("profile/gpu_allocated_gib", state.gpu_allocated_gib))
        state.gpu_reserved_gib = float(metrics.get("profile/gpu_reserved_gib", state.gpu_reserved_gib))
        state.gpu_peak_allocated_gib = float(metrics.get("profile/gpu_peak_allocated_gib", state.gpu_peak_allocated_gib))
        state.gpu_free_gib = float(metrics.get("profile/gpu_free_gib", state.gpu_free_gib))
        state.gpu_total_gib = float(metrics.get("profile/gpu_total_gib", state.gpu_total_gib))
        state.process_rss_gib = float(metrics.get("profile/process_rss_gib", state.process_rss_gib))
        state.system_memory_available_gib = float(metrics.get("profile/system_memory_available_gib", state.system_memory_available_gib))
        state.system_memory_used_gib = float(metrics.get("profile/system_memory_used_gib", state.system_memory_used_gib))
        state.profiler_active = any(key.startswith("profile/") for key in metrics)
        semantic_prefix = "pretrain/semantic/"
        semantic_metrics = {key[len(semantic_prefix) :]: float(value) for key, value in metrics.items() if key.startswith(semantic_prefix)}
        if semantic_metrics:
            state.semantic_metrics = semantic_metrics
        if validation_metrics:
            state.validation_loss = float(validation_metrics.get("validation/pretrain/loss_total", state.validation_loss or 0.0))
            if "validation/pretrain/event_soft_byte_psnr_db" in validation_metrics:
                state.validation_event_soft_byte_psnr_db = float(validation_metrics["validation/pretrain/event_soft_byte_psnr_db"])
            if "validation/pretrain/event_hard_byte_psnr_db" in validation_metrics:
                state.validation_event_hard_byte_psnr_db = float(validation_metrics["validation/pretrain/event_hard_byte_psnr_db"])
            state.validation_seconds = float(validation_metrics.get("validation/pretrain/seconds", state.validation_seconds or 0.0))
            validation_semantic_prefix = "validation/pretrain/semantic/"
            validation_semantic_metrics = {
                key[len(validation_semantic_prefix) :]: float(value)
                for key, value in validation_metrics.items()
                if key.startswith(validation_semantic_prefix)
            }
            if validation_semantic_metrics:
                state.validation_semantic_metrics = validation_semantic_metrics
        if state.step_seconds > 0:
            self.history.append(state.step_seconds)
        self.refresh()

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
        progress = Progress(
            TextColumn("[bold]Overall"),
            BarColumn(bar_width=None),
            TextColumn("{task.percentage:>6.2f}%"),
            expand=True,
        )
        if state.max_steps > 0:
            total_steps = max(1, state.max_steps)
        elif state.planned_total_steps > 0:
            total_steps = max(1, state.planned_total_steps)
        else:
            total_steps = max(1, state.shard_count * max(1, state.shard_steps) * max(1, state.epochs))
        progress.add_task("steps", total=total_steps, completed=min(state.step, total_steps))
        epoch_progress = Progress(TextColumn("[bold]Epoch"), BarColumn(bar_width=None), TextColumn("{task.percentage:>6.2f}%"), expand=True)
        epoch_progress.add_task("epoch", total=100.0, completed=max(0.0, min(100.0, state.epoch_progress_pct)))

        elapsed_seconds = max(0.0, time.perf_counter() - self.started)
        avg_step_seconds = self._latest_step_seconds()
        validation_overhead_per_step = 0.0
        if state.validation_seconds is not None and state.shard_steps > 0:
            validation_overhead_per_step = max(0.0, float(state.validation_seconds)) / max(1, state.shard_steps)
        eta_step_seconds = avg_step_seconds + validation_overhead_per_step
        total_remaining_steps = max(0, total_steps - min(state.step, total_steps))
        shard_remaining_steps = max(0, state.shard_steps - state.shard_step)
        epoch_step_estimate = max(1, state.planned_epoch_steps or state.shard_count * max(1, state.shard_steps))
        epoch_completed_steps = max(0, state.epoch_step) if state.epoch_step > 0 else int(
            round(max(0.0, min(100.0, state.epoch_progress_pct)) / 100.0 * epoch_step_estimate)
        )
        epoch_remaining_steps = max(0, epoch_step_estimate - epoch_completed_steps)
        shard_eta_seconds = shard_remaining_steps * eta_step_seconds if eta_step_seconds > 0 else None
        epoch_eta_seconds = epoch_remaining_steps * eta_step_seconds if eta_step_seconds > 0 else None
        total_eta_seconds = total_remaining_steps * eta_step_seconds if eta_step_seconds > 0 else None

        summary = Table.grid(expand=False, padding=(0, 4))
        summary.add_column(justify="left", no_wrap=True)
        summary.add_column(justify="left", no_wrap=True)
        step_text = f"{state.step:,}/{state.max_steps:,}" if state.max_steps > 0 else f"{state.step:,}"
        summary.add_row(f"[bold]Step[/] {step_text}", f"[bold]Batch[/] {state.batch_size:,}")
        summary.add_row(f"[bold]Samples[/] {state.samples_seen_total:,}", f"[bold]Speed[/] {state.samples_per_second:,.1f}/s")
        summary.add_row(f"[bold]Epoch[/] {state.epoch}/{state.epochs}", f"[bold]Shard[/] {state.shard_index}/{state.shard_count} step {state.shard_step}/{state.shard_steps}")
        summary.add_row(f"[bold]Elapsed[/] {self._format_duration(elapsed_seconds)}", f"[bold]Step avg[/] {avg_step_seconds:.3f}s")
        if validation_overhead_per_step > 0:
            summary.add_row(f"[bold]Val overhead[/] {validation_overhead_per_step:.3f}s/step", f"[bold]ETA step[/] {eta_step_seconds:.3f}s")
        summary.add_row(f"[bold]Shard ETA[/] {self._format_duration(shard_eta_seconds)}", f"[bold]Epoch ETA[/] {self._format_duration(epoch_eta_seconds)}")
        summary.add_row(f"[bold]Run ETA[/] {self._format_duration(total_eta_seconds)}", f"[bold]Finish[/] {self._format_finish_time(total_eta_seconds)}")
        summary.add_row(f"[bold]Run[/] {state.run_name}", f"[bold]Device[/] {state.device}")
        summary.add_row(f"[bold]Data[/] {state.data_source}", f"[bold]Params[/] {state.model_parameters:,}")

        metrics = Table.grid(expand=False, padding=(0, 1))
        metrics.add_column("Metric", justify="right", no_wrap=True)
        metrics.add_column("Value", justify="left", no_wrap=True)
        metrics.add_row("loss", f"{state.loss:.6f}")
        metrics.add_row("balanced bit acc", f"{state.event_balanced_bit_acc_pct:.3f}%")
        metrics.add_row("bit acc lift", f"{state.event_bit_acc_lift_pct:+.3f}%")
        metrics.add_row("event bit acc", f"{state.event_bit_acc_pct:.3f}%")
        metrics.add_row("bit baseline", f"{state.event_bit_majority_baseline_pct:.3f}%")
        metrics.add_row("byte exact acc", f"{state.byte_exact_acc_pct:.3f}%")
        metrics.add_row("byte exact lift", f"{state.byte_exact_lift_pct:+.3f}%")
        metrics.add_row("byte mode baseline", f"{state.byte_mode_baseline_pct:.3f}%")
        metrics.add_row("mask ratio", f"{state.event_mask_ratio_pct:.2f}%")
        metrics.add_row("visible/masked events", f"{state.event_visible_events}/{state.event_masked_events}")
        metrics.add_row("mask policy id", f"{state.event_mask_policy_id}")
        if state.event_soft_byte_psnr_db is not None:
            metrics.add_row("event soft PSNR", f"{state.event_soft_byte_psnr_db:.3f} dB")
        if state.event_hard_byte_psnr_db is not None:
            metrics.add_row("event hard PSNR", f"{state.event_hard_byte_psnr_db:.3f} dB")
        metrics.add_row("lr", f"{state.lr:.3e}")
        if state.epoch_loss_mean is not None:
            metrics.add_row("epoch loss mean", f"{state.epoch_loss_mean:.6f}")
        if state.validation_loss is not None:
            metrics.add_row("validation loss", f"{state.validation_loss:.6f}")
        if state.validation_event_soft_byte_psnr_db is not None:
            metrics.add_row("val event soft PSNR", f"{state.validation_event_soft_byte_psnr_db:.3f} dB")
        if state.validation_event_hard_byte_psnr_db is not None:
            metrics.add_row("val event hard PSNR", f"{state.validation_event_hard_byte_psnr_db:.3f} dB")

        semantic = Table.grid(expand=False, padding=(0, 1))
        semantic.add_column("Metric", justify="right", no_wrap=True)
        semantic.add_column("Train", justify="left", no_wrap=True)
        semantic.add_column("Val", justify="left", no_wrap=True)
        for label, key, fmt in [
            ("masked events", "masked_events", ",.0f"),
            ("valid events", "valid_events", ",.0f"),
            ("quote/trade events", "__quote_trade_events__", ""),
            ("event type acc", "event_type_acc_pct", ".2f"),
            ("presence acc", "event_presence_acc_pct", ".2f"),
            ("quote ask MAE", "quote_ask_price_mae", ".6f"),
            ("quote bid MAE", "quote_bid_price_mae", ".6f"),
            ("quote spread ticks", "quote_spread_tick_mae", ".2f"),
            ("trade price MAE", "trade_price_mae", ".6f"),
            ("quote exch acc", "__quote_exchange_acc__", ""),
            ("trade exch acc", "trade_exchange_acc_pct", ".2f"),
            ("quote cond exact", "quote_all_condition_slots_exact_acc_pct", ".2f"),
            ("trade cond exact", "trade_all_condition_slots_exact_acc_pct", ".2f"),
            ("pred quote valid", "predicted_quote_valid_pct", ".2f"),
        ]:
            semantic.add_row(
                label,
                self._format_semantic_metric(state.semantic_metrics, key, fmt),
                self._format_semantic_metric(state.validation_semantic_metrics, key, fmt),
            )

        profile = Table.grid(expand=False, padding=(0, 1))
        profile.add_column("Stage", justify="right", no_wrap=True)
        profile.add_column("Value", justify="left", no_wrap=True)
        profile.add_row("production encode", f"{state.inference_encode_ms_per_sample:.4f} ms/sample")
        profile.add_row("production encode total", f"{state.inference_encode_seconds:.4f} s")
        profile.add_row("train step total", f"{state.step_seconds:.4f} s")
        profile.add_row("forward + loss", f"{state.forward_seconds:.4f} s")
        profile.add_row("metrics", f"{state.metrics_seconds:.4f} s")
        profile.add_row("backward", f"{state.backward_seconds:.4f} s")
        profile.add_row("data wait", f"{state.data_wait_seconds:.4f} s")
        if state.decoder_chunk_size > 0:
            profile.add_row("decoder chunk size", f"{state.decoder_chunk_size:,}")
            profile.add_row("decoder chunks", f"H {state.header_decoder_chunks:,} / E {state.event_decoder_chunks:,}")
        profile.add_row("shard load", f"{state.shard_load_seconds:.4f} s")
        profile.add_row("shard shuffle", f"{state.shard_shuffle_seconds:.4f} s")
        profile.add_row("mask", f"{state.mask_seconds:.4f} s")
        profile.add_row("transfer", f"{state.transfer_seconds:.4f} s")
        profile.add_row("optimizer", f"{state.optimizer_seconds:.4f} s")

        memory = Table.grid(expand=False, padding=(0, 1))
        memory.add_column("Metric", justify="right", no_wrap=True)
        memory.add_column("GiB", justify="left", no_wrap=True)
        memory.add_row("GPU peak allocated", f"{state.gpu_peak_allocated_gib:.2f}")
        memory.add_row("GPU reserved", f"{state.gpu_reserved_gib:.2f}")
        memory.add_row("GPU allocated", f"{state.gpu_allocated_gib:.2f}")
        memory.add_row("GPU free", f"{state.gpu_free_gib:.2f}")
        memory.add_row("GPU total", f"{state.gpu_total_gib:.2f}")
        memory.add_row("process RSS", f"{state.process_rss_gib:.2f}")
        memory.add_row("system used", f"{state.system_memory_used_gib:.2f}")
        memory.add_row("system available", f"{state.system_memory_available_gib:.2f}")

        retained_messages = list(self.messages) if self.messages else [state.last_message or "running"]
        retained_messages.extend([""] * max(0, self.messages.maxlen - len(retained_messages)))
        messages = Table.grid(expand=True)
        messages.add_column(no_wrap=True, overflow="ellipsis")
        for line in retained_messages[: self.messages.maxlen]:
            messages.add_row(line)

        body = Group(
            Panel(summary, title="Training Run", border_style="cyan"),
            progress,
            epoch_progress,
            Panel(Align.center(metrics), title="Learning", border_style="magenta"),
            Panel(Align.center(semantic), title="Semantic Reconstruction", border_style="green"),
            Panel(Align.center(profile), title="Step Profile", border_style="yellow"),
            Panel(Align.center(memory), title="Memory", border_style="blue", height=12),
            Panel(messages, title="Messages", border_style="blue", height=10),
            Text("\n" * self._bottom_padding_lines),
        )
        return body

    @staticmethod
    def _format_semantic_metric(values: dict[str, float], key: str, fmt: str) -> str:
        if not values:
            return "--"
        if key == "__quote_trade_events__":
            quote_events = values.get("quote_events")
            trade_events = values.get("trade_events")
            if quote_events is None and trade_events is None:
                return "--"
            return f"{quote_events or 0:,.0f}/{trade_events or 0:,.0f}"
        if key == "__quote_exchange_acc__":
            bid_acc = values.get("quote_bid_exchange_acc_pct")
            ask_acc = values.get("quote_ask_exchange_acc_pct")
            if bid_acc is None and ask_acc is None:
                return "--"
            return f"{bid_acc or 0:.2f}/{ask_acc or 0:.2f}"
        value = values.get(key)
        if value is None:
            return "--"
        suffix = "%" if key.endswith("_pct") else ""
        return f"{value:{fmt}}{suffix}"

    def _text_line(self) -> str:
        state = self.state
        return (
            f"step={state.step} epoch={state.epoch}/{state.epochs} "
            f"loss={state.loss:.6f} balanced_bit_acc={state.event_balanced_bit_acc_pct:.3f}% "
            f"bit_lift={state.event_bit_acc_lift_pct:+.3f}% byte_lift={state.byte_exact_lift_pct:+.3f}% "
            f"step_s={state.step_seconds:.3f} data_s={state.data_wait_seconds:.3f} "
            f"elapsed={self._format_duration(time.perf_counter() - self.started)} "
            f"gpu_alloc_gib={state.gpu_allocated_gib:.2f}"
        )
