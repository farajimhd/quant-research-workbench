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
from rich.segment import Segment
from rich.table import Table
from rich.text import Text

from ..phase1_v29.panel import SolidBar, enable_virtual_terminal, maximize_console
from .scroll_input import console_scroll_input
from .move_diagnostics import CENTER, MOVE_LABELS as FINE_MOVE_LABELS, SLOT_NAMES

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
        state["panel_title"] = manifest.get("panel_title", "Phase 2 V5")
        if manifest.get("arguments", {}).get("fine_move_diagnostics"):
            state["fine_move_diagnostics"] = True
            state["training_sample"] = self.read("training-sample-latest.json")
            state["training_target_distribution"] = self.read("training-target-distribution.json")
        data = manifest.get("model", {}).get("data", {})
        if data.get("certificate"):
            cert = self.read(data["certificate"])
            if cert.get("hash") and cert["hash"] == data.get("hash"):
                if "split_counts" in cert:
                    state["full_sessions"] = sum(row["sessions"] for row in cert["split_counts"]
                                                 if row["split"] == 1)
                else:
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


def skill_pct(error, baseline):
    return 100 * (1 - error / baseline) if error is not None and baseline else None


def forecast_table(report, prior, width, detailed):
    table = Table(expand=True, box=None, padding=(0, 1))
    table.add_column("Bar", style="cyan", no_wrap=True, width=10)
    columns = ["Trade MAE", "Baseline MAE", "Origin dir %"]
    if width >= 110:
        columns += ["Skill %", "Candle dir %", "Path dir %"]
    if width >= 145:
        columns += ["Bid $", "Ask $", "Pairs"]
    if width >= 225:
        columns += ["Vol MAE", "Trade #", "Bid size", "Ask size", "Small dir %", "Small skill %", "Flat MAE $"]
    if width >= 340:
        columns += ["Bid dir %", "Ask dir %", "Up hit %", "Down hit %", ">0.5% dir", ">0.5% MAE",
                    ">0.5% Base", "Large skill %"]
    for label in columns:
        table.add_column(label, justify="right", no_wrap=True, min_width=len(label), ratio=1)
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
        skill = skill_pct(model, last)
        def signed(labels, field, *, balanced=False):
            values = [(metric(f"trade/signed_move/{label}/{field}"),
                       metric(f"trade/signed_move/{label}/count")) for label in labels]
            valid = [(v, 1 if balanced else count) for v, count in values if count and v is not None]
            return sum(v * n for v, n in valid) / sum(n for _, n in valid) if valid else None
        counts = [metric(f"trade/from_last/{label}_count") or 0 for label in ("down", "flat", "up")]
        cells = [close("trade"), num(last, ",.4f"), direction("trade", "from_last")]
        if width >= 110:
            cells += [scored(skill, 0, "+,.1f", higher=True),
                      direction("trade", "candle"), direction("trade", "path")]
        if width >= 145:
            cells += [close("bid"), close("ask"), num(sum(counts) if metric("trade/from_last/balanced_accuracy_pct") is not None else None)]
        if width >= 225:
            cells += [raw(key) for key in ("trade_size", "trade_count", "bid_size", "ask_size")]
            small = ("up_small", "down_small")
            cells += [num(signed(small, "direction_accuracy_pct", balanced=True), ".1f"),
                      scored(skill_pct(signed(small, "close_mae"), signed(small, "persistence_close_mae")),
                             0, "+.1f", higher=True),
                      num(signed(("flat",), "close_mae"), ",.4f")]
        if width >= 340:
            parts = [(metric(f"trade/abs_move_pct/{band}/count") or 0, band)
                     for band in ("05_1","1_2","gt_2")]
            total = sum(count for count, _ in parts)
            def weighted(key):
                return sum(count * (metric(f"trade/abs_move_pct/{band}/{key}") or 0)
                           for count, band in parts) / total if total else None
            # For non-flat moves, compare against a 50% random up/down guess.
            cells += [direction("bid","from_last"), direction("ask","from_last"),
                      num(signed(("up_small", "up_large"), "direction_accuracy_pct"), ".1f"),
                      num(signed(("down_small", "down_large"), "direction_accuracy_pct"), ".1f"),
                      scored(weighted("direction_accuracy_pct"),50,".1f",higher=True),
                      scored(weighted("close_mae"),weighted("persistence_close_mae"),",.4f"),
                      num(weighted("persistence_close_mae"),",.4f"),
                      scored(skill_pct(weighted("close_mae"), weighted("persistence_close_mae")),
                             0, "+.1f", higher=True)]
        table.add_row(f"{frame} / {n:02d}", *cells)
    return table


def training_table(rows, width, *, count=None):
    if count is None:
        count = 5 if width >= 95 else 2
    table = Table(expand=True, box=None, padding=(0, 1))
    for col in ("Update", "Loss", "Origins/s", "Learning rate", "Origins"):
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


def titled_panel(content, title, *, border_style="blue"):
    return Panel(content, title=Text(title, style="bold bright_white"),
                 title_align="left", border_style=border_style)


def eval_panel(report, prior, title, width, detailed, waiting):
    session_count = f"{num(report['sessions'])} sessions  ·  " if "sessions" in report else ""
    header = Text(f"Step {num(report.get('step'))}  ·  {session_count}{num(report.get('origins'))} origins  ·  "
                  f"loss {num(report.get('loss'),',.3f')}  ·  "
                  f"{num(report.get('origins_per_second'),',.0f',' origins/s')}  ·  "
                  f"nonfinite {num(report.get('nonfinite'))}", overflow="ellipsis", no_wrap=True)
    if not report:
        header = Text(waiting, style="dim", overflow="ellipsis", no_wrap=True)
    return titled_panel(Group(header, forecast_table(report, prior, width, detailed)), title)


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
    split_counts = manifest.get("model", {}).get("data", {}).get("split_counts")
    if split_counts:
        eval_count = sum(row["eligible"] for row in split_counts if row["split"] == 1)
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


MOVE_LABELS = (
    ("down_large", "Down >0.5%"), ("down_small", "Down ≤0.5%"), ("flat", "Flat"),
    ("up_small", "Up ≤0.5%"), ("up_large", "Up >0.5%"),
)
PRICE_LABELS = (
    ("lt1", "<$1"), ("1_5", "$1–<5"), ("5_20", "$5–<20"),
    ("20_100", "$20–<100"), ("ge100", "$100+"),
)


def breakdown_panel(report, title, horizon):
    table = Table(box=None, padding=(0, 1))
    table.add_column("Group", no_wrap=True)
    for column in ("Pairs", "Dir %", "MAE $", "Base $", "Skill %"):
        table.add_column(column, justify="right", no_wrap=True)
    for family, labels in (("signed_move", MOVE_LABELS), ("price_band", PRICE_LABELS)):
        for key, label in labels:
            prefix = f"{horizon}/trade/{family}/{key}"
            count, error, baseline = (report.get(f"{prefix}/{k}") for k in
                                      ("count", "close_mae", "persistence_close_mae"))
            accuracy = "balanced_accuracy_pct" if family == "price_band" else "direction_accuracy_pct"
            table.add_row(label, num(count), num(report.get(f"{prefix}/{accuracy}"), ".1f"),
                          scored(error, baseline, ",.3f"), num(baseline, ",.3f"),
                          scored(skill_pct(error, baseline), 0, "+.1f", higher=True))
    return titled_panel(table, title)


DETAIL_HORIZONS = ("1s/h01", "1s/h10", "1m/h01", "daily/h01")


def fine_move_panel(report, title, horizon):
    table = Table(expand=True, box=None, padding=(0, 1))
    table.add_column("Actual signed move", no_wrap=True)
    for col in ("Pairs", "Share %", "Dir %", "MAE $", "Base $", "Skill %", "Bias $", "P90 ≥$"):
        table.add_column(col, justify="right")
    stem = f"{horizon}/trade/fine_move/-1"
    total = sum(report.get(f"{stem}/{i}/count", 0) for i in range(len(FINE_MOVE_LABELS)))
    for i, label in enumerate(FINE_MOVE_LABELS):
        def value(key):
            return report.get(f"{stem}/{i}/{key}")
        n, error, base = (value(k) for k in ("count", "close_mae", "persistence_close_mae"))
        table.add_row(label, num(n), num(100*n/total if total and n is not None else None, ".2f"),
                      num(value("direction_accuracy_pct"), ".1f"), scored(error, base, ".4g"),
                      num(base, ".4g"), scored(skill_pct(error, base), 0, "+.1f", higher=True),
                      num(value("signed_error"), "+.4g"), num(value("p90_lower"), ".4g"))
    exact = report.get(f"{stem}/{CENTER}/exact_flat_count")
    flat = report.get(f"{stem}/{CENTER}/predicted_near_flat_pct")
    false_flat = (sum(report.get(f"{stem}/{i}/count", 0)
                      * report.get(f"{stem}/{i}/predicted_near_flat_pct", 0)
                      for i in range(len(FINE_MOVE_LABELS)) if i != CENTER)
                  / (total-report[f"{stem}/{CENTER}/count"])
                  if total > report.get(f"{stem}/{CENTER}/count", 0) else None)
    note = Text(f"Exact flats {num(exact)} · Near-flat hit {num(flat, '.1f', '%')} · "
                f"False flat {num(false_flat, '.1f', '%')}", style="dim")
    return titled_panel(Group(table, note), title)


def move_by_price_panel(report, title, horizon):
    table = Table(expand=True, box=None)
    table.add_column("Actual signed move")
    for _, label in PRICE_LABELS:
        table.add_column(label, justify="right")
    for i, label in enumerate(FINE_MOVE_LABELS):
        cells = []
        for band in range(len(PRICE_LABELS)):
            key = f"{horizon}/trade/fine_move/{band}/{i}"
            n = report.get(f"{key}/count")
            hit = report.get(f"{key}/direction_accuracy_pct")
            skill = skill_pct(report.get(f"{key}/close_mae"), report.get(f"{key}/persistence_close_mae"))
            cells.append(f"{num(n)} / {num(hit, '.1f', '%')} / {num(skill, '+.1f', '%')}")
        table.add_row(label, *cells)
    explanation = Text("Trade close · Each cell shows pairs / direction hit / MAE skill.", style="dim")
    return titled_panel(Group(explanation, table), title)


def distribution_panel(report, title, horizon, *, census=False):
    table = Table(expand=True, box=None)
    table.add_column("Actual signed move")
    for _, label in PRICE_LABELS:
        table.add_column(label, justify="right")
    counts = report.get("counts")
    slot = SLOT_NAMES.index(horizon)
    for i, label in enumerate(FINE_MOVE_LABELS):
        if census:
            values = [counts[slot][band][i] if counts else None for band in range(len(PRICE_LABELS))]
        else:
            values = [report.get(f"{horizon}/trade/fine_move/{band}/{i}/count")
                      for band in range(len(PRICE_LABELS))]
        table.add_row(label, *(num(value) for value in values))
    if census:
        header = (f"Pass {report['data_pass']} · {num(report['sessions'])} sessions · {num(report['origins'])} origins · saved once"
                  if counts else "Pending the first complete census pass.")
    else:
        header = (f"Step {num(report.get('step'))} · eligible trade pairs by starting price"
                  if report else "Pending the first completed report.")
    return titled_panel(Group(Text(header, style="dim"), table), title)


def glossary():
    guide = Table.grid(padding=(0, 1), expand=True)
    guide.add_column(style="bold cyan")
    guide.add_column(ratio=1)
    sections = (
        ("Rows and coverage", (
            ("Bar", "Timeframe and forecast horizon. 1s / 03 is the third eligible future 1-second candle."),
            ("Pairs", "Rows at this horizon with a valid actual trade close, finite forecast, and valid positive last known trade close."),
            ("Development / End of pass", "For this run both reports cover the same held-out split. Their headers show the checkpoint step and evaluated coverage."),
        )),
        ("Dollar error and skill", (
            ("Trade MAE", "Mean absolute error of the model's trade-close forecast in dollars. Lower is better."),
            ("Baseline MAE", "Dollar MAE from carrying the last known trade close forward to every horizon. It uses the same pairs as Trade MAE."),
            ("Skill %", "100 × (1 − Model MAE / Baseline MAE). +20% means 20% lower error, zero is a tie, and −20% means 20% higher error. A zero baseline gives a dash."),
            ("Bid $ / Ask $", "Dollar close MAE for bid and ask. Each compares with its own last-known-close baseline."),
            ("Volume / Counts / Sizes", "Vol MAE is in shares, Trade # is in trades, and bid/ask size MAE uses the source quantity units."),
        )),
        ("Direction scores", (
            ("Origin dir %", "Compare the forecast close and actual close against the same last known close."),
            ("Candle dir %", "Compare the sign of forecast close minus forecast open with the sign of actual close minus actual open."),
            ("Path dir %", "Compare adjacent forecast closes with adjacent actual closes within the same timeframe. The first horizon has no path score."),
            ("Bid dir % / Ask dir %", "Origin direction for each quote stream using its own last known close."),
            ("Balanced accuracy", "Average accuracy separately across actual down, flat and up moves, giving each observed class equal weight."),
        )),
        ("Move and price breakdowns", (
            ("Small dir % / Small skill %", "Actual moves greater than zero and at most 0.5% in magnitude. Direction gives up and down equal weight. Skill compares dollar MAE on those same rows."),
            (">0.5% dir / Large skill %", "Actual moves above 0.5% in magnitude. Direction is the percentage called correctly, with each move weighted equally. Skill compares dollar MAE."),
            (">0.5% MAE / >0.5% Base", "Dollar MAE of the forecast and last-close baseline on the same large moves."),
            ("Up hit % / Down hit %", "Among actual up moves and actual down moves, respectively, the share called correctly."),
            ("Flat MAE $", "Model dollar error when the actual close equals the last known close. Baseline MAE is zero."),
            ("Signed move rows", "Group by realized direction and magnitude relative to last known close. Dir % is the share called correctly within each row."),
            ("Price band rows", "Group by last known trade close at forecast time. Dir % is balanced origin direction accuracy within the band."),
            ("Breakdown columns", "Pairs shows group coverage. MAE $ is model dollar error, Base $ is baseline dollar error, and Skill % uses those same pairs."),
            ("Fine signed moves", "The central ±0.01% band is near-flat for both actual and predicted moves. False flat is the share of non-flat targets predicted near-flat."),
            ("Bias $ / P90 ≥$", "Bias is mean forecast minus actual close. P90 is the lower dollar edge of the histogram bin containing the 90th percentile absolute error."),
            ("Training reports", "Target distribution is counted once over a complete pass. Training forecast metrics score fixed sampled sessions with the end-of-pass checkpoint."),
        )),
        ("Colors and training", (
            ("Green / Orange / Dash", "Green beats the reference, orange trails, and ties stay neutral. A dash indicates an unavailable value."),
            ("Close MAE / Skill", "Close errors use the last-known-close baseline. Skill compares with zero."),
            ("Direction references", "Colored balanced scores compare with always-flat on the same rows. Large-move direction compares with a 50% up/down guess."),
            ("Activity and speed references", "Activity MAE compares with the previous development report when field counts match. Training loss and speed compare with the median of up to 25 earlier log entries."),
            ("Training columns", "Update is the optimizer step. Loss is the weighted training objective. Origins/s is recent throughput. Learning rate is the current optimizer rate. Origins is the cumulative processed count."),
        )),
    )
    for heading, rows in sections:
        guide.add_row(Text(heading, "bold white"), "")
        for label, explanation in rows:
            guide.add_row(label, explanation)
    return titled_panel(guide, "METRIC GUIDE")


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


def render(snapshot, *, width, height, now=None, scrollable=False):
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
    top = titled_panel(Group(*global_rows), f"RUN STATUS  ·  {state.get('panel_title', 'Phase 2 V5')}",
                       border_style=color)
    # Each comparison is ordered by scope within a metric and horizon.
    sample = state.get("training_sample", {})
    scopes = (("TRAINING", sample), ("DEVELOPMENT", development), ("VALIDATION", final))
    recent = titled_panel(training_table(training, width, count=1 if height < 46 else None),
                          "TRAINING ACTIVITY · recent optimizer updates")
    if height < 46:
        return Group(top, recent, *(Text(f"{scope}  |  step {num(item.get('step'))}  |  "
                                         f"loss {num(item.get('loss'), ',.3f')}",
                                         overflow="ellipsis", no_wrap=True)
                                    for scope, item in scopes))
    notes = ("fixed end-of-pass sample", "held-out split", "end-of-pass held-out split")
    waits = ("Waiting for the end-of-pass training sample.",
             "Waiting for the first completed development evaluation.",
             "Waiting for the end-of-pass evaluation.")
    views = [top, recent]
    for (scope, item), note, wait in zip(scopes, notes, waits):
        views.append(eval_panel(item, prior if scope == "DEVELOPMENT" else {},
                                f"{scope} · forecast performance · {note}", width, height >= 87, wait))
    if scrollable:
        horizons = DETAIL_HORIZONS
        fine = state.get("fine_move_diagnostics")
        for horizon in horizons:
            frame, step_label = horizon.split("/h")
            panels = [breakdown_panel(item, f"{scope} · trade move and price groups · {frame} / {step_label}", horizon)
                      for scope, item in scopes]
            if width >= 240:
                row = Table.grid(padding=(0, 1))
                for _ in panels:
                    row.add_column(vertical="top")
                row.add_row(*panels)
                views.append(row)
            else:
                views.extend(panels)
        if fine:
            for make_panel, label in ((fine_move_panel, "fine signed moves"),
                                      (move_by_price_panel, "price bands × signed moves")):
                for horizon in horizons:
                    frame, step_label = horizon.split("/h")
                    for scope, item in scopes:
                        views.append(make_panel(item, f"{scope} · {label} · {frame} / {step_label}", horizon))
            for horizon in horizons:
                frame, step_label = horizon.split("/h")
                for scope, item in scopes:
                    census = scope == "TRAINING"
                    if census:
                        item = state.get("training_target_distribution", {})
                    views.append(distribution_panel(item, f"{scope} · target distribution · {frame} / {step_label}",
                                                    horizon, census=census))
    if scrollable or height >= 65:
        views.append(glossary())
    return Group(*views)


class ScrollViewport:
    """Keep a stable viewport into the live dashboard, including its guide."""

    def __init__(self):
        self.offset = 0
        self.at_bottom = False
        self.lines = []

    def frame(self, console, content, actions=()):
        page = max(1, console.height - 1)
        if content is not None:
            self.lines = console.render_lines(content, console.options.update(height=None), new_lines=True)
        lines = self.lines
        end = max(0, len(lines) - page)
        if self.at_bottom:
            self.offset = end
        for action in actions:
            self.at_bottom = action == "end"
            if action == "home":
                self.offset = 0
            elif action == "end":
                self.offset = end
            else:
                self.offset += action
            self.offset = min(end, max(0, self.offset))
        self.offset = min(end, self.offset)
        visible = lines[self.offset:self.offset + page]
        footer = Text("Wheel or PgUp/PgDn to scroll · Home top · End metric guide"
                      f"  |  Rows {self.offset + 1}–{self.offset + len(visible)} of {len(lines)}",
                      style="dim", no_wrap=True, overflow="crop")
        return ViewportFrame(visible, footer)


class ViewportFrame:
    def __init__(self, lines, footer):
        self.lines, self.footer = lines, footer

    def __rich_console__(self, console, options):
        for line in self.lines:
            yield from line
            if not line or line[-1].text != "\n":
                yield Segment.line()
        yield self.footer


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

    def frame(snapshot, scrollable=False):
        height = max(103, console.height) if scrollable else console.height
        return render(snapshot, width=console.width, height=height, scrollable=scrollable)

    if args.once or not console.is_terminal:
        console.print(frame(reader.snapshot(), scrollable=True))
        return
    try:
        with console_scroll_input(vt) as navigation, Live(console=console, screen=vt, auto_refresh=False) as live:
            viewport = ScrollViewport()
            frames = 0
            next_refresh = 0
            snapshot = reader.snapshot()
            last_size = None
            while True:
                actions = navigation.read(max(1, console.height - 3)) if navigation else []
                refresh = time.monotonic() >= next_refresh
                if refresh:
                    snapshot = reader.snapshot()
                    next_refresh = time.monotonic() + 1
                    frames += 1
                size = console.size
                resized = size != last_size
                if refresh or resized or actions:
                    # Scrolling reuses the rendered rows between one-second
                    # refreshes, so wheel input does not rebuild every table.
                    content = frame(snapshot, scrollable=bool(navigation)) if refresh or resized else None
                    live.update(viewport.frame(console, content, actions) if navigation else content, refresh=True)
                    last_size = size
                if refresh and (frames == 1 or frames % 10 == 0):
                    receipt = dict(pid=os.getpid(), updated=time.time(), frames=frames,
                                   virtual_terminal=vt, alternate_screen=vt, scrollable=bool(navigation),
                                   columns=console.width, rows=console.height, scroll_offset=viewport.offset)
                    (args.run / "forecast-panel-status.json").write_text(json.dumps(receipt), encoding="utf-8")
                time.sleep(.06 if navigation else 1)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
