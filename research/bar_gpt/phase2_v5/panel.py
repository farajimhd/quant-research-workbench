"""Read-only Phase 2 forecast monitor for the live V5 run."""
import argparse
import json
import math
import os
from pathlib import Path
import sys
from statistics import median
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
        self.prior_step = None
        self.prior = {}

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

    def journal(self, limit):
        try:
            with (self.root / "metrics.jsonl").open("rb") as stream:
                size = stream.seek(0, os.SEEK_END)
                start = max(0, size - limit)
                stream.seek(start)
                if start:
                    stream.readline()
                for line in reversed(stream.read().splitlines()):
                    try:
                        yield json.loads(line)
                    except (ValueError, UnicodeError):
                        continue
        except OSError:
            return

    def snapshot(self):
        dev = self.read("development-latest.json")
        if dev.get("step") != self.prior_step:
            self.prior_step = dev.get("step")
            self.prior = next(
                ({k.removeprefix("development/"): v for k, v in row.items()
                  if k.startswith("development/")}
                 for row in self.journal(8 * 1024**2)
                 if "development/loss" in row and row["step"] < self.prior_step
                 and row.get("development/origins") == dev.get("origins")),
                {},
            )
        recent = []
        for row in self.journal(96 * 1024):
            if "train/loss" in row:
                recent.append(row)
                if len(recent) == 30:
                    break
        return (self.read("progress.json"), self.read("checkpoint_last.proof.json"),
                dev, self.read("validation-final.json"), self.read("manifest.json"),
                recent, self.prior)


def num(value, fmt=",.0f", suffix=""):
    return f"{value:{fmt}}{suffix}" if isinstance(value, (int, float)) and math.isfinite(value) else "—"


def scored(value, reference, fmt, *, higher=False):
    """Compare only like-for-like measurements; leave unavailable comparisons neutral."""
    style = "dim" if value is None else ""
    if value is not None and reference is not None and value != reference:
        style = "green" if (value > reference) == higher else "dark_orange"
    return Text(num(value, fmt), style=style)


def flat_reference(report, key):
    """Balanced recall for an always-flat forecast on this metric's exact target set."""
    counts = [report.get(f"{key}/{label}_count", 0) for label in ("down", "flat", "up")]
    classes = sum(n > 0 for n in counts)
    return 100 / classes if classes and counts[1] else 0 if classes else None


def forecast_table(report, prior, width, detailed):
    table = Table(expand=True, box=None, padding=(0, 1))
    table.add_column("Bar", style="cyan", no_wrap=True)
    columns = ["Trade $", "Last $", "Origin %"]
    if width >= 65:
        columns += ["Skill %", "Candle %", "Path %"]
    if width >= 100:
        columns += ["Bid $", "Ask $", "Pairs"]
    if width >= 145:
        columns += ["Open $", "High $", "Low $", "Vol MAE", "Trade #", "Bid size", "Ask size"]
    if width >= 215:
        columns += ["Bid dir %", "Ask dir %", ">0.5% dir", ">0.5% MAE", ">0.5% last $"]
    for label in columns:
        table.add_column(label, justify="right", no_wrap=True)
    for frame, n in HORIZONS if detailed else SUMMARY:
        prefix = f"{frame}/h{n:02d}"
        def metric(key):
            return report.get(f"{prefix}/{key}")

        def close(stream):
            return scored(metric(f"{stream}/comparable_close_mae"),
                          metric(f"{stream}/persistence_close_mae"), ",.4f")

        def direction(stream, mode):
            key = f"{stream}/{mode}"
            return scored(metric(f"{key}/balanced_accuracy_pct"),
                          flat_reference(report, f"{prefix}/{key}"), ".1f", higher=True)

        def raw(field):
            key = f"{prefix}/{field}/mae"
            # Compare adjacent reports only on the same scope and field coverage.
            count_key = f"{prefix}/{field}/count"
            reference = prior.get(key) if report.get(count_key) == prior.get(count_key) else None
            return scored(report.get(key), reference, ",.4g")

        model = metric("trade/comparable_close_mae")
        last = metric("trade/persistence_close_mae")
        skill = 100 * (1 - model / last) if model is not None and last else None
        counts = [metric(f"trade/from_last/{label}_count") or 0 for label in ("down", "flat", "up")]
        cells = [close("trade"), num(last, ",.4f"), direction("trade", "from_last")]
        if width >= 65:
            cells += [scored(skill, 0, "+,.1f", higher=True),
                      direction("trade", "candle"), direction("trade", "path")]
        if width >= 100:
            cells += [close("bid"), close("ask"), num(sum(counts) or None)]
        if width >= 145:
            cells += [raw(key) for key in ("trade_open","trade_high","trade_low","trade_size",
                                           "trade_count","bid_size","ask_size")]
        if width >= 215:
            parts = [(metric(f"trade/abs_move_pct/{band}/count") or 0, band)
                     for band in ("05_1","1_2","gt_2")]
            total = sum(count for count, _ in parts)
            def weighted(key):
                return sum(count * (metric(f"trade/abs_move_pct/{band}/{key}") or 0)
                           for count, band in parts) / total if total else None
            # For non-flat moves, compare against a 50% random up/down guess.
            cells += [direction("bid","from_last"), direction("ask","from_last"),
                      scored(weighted("direction_accuracy_pct"),50,".1f",higher=True),
                      scored(weighted("close_mae"),weighted("persistence_close_mae"),",.4f"),
                      num(weighted("persistence_close_mae"),",.4f")]
        table.add_row(f"{frame} / {n:02d}", *cells)
    return table


def training_table(rows, width):
    if not rows:
        return Text("Waiting for logged optimizer updates.", style="dim")
    table = Table(expand=True, box=None, padding=(0, 1))
    for col in ("Update", "Loss", "Origins/s", "Rate", "Origins"):
        table.add_column(col, justify="right", no_wrap=True)
    previous = rows[5:30]
    reference_loss = median(row["train/loss"] for row in previous) if previous else None
    reference_rate = median(row["train/origins_per_second"] for row in previous) if previous else None
    for row in rows[:5 if width >= 95 else 2]:
        table.add_row(num(row.get("step")), scored(row.get("train/loss"), reference_loss, ",.3f"),
                      scored(row.get("train/origins_per_second"), reference_rate, ",.0f", higher=True),
                      num(row.get("train/learning_rate"), ".2e"), num(row.get("train/origins")))
    return table


def eval_panel(report, prior, title, width, detailed, waiting):
    if not report:
        return Panel(Text(waiting, style="dim"), title=title, title_align="left")
    header = Text(f"Step {num(report.get('step'))}  ·  {num(report.get('origins'))} origins  ·  "
                  f"loss {num(report.get('loss'),',.3f')}  ·  "
                  f"{num(report.get('origins_per_second'),',.0f',' origins/s')}  ·  "
                  f"nonfinite {num(report.get('nonfinite'))}", overflow="ellipsis", no_wrap=True)
    return Panel(Group(header, forecast_table(report,prior,width,detailed)), title=title,
                 title_align="left", border_style="blue")


def render(snapshot, *, width, height, now=None):
    state, checkpoint, development, final, manifest, training, prior = snapshot
    if not state:
        return Panel("Waiting for progress.json from the trainer.", title="BarGPT · Phase 2 V5")
    now = time.time() if now is None else now
    age = max(0, now - state.get("updated", 0))
    status = state.get("state", "unknown")
    stale = age > 30 and status not in TERMINAL
    color = "red" if status == "failed" or stale else "green" if status == "complete" else "cyan"
    activity = state.get("stage", "initializing")
    state_label = ("STALE" if stale else status.upper()) + f" / {activity}"
    step, saved = state.get("step"), checkpoint.get("step")
    fraction = 1 if status == "complete" else state.get("pass_fraction")
    cadence = manifest.get("arguments", {}).get("validation_every")
    next_step = ((step // cadence) + 1) * cadence if step is not None and cadence else None
    speed = state.get("windows_per_second")
    gpu_memory = state.get("gpu_memory_mib")
    report = final or development
    global_rows = [
        Text(f"{state_label}  ·  step {num(step)}  ·  saved {num(saved)}  ·  status {age:.0f}s old",
             style=f"bold {color}", overflow="ellipsis", no_wrap=True),
        Text(f"Train {num(speed,',.0f',' origins/s')}  ·  Eval {num(report.get('origins_per_second'),',.0f',' origins/s')}"
             f"  ·  Train loss {num(state.get('loss'),',.3f')}  ·  LR {num(state.get('learning_rate'),'.2e')}",
             overflow="ellipsis", no_wrap=True),
        Text(f"GPU {num(state.get('gpu_util'),'.0f','%')} / {num(gpu_memory/1024 if gpu_memory is not None else None,'.1f',' GiB')}"
             f"  ·  {num(state.get('done'))} origins  ·  next dev step {num(next_step if not final else None)}",
             overflow="ellipsis", no_wrap=True),
    ]
    if state.get("error"):
        global_rows.insert(1, Text(str(state["error"]), style="red", overflow="ellipsis", no_wrap=True))
    if fraction is not None:
        global_rows.append(Text("Session traversal", style="dim"))
        global_rows.append(SolidBar(1,fraction))
    if "validation" in activity and height >= 32:
        global_rows.append(Text(f"Last eval progress reading  ·  {num(state.get('validation_done'))} origins"
                                f"  ·  {num(state.get('validation_samples_per_second'),',.0f',' origins/s')}",
                                style="cyan", overflow="ellipsis", no_wrap=True))
    top = Panel(Group(*global_rows), title="RUN STATUS  ·  Phase 2 V5", title_align="left", border_style=color)
    if height < 31 or (final and height < 43):
        return Group(top, Panel(training_table(training[:1],width), title="TRAINING", title_align="left"),
                     Text(f"Dev step {num(development.get('step'))}  ·  loss {num(development.get('loss'),',.3f')}"
                          f"  ·  {('full validation ready' if final else 'full validation pending')}",
                          overflow="ellipsis", no_wrap=True))
    detailed = height >= (65 if final else 43)
    views = [
        top,
        Panel(training_table(training,width), title="TRAINING  ·  recent logged updates", title_align="left", border_style="blue"),
        eval_panel(development,prior,"DEVELOPMENT",width,detailed,"Waiting for the first completed development evaluation."),
        eval_panel(final,{},"FULL VALIDATION",width,detailed,"Pending until the full training pass finishes."),
    ]
    if height >= 34:
        views.extend([
            Text("Green improves, orange trails. Close MAE and skill vs last-close persistence; direction vs always-flat."
                 " OHLC and activity MAE vs previous matching dev report.", style="dim", overflow="ellipsis", no_wrap=True),
            Text("Origin = move from last known close; candle = close vs open; path = consecutive forecast closes."
                 " Large-move direction is raw accuracy vs a 50% up/down guess.", style="dim",
                 overflow="ellipsis", no_wrap=True),
            Text("Training colors compare with the median of the preceding 25 logged updates. Reference values and counts are neutral.",
                 style="dim", overflow="ellipsis", no_wrap=True),
        ])
    return Group(*views)


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
    console = Console(legacy_windows=False if vt or args.once else None, force_terminal=False if args.once else None)
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
