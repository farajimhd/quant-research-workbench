"""Operator progress at durable batch boundaries; replay cursor is provisional."""
from dataclasses import dataclass
import json
import os
from pathlib import Path
from time import monotonic

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.text import Text


@dataclass
class Snapshot:
    status: str = "Starting"
    stage: str = "Source catalogue"
    focus: str = ""
    completed: int = 0
    total: int = 0
    skipped: int = 0
    failed: int = 0
    invalid: int = 0
    valid: int = 0
    ticks: int = 0
    total_ticks: int = 0
    batch: int = 0
    listings: int = 0
    replay_seconds: float = 0
    compile_seconds: float = 0
    tape_gib: float = 0
    gpu_gib: float = 0
    gpu_total_gib: float = 0
    message: str = "Checking source identities; no strategy replay yet"
    output: str = ""


def duration(seconds):
    seconds = max(0, int(seconds))
    return f"{seconds//3600:02}:{seconds//60%60:02}:{seconds%60:02}"


def render(s, elapsed, *, width=100, height=24):
    """Bound the panel by terminal dimensions; never count in-flight work as durable."""
    color = "red" if s.status == "Failed" else "yellow" if s.status == "Interrupted" else "cyan"
    queued = max(0, s.total - s.completed)
    lines = [Text(f"{s.status} | {s.stage} | {duration(elapsed)} elapsed", style=color),
             Text(s.focus or "Workstation squeeze grid · America/New_York", overflow="ellipsis", no_wrap=True),
             Text(f"Saved {s.completed:,}/{s.total:,} configurations · queued {queued:,} · failed {s.failed:,}"),
             Text(f"Valid {s.valid:,} · invalid {s.invalid:,} · verified/reused {s.skipped:,}")]
    if height >= 12:
        tick = f"{s.ticks:,}/{s.total_ticks:,} s" if s.total_ticks else "not started"
        lines.append(Text(f"Current replay {tick} · batch {s.batch or '—'} · tickers {s.listings or '—'}"))
        replayed = s.completed - s.skipped
        rate = replayed / s.replay_seconds if s.replay_seconds > 0 else None
        eta = queued / rate if rate and replayed >= max(3*s.batch, 1) else None
        lines.append(Text(f"Saved-config rate {rate:.2f}/s · replay-only remaining {duration(eta)}"
                          if eta else "Rate/ETA: warming up; preparation and compilation excluded"))
    if height >= 16:
        lines.append(Text(f"Replay {duration(s.replay_seconds)} · compile {s.compile_seconds:.1f}s · tape {s.tape_gib:.2f} GiB"))
        lines.append(Text(f"GPU allocated {s.gpu_gib:.2f}/{s.gpu_total_gib:.1f} GiB · Ctrl+C preserves saved batches"))
    lines.append(Text(s.message, overflow="ellipsis", no_wrap=True, style="bold" if s.status in ("Failed", "Interrupted") else ""))
    if s.output and height >= 18:
        lines.append(Text("Results: " + s.output, overflow="ellipsis", no_wrap=True, style="dim"))
    if width < 76:
        # Keep all important state visible, with explicit smaller comparable rows.
        lines[2] = Text(f"Saved {s.completed:,}/{s.total:,} · queued {queued:,}")
        lines[3] = Text(f"Failed {s.failed} · invalid {s.invalid} · reused {s.skipped}")
    return Panel(Group(*lines), title="Squeeze grid v2", border_style=color,
                 width=max(30, width), padding=(0, 1))


class Progress:
    def __init__(self, path, *, console=None, plain=False):
        self.console = console or Console(no_color=bool(os.environ.get("NO_COLOR")))
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.snapshot = Snapshot(output=str(self.path.parent))
        self.started = monotonic()
        self.live = None
        self.interactive = self.console.is_terminal and not plain
        self.last_plain = 0

    def __enter__(self):
        if self.interactive:
            self.live = Live(get_renderable=self.view, console=self.console, refresh_per_second=1,
                             transient=False, vertical_overflow="crop")
            self.live.start()
        return self

    def view(self):
        if self.snapshot.gpu_total_gib:
            import torch
            self.snapshot.gpu_gib = torch.cuda.memory_allocated()/1024**3
        return render(self.snapshot, monotonic()-self.started,
                      width=self.console.size.width, height=self.console.size.height)

    def emit(self, event):
        elapsed = monotonic()-self.started
        for key, value in event.items():
            if hasattr(self.snapshot, key):
                setattr(self.snapshot, key, value)
        # Event log preserves stage transitions and coarse replay cursor, not raw market rows.
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"elapsed_seconds": elapsed, **event}, allow_nan=False) + "\n")
        if self.live:
            self.live.update(self.view())
        elif elapsed-self.last_plain >= 15 or "stage" in event or "status" in event:
            s = self.snapshot
            self.console.print(f"[{duration(elapsed)}] {s.status}: {s.stage} | {s.focus} | "
                f"saved {s.completed}/{s.total}, failed {s.failed}, invalid {s.invalid} | {s.message}", markup=False)
            self.last_plain = elapsed

    def __exit__(self, kind, value, traceback):
        if kind:
            self.emit({"status": "Interrupted" if issubclass(kind, KeyboardInterrupt) else "Failed",
                       "failed": self.snapshot.failed + (0 if issubclass(kind, KeyboardInterrupt) else 1),
                       "message": f"{kind.__name__}; saved batches retained. Inspect job/campaign receipts and resume."})
            receipt = self.path.parent / "job.json"
            if receipt.exists():
                from .runtime import write_json
                state = json.loads(receipt.read_text())
                state.update(status="interrupted" if issubclass(kind, KeyboardInterrupt) else "failed",
                             failure_type=kind.__name__, failure_stage=self.snapshot.stage)
                write_json(receipt, state)
        if self.live:
            self.live.update(self.view(), refresh=True)
            self.live.stop()
