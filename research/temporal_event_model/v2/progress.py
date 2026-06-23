from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class TemporalProgressState:
    run_name: str = ""
    device: str = ""
    epoch: int = 0
    epochs: int = 0
    block: int = 0
    blocks_per_epoch: int = 0
    step: int = 0
    batch_size: int = 0
    lr: float = 0.0
    train_loss: float = 0.0
    train_mae_bps: float = 0.0
    train_sign_accuracy: float = 0.0
    val_loss: float = 0.0
    val_mae_bps: float = 0.0
    val_sign_accuracy: float = 0.0
    step_seconds: float = 0.0
    data_seconds: float = 0.0
    encode_seconds: float = 0.0
    train_seconds: float = 0.0
    samples_per_second: float = 0.0
    gpu_allocated_gib: float = 0.0
    gpu_reserved_gib: float = 0.0
    elapsed_seconds: float = 0.0
    eta_hours: float = 0.0
    messages: deque[str] = field(default_factory=lambda: deque(maxlen=6))


class TemporalTrainingReporter:
    def __init__(self, *, enabled: bool = True) -> None:
        self.enabled = bool(enabled)
        self.started = time.perf_counter()
        self.state = TemporalProgressState()
        self._live: Any | None = None
        self._rich_ready = False
        if not self.enabled:
            return
        try:
            from rich.live import Live

            self._Live = Live
            self._rich_ready = True
        except Exception:
            self._rich_ready = False

    def __enter__(self) -> "TemporalTrainingReporter":
        if self.enabled and self._rich_ready:
            self._live = self._Live(self._render(), refresh_per_second=2, transient=False)
            self._live.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._live is not None:
            self._live.__exit__(exc_type, exc, tb)
            self._live = None

    def message(self, text: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        self.state.messages.append(f"{stamp} {text}")
        self.refresh()
        if not self.enabled or not self._rich_ready:
            print(text, flush=True)

    def update(self, **values: Any) -> None:
        for key, value in values.items():
            if hasattr(self.state, key):
                setattr(self.state, key, value)
        self.state.elapsed_seconds = time.perf_counter() - self.started
        total_steps = max(1, self.state.epochs * self.state.blocks_per_epoch)
        completed_steps = max(0, (self.state.epoch - 1) * self.state.blocks_per_epoch + self.state.block)
        if completed_steps > 0:
            remaining = max(0, total_steps - completed_steps)
            seconds_per_block = self.state.elapsed_seconds / completed_steps
            self.state.eta_hours = remaining * seconds_per_block / 3600.0
        self.refresh()

    def refresh(self) -> None:
        if self._live is not None:
            self._live.update(self._render())

    def _render(self):
        from rich import box
        from rich.console import Group
        from rich.panel import Panel
        from rich.table import Table

        def kv_table(rows: list[tuple[str, str]]) -> Table:
            table = Table.grid(padding=(0, 1))
            table.add_column(justify="right", style="bold")
            table.add_column(justify="left")
            for key, value in rows:
                table.add_row(key, value)
            return table

        s = self.state
        run = kv_table(
            [
                ("run", s.run_name),
                ("device", s.device),
                ("epoch", f"{s.epoch}/{s.epochs}"),
                ("block", f"{s.block}/{s.blocks_per_epoch}"),
                ("step", f"{s.step:,}"),
                ("batch", f"{s.batch_size:,}"),
                ("elapsed", f"{s.elapsed_seconds / 3600.0:.2f}h"),
                ("eta", f"{s.eta_hours:.2f}h"),
            ]
        )
        train = kv_table(
            [
                ("loss", f"{s.train_loss:.6f}"),
                ("mae", f"{s.train_mae_bps:.3f} bps"),
                ("sign acc", f"{100.0 * s.train_sign_accuracy:.2f}%"),
                ("lr", f"{s.lr:.3e}"),
                ("speed", f"{s.samples_per_second:,.0f} samples/s"),
            ]
        )
        validation = kv_table(
            [
                ("loss", f"{s.val_loss:.6f}"),
                ("mae", f"{s.val_mae_bps:.3f} bps"),
                ("sign acc", f"{100.0 * s.val_sign_accuracy:.2f}%"),
            ]
        )
        profile = kv_table(
            [
                ("step", f"{s.step_seconds:.3f}s"),
                ("data", f"{s.data_seconds:.3f}s"),
                ("encode", f"{s.encode_seconds:.3f}s"),
                ("train", f"{s.train_seconds:.3f}s"),
                ("gpu alloc", f"{s.gpu_allocated_gib:.2f} GiB"),
                ("gpu reserved", f"{s.gpu_reserved_gib:.2f} GiB"),
            ]
        )
        messages = "\n".join(s.messages) if s.messages else "No messages yet."
        return Group(
            Panel(run, title="Run", border_style="cyan", box=box.ROUNDED),
            Panel(train, title="Training", border_style="magenta", box=box.ROUNDED),
            Panel(validation, title="Validation", border_style="green", box=box.ROUNDED),
            Panel(profile, title="Profile", border_style="yellow", box=box.ROUNDED),
            Panel(messages, title="Messages", border_style="blue", box=box.ROUNDED),
        )

