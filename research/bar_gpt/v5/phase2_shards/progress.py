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


def render(status, width=110, height=36):
    """Render one bounded snapshot, retaining critical health at narrow widths."""
    age = max(0, time.time() - status.get("updated", 0))
    state = status.get("state", "starting")
    color = "red" if "failed" in state else "yellow" if age > 20 else "cyan"
    header = Text(f"BarGPT phase 2 | {state} | updated {age:.0f}s ago", style=color)
    used, target = status.get("bytes", 0), status.get("target_bytes", 4e12)
    progress = Text(
        f"{used / 1e12:.4f} / {target / 1e12:.2f} TB saved | 4 TB inventory | {status.get('units', 0):,} sealed units"
    )
    bar = ProgressBar(total=target, completed=used, width=None)
    rate = status.get("bytes_per_second", 0)
    # Do not extrapolate startup or stale/stopped snapshots into a false ETA.
    estimating = status.get("elapsed_seconds", 0) < 120 or rate <= 0
    eta = (
        "ETA warming up"
        if estimating
        else f"ETA ~{max(0, target-used)/rate/3600:.0f}h at this rate"
    )
    if age > 20 or state != "running":
        eta = "ETA unavailable"
    throughput = Text(f"Session average {rate/1e6:.1f} MB/s | {eta}")
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
        f"RAM free {status.get('free_memory_gib', 0):.1f} GiB | disk free {status.get('free_disk_gib', 0):.0f} GiB"
    )
    queue = Text(
        f"Active {status.get('active', 0)} | saving {status.get('saving', 0)} | queued streams {status.get('queued_streams', 0):,} | failed {status.get('failed', 0)}"
    )
    workers = (
        f"{status['process_workers']} processes | {status.get('workers', 0)} lanes"
        if status.get("process_workers")
        else f"{status.get('workers', 0)} query workers"
    )
    waits = Text(
        ", ".join(sorted(set(status.get("resource_waits", []))))
        or f"{workers} | {status.get('io_workers', 0)} I/O slots | CPU only"
    )
    guard = Text(
        f"Admission cap {status.get('admission_lanes', status.get('workers', 0))} | {status.get('training_guard', 'trainer guard not configured')}",
        style="dim",
    )
    lanes = Table(expand=True, box=None)
    lanes.add_column("Ticker", width=12)
    lanes.add_column("Year", width=5)
    lanes.add_column("Current work", overflow="ellipsis", no_wrap=True)
    lane_count = min(8, max(0, height - 23))
    for lane in status.get("lanes", [])[:lane_count]:
        lanes.add_row(lane["ticker"], str(lane["year"]), lane["stage"])
    messages = [
        Text(error["reason"][: max(30, width - 2)], style="red")
        for error in status.get("errors", [])[-2:]
    ]
    return Group(
        header,
        progress,
        bar,
        throughput,
        queue,
        resources,
        waits,
        guard,
        coverage,
        *([lanes] if lane_count else []),
        *messages,
        Text(
            "STOP drains generation. Ctrl+C closes this panel only.",
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
        console.print(render(previous, console.width, console.height))
        return
    try:
        with Live(
            render(previous, console.width, console.height),
            console=console,
            auto_refresh=False,
            transient=False,
        ) as live:
            while True:
                try:
                    previous = json.loads(path.read_text())
                except (FileNotFoundError, PermissionError, json.JSONDecodeError):
                    pass  # Preserve the last trustworthy snapshot and its age.
                live.update(
                    render(previous, console.width, console.height), refresh=True
                )
                time.sleep(1)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
