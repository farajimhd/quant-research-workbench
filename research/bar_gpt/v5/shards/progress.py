"""Read-only Rich observer; closing it never interrupts extraction workers."""

import argparse
import json
from pathlib import Path
import time

from rich.console import Console, Group
from rich.live import Live
from rich.progress_bar import ProgressBar
from rich.table import Table
from rich.text import Text


def render(snapshot, width=100):
    """Stable worker rows; bars advance on durable sessions, not query guesses."""
    age = max(0, time.time() - snapshot.get("updated", 0))
    state = snapshot.get("state", "starting")
    heading = Text(f"BarGPT shards · {state.upper()}", style="bold")
    if age > 10:
        heading.append(f" · status stale ({age:.0f}s)", style="yellow")
    used, target = snapshot.get("bytes", 0), snapshot.get("target_bytes", 10**12)
    summary = Text(f"{used/10**9:,.2f} / {target/10**9:,.0f} GB  |  "
                   f"{snapshot.get('samples',0):,} samples  |  "
                   f"jobs {snapshot.get('jobs_completed',0):,}/{snapshot.get('jobs_total',0):,}  |  "
                   f"failed {len(snapshot.get('failed',[]))}  retries {snapshot.get('retries',0)}")
    table = Table(expand=True, box=None, padding=(0, 1))
    for name in ("Worker", "Ticker / date", "Stage", "Sessions resolved", "Progress"):
        table.add_column(name, no_wrap=True)
    for worker in snapshot.get("workers", []):
        done, total = worker.get("done", 0), worker.get("total", 0)
        focus = f"{worker.get('ticker','')} {worker.get('day','')}"
        table.add_row(str(worker["id"]), focus, worker.get("phase", "queued"),
                      f"{done}/{total}" if total else "—",
                      ProgressBar(total=max(total, 1), completed=min(done, total),
                                  width=12 if width < 90 else 22))
    detail = Text("Per-worker progress: resolved sessions (saved, reused, or explicitly unavailable).")
    errors = [Text(str(e.get("error", e))[:max(70, width*2)], style="red")
              for e in snapshot.get("failed", [])[-2:]]
    return Group(heading, summary, ProgressBar(total=target, completed=min(used, target)),
                 Text(""), table, Text(""), detail, *errors)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("D:/TradingML/runtimes/bar_gpt/shards-2019-2025-v2"))
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    console = Console()
    previous = dict(state="waiting for controller", updated=time.time())

    def read():
        nonlocal previous
        try:
            previous = json.loads((args.output / "progress.json").read_text())
        except (OSError, ValueError):
            pass  # Retain the last trustworthy snapshot and its freshness clock.
        return previous

    if args.once or not console.is_terminal:
        console.print(render(read(), console.width))
        return
    try:
        with Live(render(read(), console.width), console=console, refresh_per_second=1) as live:
            while True:
                snapshot = read()
                live.update(render(snapshot, console.width))
                if snapshot.get("state") in {"failed", "scope_complete", "budget_reached", "inventory_exhausted", "stopped"}:
                    break
                time.sleep(1)
    except KeyboardInterrupt:
        console.print("Observer closed. Extraction is unchanged.")


if __name__ == "__main__":
    main()
