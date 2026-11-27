"""Workstation terminal for the lossless Phase 1 migration."""
import argparse
import json
import os
import sys
import time
from pathlib import Path

from rich.console import Console, Group
from rich.live import Live
from rich.padding import Padding
from rich.panel import Panel
from rich.progress_bar import ProgressBar
from rich.table import Table
from rich.text import Text

from ..phase1_v29.panel import enable_virtual_terminal, maximize_console
from ..v5.shards.contract import write_json


def value(number, scale=1, suffix=""):
    return "—" if number is None else f"{number/scale:,.1f}{suffix}"


def render(status):
    done, total = status.get("files_done", 0), status.get("files_total")
    src, dst = status.get("input_bytes"), status.get("compact_bytes")
    speed = status.get("bytes_per_second")
    phase = status.get("state", "waiting")
    accent = {"failed": "red", "complete": "green"}.get(phase, "cyan")
    if speed and src is not None and status.get("source_bytes") and phase == "running":
        remaining = max(0, status["source_bytes"]-src)/speed
        eta = f"{int(remaining//3600):02d}:{int(remaining%3600//60):02d}:{int(remaining%60):02d}"
    else:
        eta = "—"
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold cyan")
    grid.add_column(justify="right")
    for label, data in (
        ("Verified source files", f"{done:,} / {total:,}" if total else f"{done:,} indexed"),
        ("Windows read back", f"{status['windows']:,}" if "windows" in status else "—"),
        ("Stored bar records", f"{status['unique_bars']:,}" if "unique_bars" in status else "—"),
        ("Source read", value(src, 1e9, " GB")),
        ("Compact output", value(dst, 1e9, " GB")),
        ("Payload reduction", value(100*(1-dst/src), suffix="%") if src and dst is not None else "—"),
        ("Read throughput", value(speed, 1e6, " MB/s")),
        ("ETA", eta),
        ("Workers", str(status.get("workers", "—"))),
    ):
        grid.add_row(label, data)
    status_line = Text(f"{phase.upper()} · {status.get('stage', 'Awaiting migration state')}", style=f"bold {accent}")
    bar = Padding(ProgressBar(total=total, completed=done, pulse=total is None), (0, 0, 1, 0))
    parts = [status_line, bar, grid]
    if status.get("error"):
        parts.append(Text(status["error"], style="red"))
    return Panel(Group(*parts), title=Text("Phase 1 · Lossless shard migration", "bold white"),
                 border_style="blue", title_align="left")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--retirement", type=Path, help="Show Phase 2 retirement until migration starts")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    status = {}
    def read():
        nonlocal status
        try:
            status = json.loads((args.run/"progress.json").read_text())
        except (FileNotFoundError, PermissionError, json.JSONDecodeError):
            pass
        if not status and args.retirement:
            try:
                status_file = args.retirement/"retirement-aggregate.json"
                if not status_file.exists():
                    status_file = args.retirement/"retirement-progress.json"
                p = json.loads(status_file.read_text())
            except (FileNotFoundError, PermissionError, json.JSONDecodeError):
                p = {}
            finished = p.get("completed_roots", ())
            done = p.get("sessions", 0) + sum(item["sessions"] for item in finished)
            freed = p.get("bytes_removed", 0) + sum(item["payload_bytes_removed"] for item in finished)
            title = Text("Phase 1 migration · awaiting approved Phase 2 retirement", "bold white")
            return Panel(Group(Padding(ProgressBar(total=1577160, completed=done), (0, 0, 1, 0)),
                               Text(f"Legacy Phase 2 sessions {done:,} / 1,577,160  ·  "
                                    f"Retired payload {freed/1e12:.2f} TB")),
                         title=title, border_style="blue", title_align="left")
        return render(status)

    if args.once or not sys.stdout.isatty():
        Console().print(read())
        return
    maximize_console()
    alternate = enable_virtual_terminal()
    console = Console(legacy_windows=not alternate)
    try:
        with Live(read(), console=console, screen=alternate, auto_refresh=False) as live:
            frames = 0
            while True:
                live.update(read(), refresh=True)
                frames += 1
                if frames % 5 == 1:
                    write_json(args.run/"panel-status.json", dict(pid=os.getpid(),
                        updated=time.time(), frames=frames, alternate_screen=alternate,
                        columns=console.width, rows=console.height))
                time.sleep(1)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
