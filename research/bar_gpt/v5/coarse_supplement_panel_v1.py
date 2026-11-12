"""Read-only coarse supplement dashboard; never controls a writer or trainer."""

import argparse
import json
import os
from pathlib import Path
import sys
import time

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.progress_bar import ProgressBar
from rich.table import Table
from rich.text import Text


def render(data, contract, now=None):
    now = time.time() if now is None else now
    age = max(0, now-data.get("updated", now))
    state = data.get("state", "waiting for writer")
    stale = age > 90 and state == "running"
    color = "red" if state == "failed" else "yellow" if stale or state == "stopped" else "cyan"
    total, done = data.get("total", 0), data.get("completed", 0)
    active = data.get("active", {})
    headline = Text(f"Phase 1 hourly/daily supplement  |  {'STALE' if stale else state.upper()}", style=color)
    summary = Text(f"Durable tickers {done:,}/{total:,}  |  Active {len(active)}  |  Queued {max(0,total-done-len(active)):,}  |  Failed {data.get('failed',0)}  |  Snapshot {age:.0f}s old")
    samples = Table(expand=True, box=None)
    samples.add_column("Split / timeframe")
    samples.add_column("New windows", justify="right")
    samples.add_column("Target", justify="right")
    samples.add_column("Progress", ratio=1)
    saved = data.get("saved", {})
    for split, targets in contract.get("targets", {}).items():
        for frame, target in targets.items():
            count = saved.get(split, {}).get(str(frame), 0)
            label = "hourly" if int(frame) == 3_600_000_000 else "daily"
            samples.add_row(f"{split} / {label}", f"{count:,}", f"{target:,}", ProgressBar(total=max(1,target), completed=count))
    workers = Table(expand=True, box=None)
    workers.add_column("Ticker")
    workers.add_column("Stage")
    workers.add_column("Source pages", justify="right")
    workers.add_column("Page progress", ratio=1)
    for ticker, row in sorted(active.items()):
        page, pages = row.get("page", 0), row.get("pages", 0)
        workers.add_row(ticker, row.get("stage", "unknown"), f"{page}/{pages}" if pages else "—",
                        ProgressBar(total=max(1,pages), completed=page))
    footer = Text(f"Queries {data.get('queries',0):,}  |  Retries {data.get('retries',0)}  |  Cache pages are unadjusted; published windows are split-adjusted.\nTraining unchanged · no automatic activation · Ctrl+C closes this viewer only", style="dim")
    if data.get("error"):
        footer.append("\n"+data["error"], style="red")
    if data.get("shortfall"):
        footer.append("\nFrozen source exhausted before balance targets; see result.json for exact shortfalls.", style="yellow")
    return Panel(Group(headline, summary, ProgressBar(total=max(1,total), completed=done), Text(""), samples, Text(""), workers, Text(""), footer), border_style=color)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    console = Console(no_color=bool(os.environ.get("NO_COLOR")))
    contract = json.loads((args.output / "contract.json").read_text())["contract"]
    prior = dict(state="waiting for writer", updated=time.time())
    def snapshot():
        nonlocal prior
        try:
            prior = json.loads((args.output / "progress.json").read_text())
        except (OSError, ValueError):
            pass  # Retain last snapshot and let its age expose unavailable updates.
        return render(prior, contract)
    if args.once or not console.is_terminal:
        console.print(snapshot())
        return
    try:
        with Live(snapshot(), console=console, refresh_per_second=1, screen=True) as live:
            while True:
                time.sleep(1)
                live.update(snapshot())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
