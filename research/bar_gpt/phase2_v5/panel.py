"""Read-only Phase 2 forecast monitor for the live V5 run."""
import argparse
import json
import math
import os
from pathlib import Path
import sys
import time

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ..phase1_v29.panel import SolidBar, enable_virtual_terminal, maximize_console

HORIZONS = tuple(
    (frame, n)
    for frame, count in (("1s", 10), ("10s", 6), ("1m", 1), ("1h", 1), ("daily", 1))
    for n in range(1, count + 1)
)
SUMMARY = (("1s", 1), ("1s", 10), ("10s", 1), ("10s", 6), ("1m", 1), ("1h", 1), ("daily", 1))
TERMINAL = {"complete", "failed", "stopped"}


class Reader:
    """Keep complete snapshots when the producer replaces a report or a read fails."""

    def __init__(self, root):
        self.root = root
        self.cached = {}

    def read(self, name):
        path = self.root / name
        stamp, payload = self.cached.get(name, (None, {}))
        try:
            info = path.stat()
            current = (info.st_mtime_ns, info.st_size)
            if current != stamp:
                value = json.loads(path.read_text(encoding="utf-8-sig"))
                if isinstance(value, dict):
                    self.cached[name] = (current, value)
                    return value
        except (OSError, ValueError):
            pass
        return payload

    def snapshot(self):
        return (
            self.read("progress.json"), self.read("checkpoint_last.proof.json"),
            self.read("development-latest.json"), self.read("validation-final.json"),
            self.read("manifest.json"),
        )


def num(value, fmt=",.0f", suffix=""):
    return f"{value:{fmt}}{suffix}" if isinstance(value, (int, float)) and math.isfinite(value) else "—"


def delta_color(model, baseline):
    if model is None or baseline is None:
        return "dim"
    if model < baseline:
        return "green"
    return "yellow" if model > baseline else "dim"


def forecast_table(report, scope, width, height):
    title = f"{scope}  ·  step {num(report.get('step'))}  ·  {num(report.get('origins'))} origins"
    if width < 52:
        title = f"{scope} · step {num(report.get('step'))}"
    metrics = Table(title=title, title_justify="left", title_style="bold", expand=True,
                    box=None, padding=(0, 1))
    metrics.add_column("Frame / bar", no_wrap=True, style="cyan")
    columns = [("Close $", "close"), ("Last $", "baseline"), ("Origin bal%", "origin")]
    if width < 52:
        columns = [("MAE $", "close"), ("Last $", "baseline"), ("Dir%", "origin")]
    if width >= 72:
        columns.insert(2, ("Skill %", "skill"))
        columns.append(("Candle bal%", "candle"))
    if width >= 104:
        columns.extend((("Path bal%", "path"), ("Bid $", "bid"), ("Ask $", "ask"), ("Pairs", "count")))
    for label, _ in columns:
        metrics.add_column(label, justify="right", no_wrap=True)
    rows = HORIZONS if height >= 36 else SUMMARY if height >= 20 else tuple(
        (frame, n) for frame, n in SUMMARY if n == 1 and frame in ("1s", "1m", "daily")
    )
    for frame, n in rows:
        prefix = f"{frame}/h{n:02d}"
        def metric(key):
            return report.get(f"{prefix}/{key}")

        close = metric("trade/comparable_close_mae")
        baseline = metric("trade/persistence_close_mae")
        skill = 100 * (1 - close / baseline) if close is not None and baseline else None
        color = delta_color(close, baseline)
        values = {
            "close": Text(num(close, ",.4f"), style=color),
            "baseline": num(baseline, ",.4f"),
            "skill": Text(num(skill, "+,.1f"), style=color),
            "origin": num(metric("trade/from_last/balanced_accuracy_pct"), ".1f"),
            "candle": num(metric("trade/candle/balanced_accuracy_pct"), ".1f"),
            "path": num(metric("trade/path/balanced_accuracy_pct"), ".1f"),
            "bid": num(metric("bid_close/mae"), ",.4f"),
            "ask": num(metric("ask_close/mae"), ",.4f"),
            "count": num(sum(metric(f"trade/from_last/{label}_count") or 0
                             for label in ("down", "flat", "up")) or None),
        }
        metrics.add_row(f"{frame:>5} / {n:02d}", *(values[key] for _, key in columns))
    return metrics


def render(snapshot, *, width, height, now=None):
    state, checkpoint, development, final, manifest = snapshot
    if not state:
        return Panel("Waiting for progress.json from the trainer.", title="BarGPT · Phase 2 V5")
    now = time.time() if now is None else now
    age = max(0, now - state.get("updated", 0))
    status = state.get("state", "unknown")
    stale = age > 30 and status not in TERMINAL
    color = "red" if status == "failed" or stale else "green" if status == "complete" else "cyan"
    activity = state.get("stage", "initializing")
    title = ("STALE" if stale else status.upper()) + f"  /  {activity}"
    step, saved = state.get("step"), checkpoint.get("step")
    content = [
        Text(title, style=f"bold {color}"),
        Text(f"Step {num(step)} · saved {num(saved)} · {age:.0f}s old"),
    ]
    if state.get("error"):
        content.append(Text(str(state["error"]), style="red", overflow="ellipsis", no_wrap=True))
    fraction = state.get("pass_fraction")
    if status == "complete":
        fraction = 1
    content.append(Text(f"Sessions · {num(state.get('done'))} origins",
                        style="bold"))
    if fraction is not None:
        content.append(SolidBar(1, fraction))

    report = final or development
    cadence = manifest.get("arguments", {}).get("validation_every")
    next_step = ((step // cadence) + 1) * cadence if step is not None and cadence else None
    speed = state.get("windows_per_second")
    lr = state.get("learning_rate")
    gpu = state.get("gpu_util")
    memory = state.get("gpu_memory_mib")
    content.append(Text(f"Train {num(speed, ',.0f', ' origins/s')}  ·  loss {num(state.get('loss'), ',.3f')}", style="bold"))
    if height >= 20:
        content.append(Text(f"LR {num(lr, '.2e')}  ·  GPU {num(gpu, '.0f', '%')} / "
                            f"{num(memory / 1024 if memory is not None else None, '.1f', ' GiB')}"))
    if report:
        content.append(Text(f"Eval {num(report.get('origins_per_second'), ',.0f', ' origins/s')}  ·  "
                            f"loss {num(report.get('loss'), ',.3f')}"
                            + (f"  ·  next dev {num(next_step)}" if next_step and not final and width >= 70 else "")))
    if "validation" in activity and height >= 22:
        content.append(Text(f"Latest eval progress reading  ·  {num(state.get('validation_done'))} origins  ·  "
                            f"{num(state.get('validation_samples_per_second'), ',.0f', ' origins/s')}", style="cyan"))
    if report and height >= 13:
        content.append(forecast_table(report, "FULL VALIDATION" if final else "DEVELOPMENT", width, height))
    elif not report and height >= 13:
        content.append(Text("Waiting for the first completed forecast evaluation.", style="dim"))
    if report and height >= 22:
        content.extend([
            Text("Close $ = trade MAE. Last $ = persistence MAE.",
                 style="dim", overflow="ellipsis", no_wrap=True),
            Text("Origin = direction from last known close.",
                 style="dim", overflow="ellipsis", no_wrap=True),
        ])
    return Panel(Group(*content), title="BarGPT · Phase 2 V5 Forecast", border_style=color)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--maximize", action="store_true")
    args = parser.parse_args()
    if not args.run.is_dir():
        parser.error("--run must be an existing runtime directory")
    if args.maximize and not args.once:
        maximize_console()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    vt = sys.stdout.isatty() and not args.once and enable_virtual_terminal()
    if vt and os.environ.get("TERM") in ("dumb", "unknown"):
        os.environ["TERM"] = "xterm-256color"
    console = Console(legacy_windows=False if vt else None)
    reader = Reader(args.run)

    def frame():
        return render(reader.snapshot(), width=console.width, height=console.height)

    if args.once or not console.is_terminal:
        console.print(frame())
        return
    try:
        with Live(frame(), console=console, screen=vt, auto_refresh=False) as live:
            frames = 0
            while True:
                live.update(frame(), refresh=True)
                frames += 1
                if frames == 1 or frames % 10 == 0:
                    receipt = dict(pid=os.getpid(), updated=time.time(), frames=frames,
                                   virtual_terminal=vt, alternate_screen=vt,
                                   columns=console.width, rows=console.height)
                    (args.run / "forecast-panel-status.json").write_text(
                        json.dumps(receipt), encoding="utf-8"
                    )
                time.sleep(1)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
