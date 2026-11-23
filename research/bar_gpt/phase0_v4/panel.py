"""Read-only Rich monitor: training owns progress.json; this viewer owns no job."""

import os

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

import argparse
import json
import time
import sys
from pathlib import Path
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.columns import Columns
from rich.table import Table
from rich.text import Text

TERMINAL_STATES = {
    "complete",
    "failed",
    "stopped",
    "bounded_complete",
    "qualification_complete",
    "interrupted_use_last_checkpoint",
}

# Display order follows the canonical six Phase1 timeframes. Reading the already
# reduced report avoids importing Torch or doing any validation work in the viewer.
DISPLAY_FRAMES = ("100ms", "1s", "10s", "1m", "1h", "daily")


class EvaluationReader:
    """Cache complete evaluation reports; never mix metrics from different steps."""

    def __init__(self, root):
        self.root, self.stamp, self.report = root, None, {}

    def enrich(self, state):
        latest = state.get("latest_evaluation", {})
        scope = latest.get("scope")
        if scope not in ("development", "validation"):
            return state
        path = self.root / f"{scope}-latest.json"
        try:
            stamp = (scope, path.stat().st_mtime_ns)
            if stamp != self.stamp:
                report = json.loads(path.read_text(encoding="utf-8"))
                self.report, self.stamp = report, stamp
        except (OSError, ValueError):
            pass
        state = dict(state)
        state["timeframe_metrics"] = (
            self.report.get("metrics", {})
            if self.stamp and self.stamp[0] == scope and self.report.get("step") == latest.get("step")
            else {}
        )
        return state


def number(value, suffix="", precision=2):
    """Unknown is not zero; omit numerical claims until a measurement exists."""
    return "n/a" if value is None else f"{value:,.{precision}f}{suffix}"


def enable_virtual_terminal():
    """Enable buffered ANSI frames instead of legacy per-cell console writes."""
    if os.name != "nt" or not sys.stdout.isatty():
        return os.name != "nt"
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetStdHandle.restype = wintypes.HANDLE
    kernel.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    handle = kernel.GetStdHandle(-11)
    mode = wintypes.DWORD()
    if not kernel.GetConsoleMode(handle, ctypes.byref(mode)):
        return False
    return bool(kernel.SetConsoleMode(handle, mode.value | 0x0004))


class SolidBar:
    """Use solid cells even on consoles where Rich's thin bar looks like dashes."""

    def __init__(self, total, completed):
        self.fraction = min(1.0, max(0.0, completed / total))

    def __rich_console__(self, console, options):
        width = max(1, options.max_width - 8)
        filled = round(width * self.fraction)
        bar = Text("\u2588" * filled, style="bold cyan")
        bar.append("\u2591" * (width - filled), style="bright_black")
        bar.append(f" {self.fraction:6.1%}")
        yield bar


def render(state, now=None, width=120):
    """Pure renderer, allowing narrow-terminal and stale-state tests without GPU."""
    now = time.time() if now is None else now
    age = now - state.get("updated", 0)
    status = state.get("state", "waiting for trainer")
    if status in TERMINAL_STATES:
        now = state.get("updated", now)
    stale = age > 30 and status not in TERMINAL_STATES
    color = "red" if status == "failed" or stale else "green" if status == "complete" else "cyan"
    heading = f"{status.upper()} | {state.get('stage', 'no status received')}"
    coverage_mismatch = bool(state.get("validation_complete")) and (
        state.get("validation_windows") != state.get("validation_total")
    )
    if coverage_mismatch:
        heading += " | COVERAGE MISMATCH"
        color = "red"
    if stale:
        heading += " | STALE: check trainer"
    elif (
        state.get("stage") in ("training", "development", "validation")
        and state.get("last_progress_at")
        and now - state["last_progress_at"] > 300
        and status not in TERMINAL_STATES
    ):
        heading += " | NO PROGRESS >5 min"
        color = "yellow"
    progress = Table.grid(expand=True)
    progress.add_column(ratio=1)
    for label, done, total in (
        (
            f"Training epoch {state.get('epoch', 'n/a')}/{state.get('epochs', 'n/a')}",
            state.get("epoch_done", 0),
            state.get("epoch_total", 0),
        ),
        (
            "Validation"
            + (
                " complete"
                if state.get("validation_complete") and not coverage_mismatch
                else " (partial)"
            ),
            state.get("validation_windows", 0),
            state.get("validation_total", 0),
        ),
    ):
        progress.add_row(
            Text(f"{label} | {done:,}/{total:,} windows" if total else f"{label} | not started")
        )
        if total:
            progress.add_row(SolidBar(total, done))
    performance = Table(
        title="TRAINING & RESOURCES", title_justify="left", box=None, padding=(0, 1)
    )
    performance.add_column("Metric", style="dim", width=25)
    performance.add_column("Value", justify="right", style="cyan", width=23)
    rows = (
        ("Train speed", number(state.get("windows_per_second"), " windows/s")),
        ("Validation speed", number(state.get("validation_windows_per_second"), " windows/s")),
        (
            "Train / validation ETA",
            number(state.get("epoch_eta_seconds"), " s", 0)
            + " / "
            + number(state.get("validation_eta_seconds"), " s", 0),
        ),
        (
            "Train loss",
            number(state.get("loss"), precision=5),
        ),
        ("Learning rate", f"{state['learning_rate']:.2e}" if state.get("learning_rate") else "n/a"),
        (
            "Loader startup / wait",
            number(state.get("loader_startup_seconds"), " s")
            + " / "
            + number(state.get("loader_wait_seconds"), " s"),
        ),
        (
            "GPU utilization / memory",
            number(state.get("gpu_util"), "%", 0)
            + " / "
            + number(state.get("gpu_memory_mib"), " MiB", 0),
        ),
    )
    for left, right in rows:
        performance.add_row(left, Text(right, style="yellow" if left == "Train loss" else "cyan"))
    latest = state.get("latest_evaluation", {})
    quality = Table(title="LATEST EVALUATION", title_justify="left", box=None, padding=(0, 1))
    quality.add_column("Metric", style="dim", width=24)
    quality.add_column("Value", justify="right", width=22)
    for label, value, style in (
        ("Scope / step", f"{latest.get('scope', 'pending')} / {latest.get('step', 'n/a')}", "cyan"),
        ("Loss", number(latest.get("loss"), precision=5), "yellow"),
    ):
        quality.add_row(label, Text(value, style=style))
    metrics = state.get("timeframe_metrics", {})
    frames = Table(title="LATEST COMPLETED EVALUATION BY TIMEFRAME", title_justify="left",
                   box=None, padding=(0, 1), expand=True)
    for label, style in (("Frame", "cyan"), ("Trade close\nMAE $", "yellow"),
                         ("Trade\ndir %", "magenta"), ("Bid\ndir %", "magenta"),
                         ("Ask\ndir %", "magenta"), ("Volume\nbal dir %", "green"),
                         ("Bid size\nbal dir %", "green"), ("Ask size\nbal dir %", "green")):
        frames.add_column(label, justify="left" if label == "Frame" else "right", style=style)
    for frame in DISPLAY_FRAMES:
        frames.add_row(frame, number(metrics.get(f"raw_mae/{frame}/trade_close"), precision=4),
            *[number(metrics.get(f"candle/{frame}/{family}/moving_direction_pct"))
              for family in ("trade", "bid", "ask")],
            *[number(metrics.get(f"activity_direction/{frame}/{channel}/balanced_accuracy_pct"))
              for channel in ("trade_volume", "bid_size", "ask_size")])
    resources = Columns([performance, quality], padding=(0, 3))
    legend = "Price direction: open to close, moving candles. Activity: previous bar, class-balanced."
    if width < 100:
        # Keep all six frames and all three families visible on small consoles;
        # combine values with an explicit order rather than truncating numbers.
        frames = Table(title="COMPLETED EVALUATION", box=None, padding=(0, 1))
        for label in ("Frame", "Close $", "Price dir %\nT / B / A", "Activity bal %\nV / B / A"):
            frames.add_column(label, justify="right")
        for frame in DISPLAY_FRAMES:
            price = [metrics.get(f"candle/{frame}/{f}/moving_direction_pct") for f in ("trade", "bid", "ask")]
            activity = [metrics.get(f"activity_direction/{frame}/{f}/balanced_accuracy_pct") for f in ("trade_volume", "bid_size", "ask_size")]
            frames.add_row(frame, number(metrics.get(f"raw_mae/{frame}/trade_close")),
                           "/".join(number(v, precision=1) for v in price),
                           "/".join(number(v, precision=1) for v in activity))
        resources = Group(
            Text(f"Train {number(state.get('windows_per_second'), precision=0)} | eval {number(state.get('validation_windows_per_second'), precision=0)} windows/s", style="cyan"),
            Text(f"Train loss {number(state.get('loss'))} | eval loss {number(latest.get('loss'))}", style="yellow"),
            Text(f"{latest.get('scope', 'pending')} at step {latest.get('step', 'n/a')}")
        )
        legend = "T=trade, B=bid, A=ask, V=volume. Activity: balanced."
    content = [
        Text(heading, style=color),
        progress,
        resources,
        frames,
        Text(legend, style="dim"),
    ]
    if state.get("error"):
        content.append(Text(str(state["error"]), style="red"))
    content.append(Text(f"Step {state.get('step', 'n/a')} | saved {state.get('checkpoint_step', 'n/a')} | windows/s (64 bars each)", style="dim"))
    return Panel(Group(*content), title="Phase0 V4 | same-bar reconstruction", border_style=color)


def main():
    """Use --once for redirected logs; never emit cursor control to a pipe."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--once", action="store_true")
    parser.add_argument(
        "--keep-open", action="store_true", help="Keep final status visible until Ctrl+C"
    )
    args = parser.parse_args()
    # Redirected Windows streams often default to cp1252, which cannot encode
    # solid bars. Real consoles and UTF-8 log readers both support these glyphs.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    vt = enable_virtual_terminal()
    if vt and os.environ.get("TERM") in ("dumb", "unknown"):
        # Capability was checked on the actual console handle, so an inherited
        # noninteractive launcher label must not disable its live rendering.
        os.environ["TERM"] = "xterm-256color"
    console = Console(legacy_windows=False if vt else None)
    last = {}
    evaluation = EvaluationReader(args.run)

    def read():
        nonlocal last
        try:
            last = json.loads((args.run / "progress.json").read_text())
        except (OSError, ValueError):
            # Keep the last trustworthy observation if a publication is briefly
            # unavailable. Its age still increases, making staleness visible.
            pass
        return evaluation.enrich(last)

    if args.once or not console.is_terminal:
        console.print(render(read(), width=console.width))
        return
    try:
        # Home and overwrite a buffered frame; do not erase every previous line.
        # This is the ONLY refresh loop, avoiding duplicate timer-driven paints.
        with Live(render(read(), width=console.width), console=console, screen=vt, auto_refresh=False) as live:
            frames = 0
            while True:
                state = read()
                live.update(render(state, width=console.width), refresh=True)
                frames += 1
                if frames == 1 or frames % 10 == 0:
                    # This viewer receipt proves the interactive mode and advancing
                    # redraws without modifying the trainer's progress authority.
                    receipt = dict(pid=os.getpid(), updated=time.time(), frames=frames,
                                   virtual_terminal=vt, alternate_screen=vt,
                                   columns=console.width, rows=console.height)
                    (args.run / "panel-status.json").write_text(json.dumps(receipt), encoding="utf-8")
                if state.get("state") in TERMINAL_STATES and not args.keep_open:
                    break
                time.sleep(1)
    except KeyboardInterrupt:
        console.print("Viewer closed; trainer was not interrupted.")


if __name__ == "__main__":
    main()
