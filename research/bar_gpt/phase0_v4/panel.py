"""Read-only Rich monitor: training owns progress.json; this viewer owns no job."""

import os

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

import argparse
import json
import time
from pathlib import Path
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.progress_bar import ProgressBar
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


def render(state, now=None):
    """Pure renderer, allowing narrow-terminal and stale-state tests without GPU."""
    now = time.time() if now is None else now
    age = now - state.get("updated", 0)
    status = state.get("state", "waiting for trainer")
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
            progress.add_row(ProgressBar(total=total, completed=done))
    performance = Table.grid(padding=(0, 2), expand=True)
    performance.add_column()
    performance.add_column(justify="right")
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
            "Loss / learning rate",
            number(state.get("loss"), precision=5)
            + " / "
            + (f"{state['learning_rate']:.2e}" if state.get("learning_rate") else "n/a"),
        ),
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
        performance.add_row(Text(left), Text(right))
    latest = state.get("latest_evaluation", {})
    quality = Text(
        f"Latest {latest.get('scope', 'evaluation pending')} | step {latest.get('step', 'n/a')}\n"
    )
    quality.append("Evaluation loss " + number(latest.get("loss"), precision=5) + "\n")
    quality.append("1s close MAE " + number(latest.get("close_mae"), " $", 4))
    quality.append(" | candle direction " + number(latest.get("candle_direction"), "%"))
    quality.append(
        "\n1s volume balanced direction " + number(latest.get("activity_direction"), "%")
    )
    content = [Text(heading, style=color), progress, performance, quality]
    if state.get("error"):
        content.append(Text(str(state["error"]), style="red"))
    content.append(
        Text("Rates count windows; each window supplies64 independent bar examples.", style="dim")
    )
    return Panel(Group(*content), title="Phase0 V4 | same-bar reconstruction", border_style=color)


def main():
    """Use --once for redirected logs; never emit cursor control to a pipe."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
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
                if state.get("state") in TERMINAL_STATES:
                    break
                time.sleep(1)
    except KeyboardInterrupt:
        console.print("Viewer closed; trainer was not interrupted.")


if __name__ == "__main__":
    main()
