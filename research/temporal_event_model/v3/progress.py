from __future__ import annotations

import datetime as dt
import time
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping


@dataclass(slots=True)
class TemporalProgressState:
    run_name: str
    dataset_id: str
    device: str
    precision: str
    output_dir: str
    model_parameters: int
    batch_size: int = 0
    max_samples: int = 0
    samples_clock: int = 0
    update_count: int = 0
    epoch: int = 0
    samples_seen: int = 0
    loss: float = 0.0
    active_task_count: float = 0.0
    lr: float = 0.0
    batch_seconds: float = 0.0
    samples_per_second: float = 0.0
    loader_wait_seconds: float = 0.0
    gpu_batch_seconds: float = 0.0
    materialize_seconds: float = 0.0
    gpu_memory_gib: float = 0.0
    cpu_rss_gib: float = 0.0
    validation_loss: float | None = None
    availability: dict[str, float] = field(default_factory=dict)
    task_losses: dict[str, float] = field(default_factory=dict)
    validation_metrics: dict[str, float] = field(default_factory=dict)
    loader_cache: dict[str, float] = field(default_factory=dict)
    loader_window: dict[str, float] = field(default_factory=dict)
    loader_prefetch: dict[str, float] = field(default_factory=dict)
    loader_state: dict[str, float] = field(default_factory=dict)
    loader_status: dict[str, Any] = field(default_factory=dict)
    trainer_status: dict[str, Any] = field(default_factory=dict)
    cache_state: dict[str, Any] = field(default_factory=dict)
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
        self._bottom_padding_lines = 4
        self._state_lock = threading.RLock()
        self._refresh_lock = threading.Lock()
        self._loader_metrics_provider: Callable[[], Mapping[str, Any]] | None = None
        self._loader_poll_stop = threading.Event()
        self._loader_poll_thread: threading.Thread | None = None

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

    def _latest_batch_seconds(self) -> float:
        if self.history:
            return sum(self.history) / len(self.history)
        return max(0.0, float(self.state.batch_seconds))

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
        self._loader_poll_stop.set()
        if self._loader_poll_thread is not None and self._loader_poll_thread.is_alive():
            self._loader_poll_thread.join(timeout=2.0)
        if self._live is not None:
            self.refresh(force=True)
            self._live.stop()

    def set_loader_metrics_provider(self, provider: Callable[[], Mapping[str, Any]] | None) -> None:
        self._loader_metrics_provider = provider
        if provider is None or self._loader_poll_thread is not None:
            return
        self._loader_poll_stop.clear()
        self._loader_poll_thread = threading.Thread(target=self._poll_loader_metrics, name="temporal-v3-loader-telemetry", daemon=True)
        self._loader_poll_thread.start()

    def _poll_loader_metrics(self) -> None:
        while not self._loader_poll_stop.wait(self.min_refresh_interval):
            provider = self._loader_metrics_provider
            if provider is None:
                continue
            try:
                metrics = dict(provider())
            except Exception as exc:  # noqa: BLE001
                self.message(f"Loader telemetry unavailable: {exc!r}")
                continue
            self.update(metrics, step=int(self.state.samples_clock), validation_metrics=None, record_history=False)

    def update(self, metrics: Mapping[str, Any], *, step: int, validation_metrics: dict[str, float] | None = None, record_history: bool = True) -> None:
        with self._state_lock:
            self._apply_update(metrics, step=step, validation_metrics=validation_metrics, record_history=record_history)
        self.refresh()

    def _apply_update(self, metrics: Mapping[str, Any], *, step: int, validation_metrics: dict[str, float] | None, record_history: bool) -> None:
        s = self.state
        s.samples_clock = int(metrics.get("train/samples_clock", step))
        s.update_count = int(metrics.get("train/update_count", s.update_count))
        s.epoch = int(metrics.get("loader/epoch", s.epoch))
        s.samples_seen = int(metrics.get("train/samples_seen_total", s.samples_seen))
        s.loss = float(metrics.get("train/loss", s.loss))
        s.active_task_count = float(metrics.get("train/active_task_count", s.active_task_count))
        s.lr = float(metrics.get("train/learning_rate", s.lr))
        s.batch_seconds = float(metrics.get("train/batch_seconds", metrics.get("train/step_seconds", s.batch_seconds)))
        s.samples_per_second = float(metrics.get("train/samples_per_second", s.samples_per_second))
        s.loader_wait_seconds = float(metrics.get("train/loader_wait_seconds", s.loader_wait_seconds))
        s.gpu_batch_seconds = float(metrics.get("train/gpu_batch_seconds", metrics.get("train/gpu_step_seconds", s.gpu_batch_seconds)))
        s.materialize_seconds = float(metrics.get("train/materialize_seconds", s.materialize_seconds))
        s.gpu_memory_gib = float(metrics.get("train/gpu_memory_allocated_gib", s.gpu_memory_gib))
        s.cpu_rss_gib = float(metrics.get("train/cpu_rss_gib", s.cpu_rss_gib))
        s.task_losses = {key.replace("train/loss_", ""): float(value) for key, value in metrics.items() if key.startswith("train/loss_")}
        s.availability = {key: float(value) for key, value in metrics.items() if key.endswith("_available_fraction") or key.endswith("_valid_fraction")}
        s.loader_cache = {key.replace("loader/cache/", ""): float(value) for key, value in metrics.items() if key.startswith("loader/cache/")}
        s.loader_window = {key.replace("loader/window/", ""): float(value) for key, value in metrics.items() if key.startswith("loader/window/")}
        s.loader_prefetch = {key.replace("loader/prefetch/", ""): float(value) for key, value in metrics.items() if key.startswith("loader/prefetch/")}
        s.loader_state = {key.replace("loader/state/", ""): float(value) for key, value in metrics.items() if key.startswith("loader/state/")}
        loader_status = {key.replace("loader/status/", ""): value for key, value in metrics.items() if key.startswith("loader/status/")}
        if loader_status:
            s.loader_status = loader_status
        trainer_status = {key.replace("train/status/", ""): value for key, value in metrics.items() if key.startswith("train/status/")}
        if trainer_status:
            s.trainer_status = trainer_status
        cache_state = {key.replace("cache/state/", ""): value for key, value in metrics.items() if key.startswith("cache/state/")}
        if cache_state:
            s.cache_state = cache_state
        s.day_index = int(metrics.get("schedule/day_index", s.day_index))
        s.day_count = int(metrics.get("schedule/day_count", s.day_count))
        s.current_day_samples_seen = int(metrics.get("schedule/current_day_samples_seen", s.current_day_samples_seen))
        s.current_day_sample_count = int(metrics.get("schedule/current_day_sample_count", s.current_day_sample_count))
        if validation_metrics:
            s.validation_metrics = {key: float(value) for key, value in validation_metrics.items()}
            if "val/loss" in validation_metrics:
                s.validation_loss = float(validation_metrics["val/loss"])
        if record_history and s.batch_seconds > 0:
            self.history.append(s.batch_seconds)

    def message(self, text: str) -> None:
        with self._state_lock:
            self.state.last_message = str(text)
            self.messages.append(f"{time.strftime('%H:%M:%S')} {text}")
        if self._rich:
            self.refresh(force=True)
        else:
            print(text, flush=True)

    def refresh(self, *, force: bool = False) -> None:
        with self._refresh_lock:
            self._refresh_unlocked(force=force)

    def _refresh_unlocked(self, *, force: bool = False) -> None:
        if self._rich and self._live is not None:
            now = time.perf_counter()
            if not force and now - self._last_refresh < self.min_refresh_interval:
                return
            self._live.update(self._render(), refresh=True)
            self._last_refresh = now
        elif self.layout == "text":
            print(self._text_line(), flush=True)

    def _render(self) -> Any:
        from rich.align import Align
        from rich.console import Group
        from rich.panel import Panel
        from rich.progress import BarColumn, Progress, TextColumn
        from rich.table import Table
        from rich.text import Text

        with self._state_lock:
            s = self.state
        elapsed_seconds = max(0.0, time.perf_counter() - self.started)
        avg_batch_seconds = self._latest_batch_seconds()
        samples_remaining = max(0, int(s.max_samples) - int(s.samples_clock)) if int(s.max_samples) > 0 else 0
        eta_seconds = (samples_remaining / max(float(s.samples_per_second), 1e-9)) if samples_remaining > 0 and s.samples_per_second > 0 else None

        overall = Progress(
            TextColumn("[bold]Samples"),
            BarColumn(bar_width=None),
            TextColumn("{task.completed:,.0f}/{task.total:,.0f}"),
            TextColumn("{task.percentage:>6.2f}%"),
            expand=True,
        )
        overall_total = max(int(s.max_samples), int(s.samples_clock), 1)
        overall.add_task("samples", total=overall_total, completed=min(int(s.samples_clock), overall_total))

        day_progress = Progress(
            TextColumn("[bold]Current Day"),
            BarColumn(bar_width=None),
            TextColumn("{task.completed:,.0f}/{task.total:,.0f}"),
            TextColumn("{task.percentage:>6.2f}%"),
            expand=True,
        )
        day_total = max(int(s.current_day_sample_count), int(s.current_day_samples_seen), 1)
        day_progress.add_task("day", total=day_total, completed=min(int(s.current_day_samples_seen), day_total))

        summary = Table.grid(expand=False, padding=(0, 4))
        summary.add_column(justify="left", no_wrap=True)
        summary.add_column(justify="left", no_wrap=True)
        max_samples_text = f"{s.samples_clock:,}/{s.max_samples:,}" if s.max_samples > 0 else f"{s.samples_clock:,}"
        summary.add_row(f"[bold]Samples[/] {max_samples_text}", f"[bold]Batch[/] {s.batch_size:,}")
        summary.add_row(f"[bold]Updates[/] {s.update_count:,}", f"[bold]Speed[/] {s.samples_per_second:,.1f}/s")
        summary.add_row(f"[bold]Trainer[/] {s.trainer_status.get('phase', 'initializing')}", f"[bold]Loader[/] {s.loader_status.get('phase', 'waiting')}")
        summary.add_row(f"[bold]Epoch[/] {s.epoch}", f"[bold]Day[/] {s.day_index + 1:,}/{max(s.day_count, 1):,}")
        summary.add_row(f"[bold]Day samples[/] {s.current_day_samples_seen:,}/{max(s.current_day_sample_count, 1):,}", f"[bold]Batch avg[/] {avg_batch_seconds:.3f}s")
        summary.add_row(f"[bold]Elapsed[/] {self._format_duration(elapsed_seconds)}", f"[bold]ETA[/] {self._format_duration(eta_seconds)}")
        summary.add_row(f"[bold]Finish[/] {self._format_finish_time(eta_seconds)}", f"[bold]Precision[/] {s.precision}")
        summary.add_row(f"[bold]Run[/] {s.run_name}", f"[bold]Device[/] {s.device}")
        summary.add_row(f"[bold]Dataset[/] {s.dataset_id}", f"[bold]Params[/] {s.model_parameters:,}")

        learning = Table.grid(expand=False, padding=(0, 1))
        learning.add_column("Metric", justify="right", no_wrap=True)
        learning.add_column("Value", justify="left", no_wrap=True)
        learning.add_row("loss", f"{s.loss:.6f}")
        learning.add_row("active tasks", f"{s.active_task_count:.0f}")
        learning.add_row("learning rate", f"{s.lr:.3e}")
        if s.validation_loss is not None:
            learning.add_row("validation loss", f"{s.validation_loss:.6f}")
        for key, value in list(s.validation_metrics.items())[:8]:
            label = key.replace("val/", "")
            if label != "loss":
                learning.add_row(f"val {label}", f"{value:.5f}")

        task_losses = Table.grid(expand=False, padding=(0, 1))
        task_losses.add_column("Task", justify="right", no_wrap=True)
        task_losses.add_column("Loss", justify="left", no_wrap=True)
        if s.task_losses:
            for key, value in list(s.task_losses.items())[:14]:
                task_losses.add_row(key, f"{value:.6f}")
        else:
            task_losses.add_row("waiting", "--")

        availability = Table.grid(expand=False, padding=(0, 1))
        availability.add_column("Data", justify="right", no_wrap=True)
        availability.add_column("Fraction", justify="left", no_wrap=True)
        if s.availability:
            for key, value in list(s.availability.items())[:14]:
                availability.add_row(key.replace("train/", "").replace("_available_fraction", "").replace("_valid_fraction", ""), f"{value:.3f}")
        else:
            availability.add_row("waiting", "--")

        profile = Table.grid(expand=False, padding=(0, 1))
        profile.add_column("Stage", justify="right", no_wrap=True)
        profile.add_column("Seconds", justify="left", no_wrap=True)
        profile.add_row("batch total", f"{s.batch_seconds:.4f}")
        profile.add_row("loader wait", f"{s.loader_wait_seconds:.4f}")
        profile.add_row("materialize", f"{s.materialize_seconds:.4f}")
        profile.add_row("GPU batch", f"{s.gpu_batch_seconds:.4f}")
        non_loader = max(0.0, float(s.batch_seconds) - float(s.loader_wait_seconds))
        profile.add_row("train compute", f"{non_loader:.4f}")

        memory = Table.grid(expand=False, padding=(0, 1))
        memory.add_column("Metric", justify="right", no_wrap=True)
        memory.add_column("GiB", justify="left", no_wrap=True)
        memory.add_row("GPU allocated", f"{s.gpu_memory_gib:.2f}")
        memory.add_row("process RSS", f"{s.cpu_rss_gib:.2f}")

        loader_progress = Table.grid(expand=False, padding=(0, 1))
        loader_progress.add_column("Metric", justify="right", no_wrap=True)
        loader_progress.add_column("Value", justify="left", no_wrap=True)
        loader_progress.add_column("Metric", justify="right", no_wrap=True)
        loader_progress.add_column("Value", justify="left", no_wrap=True)
        progress_rows: list[tuple[str, str]] = [("phase", str(s.loader_status.get("phase", "waiting")))]
        if s.loader_status.get("current_day"):
            progress_rows.append(("day", str(s.loader_status.get("current_day"))))
        if s.loader_window.get("start_timestamp_us") is not None or s.loader_window.get("end_timestamp_us") is not None:
            progress_rows.append(
                (
                    "window UTC",
                    f"{_timestamp_us_text(s.loader_window.get('start_timestamp_us'))} -> {_timestamp_us_text(s.loader_window.get('end_timestamp_us'))}",
                )
            )
        progress_items = [
            ("total origins", s.loader_cache.get("total_available_origins")),
            ("ticker folders", s.loader_cache.get("ticker_package_count")),
            ("unique tickers", s.loader_cache.get("ticker_count")),
            ("origin parts", s.loader_cache.get("part_count")),
            ("day parts", s.loader_window.get("day_package_count")),
            ("day tickers", s.loader_window.get("day_ticker_count")),
            ("day refs", s.loader_window.get("day_refs_total")),
            ("day remaining", s.loader_window.get("day_refs_remaining_before_window")),
            ("window refs", s.loader_window.get("active_refs")),
            ("window parts", s.loader_window.get("active_parts")),
            ("window tickers", s.loader_window.get("active_tickers")),
            ("window seconds", s.loader_window.get("seconds")),
            ("chrono cursor", s.loader_state.get("chronological_origin_cursor")),
        ]
        progress_rows.extend(_formatted_metric_rows(progress_items))
        _add_two_column_rows(loader_progress, progress_rows)

        cache_state = Table.grid(expand=False, padding=(0, 1))
        cache_state.add_column("Cache", justify="right", no_wrap=True)
        cache_state.add_column("State", justify="left", no_wrap=True)
        cache_state.add_column("Cache", justify="right", no_wrap=True)
        cache_state.add_column("State", justify="left", no_wrap=True)
        cache_rows = _formatted_metric_rows(
            [
                ("loader phase", s.cache_state.get("loader_phase")),
                ("trainer phase", s.cache_state.get("trainer_phase")),
                ("current day", s.cache_state.get("current_day")),
                ("event tickers", s.cache_state.get("event_tickers")),
                ("event rows/ticker", s.cache_state.get("event_rows_per_ticker")),
                ("event features", s.cache_state.get("event_feature_count")),
                ("event MiB", s.cache_state.get("event_estimated_mib")),
                ("origin parts", s.cache_state.get("origin_parts")),
                ("origin rows", s.cache_state.get("origin_rows")),
                ("payload parts", s.cache_state.get("payload_parts")),
                ("payload limit", s.cache_state.get("payload_limit")),
                ("ready batches", s.cache_state.get("ready_batches")),
                ("ready samples", s.cache_state.get("ready_samples")),
                ("ready chunks", s.cache_state.get("ready_chunks")),
                ("raw ready", s.cache_state.get("raw_ready_batches")),
                ("raw limit", s.cache_state.get("raw_ready_limit")),
                ("raw produced", s.cache_state.get("raw_produced_batches")),
                ("raw consumed", s.cache_state.get("raw_consumed_batches")),
                ("raw thread", s.cache_state.get("raw_thread_alive")),
                ("mat pending", s.cache_state.get("materialize_pending_batches")),
                ("mat max", s.cache_state.get("materialize_max_pending_batches")),
                ("text idx", s.cache_state.get("text_index_entries")),
                ("label idx", s.cache_state.get("label_index_entries")),
                ("scanner idx", s.cache_state.get("scanner_index_entries")),
                ("bar idx", s.cache_state.get("bar_index_entries")),
                ("xbrl idx", s.cache_state.get("xbrl_index_entries")),
                ("xbrl cats", s.cache_state.get("xbrl_category_entries")),
                ("corp idx", s.cache_state.get("corporate_action_index_entries")),
            ]
        )
        if not cache_rows:
            cache_rows = [("waiting", "--")]
        _add_two_column_rows(cache_state, cache_rows)

        retained_messages = list(self.messages) if self.messages else [s.last_message or "waiting for first update"]
        retained_messages.extend([""] * max(0, self.messages.maxlen - len(retained_messages)))
        messages = Table.grid(expand=True)
        messages.add_column(no_wrap=True, overflow="ellipsis")
        for line in retained_messages[: self.messages.maxlen]:
            messages.add_row(line)

        return Group(
            Panel(summary, title="Temporal v3 Training Run", border_style="cyan"),
            overall,
            day_progress,
            Panel(Align.center(learning), title="Learning", border_style="magenta"),
            Panel(Align.center(task_losses), title="Task Losses", border_style="green"),
            Panel(Align.center(availability), title="Data Availability", border_style="blue"),
            Panel(Align.center(profile), title="Batch Profile", border_style="yellow"),
            Panel(Align.center(loader_progress), title="Loader Progress", border_style="green"),
            Panel(Align.center(cache_state), title="Cache State", border_style="yellow"),
            Panel(Align.center(memory), title="Memory", border_style="cyan", height=6),
            Panel(messages, title="Messages", border_style="blue", height=9),
            Text("\n" * self._bottom_padding_lines),
        )

    def _text_line(self) -> str:
        s = self.state
        return f"samples={s.samples_clock:,} updates={s.update_count:,} loss={s.loss:.5f} sps={s.samples_per_second:.1f}"


def _duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def _timestamp_us_text(value: Any) -> str:
    try:
        timestamp_us = int(float(value))
    except (TypeError, ValueError, OverflowError):
        return "--"
    if timestamp_us <= 0:
        return "--"
    return dt.datetime.fromtimestamp(timestamp_us / 1_000_000.0, tz=dt.timezone.utc).strftime("%H:%M:%S")


def _format_metric_value(label: str, value: Any) -> str:
    if value is None:
        return "--"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, str):
        return value
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return str(value)
    if not numeric.is_integer():
        return f"{numeric:,.2f}"
    if label.endswith("MiB") or label.endswith("sec") or "seconds" in label:
        return f"{numeric:,.2f}"
    return f"{numeric:,.0f}"


def _formatted_metric_rows(items: list[tuple[str, Any]]) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for label, value in items:
        if value is None:
            continue
        rows.append((label, _format_metric_value(label, value)))
    return rows


def _add_two_column_rows(table: Any, rows: list[tuple[str, str]]) -> None:
    if not rows:
        rows = [("waiting", "--")]
    left_count = (len(rows) + 1) // 2
    left_rows = rows[:left_count]
    right_rows = rows[left_count:]
    for index, (left_label, left_value) in enumerate(left_rows):
        if index < len(right_rows):
            right_label, right_value = right_rows[index]
            table.add_row(left_label, left_value, right_label, right_value)
        else:
            table.add_row(left_label, left_value, "", "")
