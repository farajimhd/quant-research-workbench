"""Read-only, stable workstation panel; never owns or restarts the producer."""

import argparse
import json
from pathlib import Path
import time

from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.progress_bar import ProgressBar
from rich.console import Group
from rich.text import Text


def render(status, width=110):
    """Render one bounded snapshot, retaining critical health at narrow widths."""
    age = max(0, time.time() - status.get("updated", 0))
    state = status.get("state", "starting")
    color = "red" if "failed" in state else "yellow" if age > 20 else "cyan"
    header = Text(f"BarGPT phase 2 | {state} | updated {age:.0f}s ago", style=color)
    used, target = status.get("bytes", 0), status.get("target_bytes", 2e12)
    progress = Text(
        f"{used / 1e12:.4f} / {target / 1e12:.2f} TB saved | 4 TB inventory | {status.get('units', 0):,} sealed units"
    )
    bar = ProgressBar(total=target, completed=used, width=None)
    coverage = Table(expand=True, box=None)
    coverage.add_column("Split / group")
    coverage.add_column("Selected blocks", justify="right")
    coverage.add_column("Deferred / purged", justify="right")
    for item in sorted(
        status.get("blocks", []), key=lambda x: (x["split"], -x["liquid"])
    ):
        name = ("train", "validation", "cross-cutoff purge")[item["split"]]
        coverage.add_row(
            f"{name} / {'liquid' if item['liquid'] else 'less liquid'}",
            f"{item['selected']:,}",
            f"{item['total'] - item['selected']:,}",
        )
    resources = Text(
        f"RAM free {status.get('free_memory_gib', 0):.1f} GiB | query reserved {status.get('reserved_query_gib', 0):.1f} GiB | disk free {status.get('free_disk_gib', 0):.1f} GiB"
    )
    queue = Text(
        f"Active {status.get('active', 0)} | saving {status.get('saving', 0)} | queued streams {status.get('queued_streams', 0):,} | failed {status.get('failed', 0)}"
    )
    waits = Text(
        ", ".join(sorted(set(status.get("resource_waits", []))))
        or f"{status.get('workers', 0)} query / {status.get('io_workers', 0)} I/O workers | no GPU work"
    )
    lanes = Table(expand=True, box=None)
    lanes.add_column("Ticker", width=12)
    lanes.add_column("Year", width=5)
    lanes.add_column("Current work", overflow="ellipsis", no_wrap=True)
    for lane in status.get("lanes", [])[:8]:
        lanes.add_row(lane["ticker"], str(lane["year"]), lane["stage"])
    messages = [
        Text(error["reason"][: max(30, width - 2)], style="red")
        for error in status.get("errors", [])[-2:]
    ]
    return Group(
        header,
        progress,
        bar,
        queue,
        resources,
        waits,
        coverage,
        lanes,
        *messages,
        Text(
            "STOP file: graceful stop after admitted work. Panel Ctrl+C closes only this viewer.",
            style="dim",
        ),
    )


def main():
    """Refresh at one Hz without clearing the terminal or flooding redirected logs."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", default="D:/TradingML/runtimes/bar_gpt/phase2-streams-v1"
    )
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    console, path = Console(), Path(args.output) / "progress.json"
    previous = dict(state="waiting for producer status", updated=time.time())
    if args.once or not console.is_terminal:
        if path.exists():
            previous = json.loads(path.read_text())
        console.print(render(previous, console.width))
        return
    try:
        with Live(
            render(previous), console=console, auto_refresh=False, transient=False
        ) as live:
            while True:
                try:
                    previous = json.loads(path.read_text())
                except (FileNotFoundError, PermissionError, json.JSONDecodeError):
                    pass  # Preserve the last trustworthy snapshot and its age.
                live.update(render(previous, console.width), refresh=True)
                time.sleep(1)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
