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
        self.last_eval_counter = None
        self.eval_cycle = None
        self.confirmed_sessions = None

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
        state = dict(self.read("progress.json"))
        manifest = self.read("manifest.json")
        data = manifest.get("model", {}).get("data", {})
        if data.get("certificate"):
            cert = self.read(data["certificate"])
            if cert.get("hash") and cert["hash"] == data.get("hash"):
                state["full_sessions"] = sum(row["publications"] for row in cert["distribution"]
                                             if row["split"] == 1)

        # The trainer reuses the same counter for both evaluation scopes and
        # leaves the previous value in place until the next eight-session update.
        stage = state.get("stage")
        cycle = (stage, state.get("step"), state.get("epoch")) if stage in (
            "development validation", "full validation") else None
        counter = (state.get("validation_sessions"), state.get("validation_done"))
        if cycle != self.eval_cycle:
            self.confirmed_sessions = None
        if cycle and self.last_eval_counter is not None and counter != self.last_eval_counter:
            self.confirmed_sessions = counter[0]
        self.eval_cycle, self.last_eval_counter = cycle, counter
        state["active_eval_sessions"] = self.confirmed_sessions
        return (state, self.read("checkpoint_last.proof.json"), dev,
                self.read("validation-final.json"), manifest, recent, self.prior)


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
    table.add_column("Bar", style="cyan", no_wrap=True, width=10)
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
        table.add_column(label, justify="right", no_wrap=True, ratio=1)
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
            cells += [close("bid"), close("ask"), num(sum(counts) if metric("trade/from_last/balanced_accuracy_pct") is not None else None)]
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


def training_table(rows, width, *, count=None):
    if count is None:
        count = 5 if width >= 95 else 2
    table = Table(expand=True, box=None, padding=(0, 1))
    for col in ("Update", "Loss", "Origins/s", "Rate", "Origins"):
        table.add_column(col, justify="right", no_wrap=True, ratio=1)
    previous = rows[5:30]
    reference_loss = median(row["train/loss"] for row in previous) if previous else None
    reference_rate = median(row["train/origins_per_second"] for row in previous) if previous else None
    for index in range(count):
        row = rows[index] if index < len(rows) else {}
        table.add_row(num(row.get("step")), scored(row.get("train/loss"), reference_loss, ",.3f"),
                      scored(row.get("train/origins_per_second"), reference_rate, ",.0f", higher=True),
                      num(row.get("train/learning_rate"), ".2e"), num(row.get("train/origins")))
    return table


def eval_panel(report, prior, title, width, detailed, waiting):
    header = Text(f"Step {num(report.get('step'))}  ·  {num(report.get('origins'))} origins  ·  "
                  f"loss {num(report.get('loss'),',.3f')}  ·  "
                  f"{num(report.get('origins_per_second'),',.0f',' origins/s')}  ·  "
                  f"nonfinite {num(report.get('nonfinite'))}", overflow="ellipsis", no_wrap=True)
    if not report:
        header = Text(waiting, style="dim", overflow="ellipsis", no_wrap=True)
    return Panel(Group(header, forecast_table(report,prior,width,detailed)), title=title,
                 title_align="left", border_style="blue")


def duration(seconds):
    if seconds is None:
        return "—"
    if seconds < 60:
        return f"{max(0, round(seconds))}s"
    minutes = math.ceil(seconds / 60)
    days, minutes = divmod(minutes, 24 * 60)
    hours, minutes = divmod(minutes, 60)
    if days:
        return f"{days}d {hours:02d}h"
    if hours:
        return f"{hours}h {minutes:02d}m"
    return f"{minutes}m"


def estimates(state, training, manifest, report, active):
    """Use committed coverage and measured rates, with separate train/eval costs."""
    total = state.get("pass_origins_total")
    done = state.get("pass_origins_done")
    epoch = state.get("epoch")
    epochs = state.get("epochs")
    speed = state.get("windows_per_second")
    rates = [r["train/origins_per_second"] for r in training[:25]
             if r.get("train/origins_per_second", 0) > 0 and r.get("train/pass", epoch) == epoch]
    typical = median(rates) if len(rates) >= 5 else speed
    def at_rate(work, rate):
        return work / rate if active and work is not None and rate and rate > 0 else None

    left = max(total - done, 0) if total and done is not None else None
    all_left = left + total * max(0, epochs - epoch) if left is not None and epoch and epochs else None
    cadence = manifest.get("arguments", {}).get("validation_every")
    step = state.get("step")
    next_step = ((step // cadence) + 1) * cadence if step is not None and cadence else None
    upcoming = None
    if next_step is not None and len(training) >= 5:
        latest, oldest = training[0], training[min(24, len(training)-1)]
        updates = latest["step"] - oldest["step"]
        origins = latest.get("train/origins", 0) - oldest.get("train/origins", 0)
        if updates > 0 and origins > 0:
            upcoming = at_rate((next_step - step) * origins / updates, typical)
    block_count = manifest.get("model", {}).get("data", {}).get("blocks")
    eval_count = block_count * 4096 - total if block_count and total else None
    eval_rate = report.get("origins_per_second")
    eval_seconds = at_rate(eval_count, eval_rate) if eval_count is not None and eval_count > 0 else None
    return (typical, left, at_rate(left, speed), at_rate(left, typical),
            at_rate(all_left, typical), next_step, upcoming, eval_seconds)


def status_rows(state, report, manifest, training, step, saved, age, label, color, *, width, compact, stale):
    age_line = Text(f"{label}  ·  step {num(step)}  ·  saved {num(saved)}  ·  {age:.0f}s old",
                    style=f"bold {color}", overflow="ellipsis", no_wrap=True)
    rows = [age_line]
    if state.get("error"):
        error = " ".join(str(state["error"]).splitlines())
        rows[0] = Text(f"{label}  |  {error}", style="bold red", overflow="ellipsis", no_wrap=True)
    active = state.get("state") == "running" and not stale
    typical, left, live_eta, pass_eta, run_eta, next_step, dev_eta, eval_eta = estimates(
        state, training, manifest, report, active)
    speed = state.get("windows_per_second")
    memory = state.get("gpu_memory_mib")
    if compact or width < 75:
        rows.append(Text(f"Train {num(speed)} /s · Eval {num(report.get('origins_per_second'))} /s",
                         overflow="ellipsis", no_wrap=True))
        rows.append(Text(f"Pass ETA {duration(pass_eta)} · Run ETA {duration(run_eta)}",
                         overflow="ellipsis", no_wrap=True))
        return rows

    grid = Table.grid(padding=(0, 2), expand=True)
    for header in ("THROUGHPUT", "TRAINING", "TIME REMAINING"):
        grid.add_column(ratio=1, no_wrap=True)
    grid.add_row(*(Text(label, "bold blue") for label in ("THROUGHPUT", "TRAINING", "TIME REMAINING")))
    entries = (
        (("Train live", num(speed, suffix=" origins/s")),
         ("Train loss", num(state.get("loss"), ",.3f")),
         ("Current pass", duration(pass_eta))),
        (("Train median", num(typical, suffix=" origins/s")),
         ("Learning rate", num(state.get("learning_rate"), ".4e")),
         ("Pass at live rate", duration(live_eta))),
        (("Last evaluation", num(report.get("origins_per_second"), suffix=" origins/s")),
         ("GPU / memory", f"{num(state.get('gpu_util'),'.0f','%')} / {num(memory/1024 if memory is not None else None,'.1f',' GiB')}"),
         ("All passes", duration(run_eta))),
        (("Eval step", num(report.get("step"))),
         ("Pass origins left", num(left)),
         ("Next dev", f"{duration(dev_eta)} · step {num(next_step)}")),
        (("Full eval budget", duration(eval_eta)),
         ("Trained origins", num(state.get("done"))),
         ("Pass size", num(state.get("pass_origins_total")))),
    )
    for group in entries:
        grid.add_row(*(Text(title + "  ", "dim").append(value, "bold cyan") for title, value in group))
    rows.append(grid)
    rows.append(Text("ETAs use current or recent-median train rate. Training ETAs exclude evaluations and downtime. "
                     "Full eval uses held-out candidate count and the last measured eval rate.",
                     style="dim", overflow="ellipsis", no_wrap=True))
    return rows


GLOSSARY = (
    ("Bar / Pairs", "Timeframe and nth observed eligible future candle; pairs count trade closes with both a valid target and prior close."),
    ("Trade $ / Last $", "Mean absolute dollar error of the predicted trade close and of carrying forward the last known trade close, on identical pairs."),
    ("Skill %", "100 × (1 − Trade $ / Last $). Positive means lower error than persistence; zero is a tie."),
    ("Origin %", "Sign of forecast close minus last known close vs sign of actual close minus that same last known close."),
    ("Candle %", "Sign of predicted close minus predicted open vs the same comparison in the actual candle."),
    ("Path %", "Sign of forecast close minus the previous forecast close vs the actual adjacent change. First horizon has no path comparison."),
    ("Bid $ / Ask $", "Mean absolute dollar error for bid/ask closes. Each color compares with that stream's own last-close baseline."),
    ("Bid dir % / Ask dir %", "Origin direction accuracy for each quote stream using its own last known close."),
    ("Open $ / High $ / Low $", "Mean absolute forecast error for each trade field in dollars, over source-valid outcomes."),
    ("Vol MAE / Trade #", "Mean absolute error of traded volume in shares and of trade count in trades."),
    ("Bid size / Ask size", "Mean absolute error of predicted quote sizes in source quantity units."),
    (">0.5% dir", "Share of correct up/down forecasts when the actual close moved more than 0.5% from the last known close."),
    (">0.5% MAE / last $", "Forecast and persistence close MAE in dollars on that same large-move subset."),
    ("Training columns", "Update is the optimizer step; Loss is the masked training objective; Origins/s is throughput; Rate is learning rate; Origins is the cumulative trained count."),
)


def glossary(width, *, expanded):
    guide = Table.grid(padding=(0, 1), expand=True)
    guide.add_column(style="bold cyan", no_wrap=True)
    guide.add_column(ratio=1)
    if expanded:
        for name, meaning in GLOSSARY:
            guide.add_row(name, meaning)
    else:
        guide.add_row("Close errors", "Mean absolute dollar error. Last $ carries forward the last known close. Skill % is 100 × (1 − Trade $ / Last $).")
        guide.add_row("Direction", "Origin = close vs last known close; Candle = close vs open; Path = consecutive closes. Direction uses balanced recall.")
        guide.add_row("Coverage", "Bar = timeframe/horizon. Pairs = targets with an available prior close. Full-width view shows the complete glossary.")
    meaning = Text("Dollar errors and skill compare to persistence where available. Direction % uses balanced recall: average recall across present down/flat/up classes, "
                   "compared with always-flat. Large-move direction uses raw accuracy vs a 50% guess.")
    colors = Text()
    colors.append("Green", "bold green")
    colors.append(" beats the reference; ")
    colors.append("orange", "bold dark_orange")
    colors.append(" trails. OHLC and activity MAE use the previous dev report with matching coverage. "
                  "Training loss/speed use the preceding 25 logged updates' median. Counts and references stay neutral.")
    return Panel(Group(guide, meaning, colors), title="METRIC GUIDE", title_align="left", border_style="blue")


def progress_bars(state, final, width):
    """Keep each scope visible; only completed reports certify evaluation completion."""
    table = Table.grid(expand=True, padding=(0, 1))
    table.add_column(no_wrap=True, style="bold")
    table.add_column(ratio=1)
    table.add_column(no_wrap=True, width=32 if width >= 85 else 20)
    epoch, epochs = state.get("epoch"), state.get("epochs")
    total, done = state.get("pass_origins_total"), state.get("pass_origins_done")
    fraction = done / total if done is not None and total else None
    overall = ((epoch - 1 + fraction) / epochs
               if epoch and epochs and fraction is not None else None)
    if state.get("state") == "complete":
        overall = fraction = 1
    def row(name, value, detail):
        bar = SolidBar(1, value) if value is not None else Text("\u2591" * max(1, width - 4), style="bright_black", no_wrap=True, overflow="crop")
        table.add_row(name, bar, Text(detail, style="dim", no_wrap=True, overflow="ellipsis"))
    row("Overall", overall, f"{num(epochs)} training passes")
    row("Current pass", fraction, f"Pass {num(epoch)} of {num(epochs)}")
    limit = state.get("full_sessions")
    if final and final.get("data_pass") == epoch:
        row("Full validation", 1, f"Complete | pass {num(epoch)}")
    elif state.get("stage") == "full validation":
        n = state.get("active_eval_sessions")
        row("Full validation", n / limit if n is not None and limit else None,
            f"{num(n)} / {num(limit)} sessions" if n is not None else "Awaiting first count")
    else:
        row("Full validation", 0, f"Pending | {num(limit)} sessions")
    return table


def render(snapshot, *, width, height, now=None):
    state, checkpoint, development, final, manifest, training, prior = snapshot
    now = time.time() if now is None else now
    age = max(0, now - state.get("updated", now))
    status = state.get("state", "waiting")
    stale = age > 30 and status not in TERMINAL
    color = "red" if status == "failed" or stale else "green" if status == "complete" else "cyan"
    activity = state.get("stage", "initializing")
    state_label = ("STALE" if stale else status.upper()) + f" / {activity}"
    step, saved = state.get("step"), checkpoint.get("step")
    report = max((development, final), key=lambda item: item.get("step", -1))
    global_rows = status_rows(state, report, manifest, training, step, saved, age, state_label,
                             color, width=width, compact=height < 46, stale=stale)
    global_rows.append(progress_bars(state, final, width))
    top = Panel(Group(*global_rows), title="RUN STATUS  ·  Phase 2 V5", title_align="left", border_style=color)
    # Viewport size alone determines the layout. A report arriving can fill
    # cells, but must never move another section or change the horizon list.
    if height < 46:
        def summary(label, report):
            return Text(f"{label}  |  step {num(report.get('step'))}  |  loss {num(report.get('loss'), ',.3f')}",
                        overflow="ellipsis", no_wrap=True)
        return Group(top, Panel(training_table(training, width, count=1), title="TRAINING", title_align="left"),
                     summary("DEVELOPMENT", development), summary("FULL VALIDATION", final))
    detailed = height >= 87
    views = [
        top,
        Panel(training_table(training,width), title="TRAINING  ·  recent logged updates", title_align="left", border_style="blue"),
        eval_panel(development,prior,"DEVELOPMENT",width,detailed,"Waiting for the first completed development evaluation."),
        eval_panel(final,{},"FULL VALIDATION",width,detailed,"Pending until the full training pass finishes."),
    ]
    if height >= 65:
        views.append(glossary(width, expanded=height >= 94 and width >= 145))
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
