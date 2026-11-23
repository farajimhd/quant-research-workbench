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


def number(value, suffix="", precision=2):
    """Unknown is not zero; omit numerical claims until a measurement exists."""
    return "n/a" if value is None else f"{value:,.{precision}f}{suffix}"


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


def render(state, now=None):
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
    performance.add_column("Metric", style="dim")
    performance.add_column("Value", justify="right", style="cyan")
    rows = (
        (
            "Session elapsed",
            (
                number(now - state["session_started"], " s", 0)
                if state.get("session_started")
                else "n/a"
            ),
        ),
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
            "Loader startup / epoch wait",
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
        (
            "Update / saved update",
            f"{state.get('step', 'n/a')} / {state.get('checkpoint_step', 'n/a')}",
        ),
    )
    for left, right in rows:
        performance.add_row(left, Text(right, style="yellow" if left == "Train loss" else "cyan"))
    latest = state.get("latest_evaluation", {})
    quality = Table(title="LATEST EVALUATION", title_justify="left", box=None, padding=(0, 1))
    quality.add_column("Metric", style="dim")
    quality.add_column("Value", justify="right")
    for label, value, style in (
        ("Scope / step", f"{latest.get('scope', 'pending')} / {latest.get('step', 'n/a')}", "cyan"),
        ("Loss", number(latest.get("loss"), precision=5), "yellow"),
        ("1s close MAE", number(latest.get("close_mae"), " $", 4), "yellow"),
        ("1s candle direction", number(latest.get("candle_direction"), "%"), "magenta"),
        ("1s volume balanced dir.", number(latest.get("activity_direction"), "%"), "magenta"),
    ):
        quality.add_row(label, Text(value, style=style))
    content = [
        Text(heading, style=color),
        progress,
        Columns([performance, quality], padding=(0, 3)),
    ]
    if state.get("error"):
        content.append(Text(str(state["error"]), style="red"))
    content.append(Text("Rates: windows/s. Each window supplies 64 independent bars.", style="dim"))
    return Panel(Group(*content), title="Phase0 V4 | same-bar reconstruction", border_style=color)


def main():
    """Use --once for redirected logs; never emit cursor control to a pipe."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--keep-open", action="store_true", help="Keep final status visible until Ctrl+C")
    args = parser.parse_args()
    # Redirected Windows streams often default to cp1252, which cannot encode
    # solid bars. Real consoles and UTF-8 log readers both support these glyphs.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    console = Console()
    last = {}

    def read():
        nonlocal last
        try:
            last = json.loads((args.run / "progress.json").read_text())
        except (OSError, ValueError):
            # Keep the last trustworthy observation if a publication is briefly
            # unavailable. Its age still increases, making staleness visible.
            pass
        return last

    if args.once or not console.is_terminal:
        console.print(render(read()))
        return
    try:
        with Live(render(read()), console=console, refresh_per_second=1) as live:
            while True:
                state = read()
                live.update(render(state))
                if state.get("state") in TERMINAL_STATES and not args.keep_open:
                    break
                time.sleep(1)
    except KeyboardInterrupt:
        console.print("Viewer closed; trainer was not interrupted.")


if __name__ == "__main__":
    main()
