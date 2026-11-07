"""Read-only Rich observer; closing it never interrupts extraction workers."""

import argparse
import json
import os
from pathlib import Path
import time

from rich.console import Console, Group
from rich.live import Live
from rich.progress_bar import ProgressBar
from rich.table import Table
from rich.text import Text


def render(snapshot, width=100, height=40):
    """Stable worker rows; bars advance on durable sessions, not query guesses."""
    age = max(0, time.time() - snapshot.get("updated", 0))
    state = snapshot.get("state", "starting")
    heading = Text(f"BarGPT shards · {state.upper()}", style="bold")
    if age > 10:
        heading.append(f" · status stale ({age:.0f}s)", style="yellow")
    used, target = snapshot.get("bytes", 0), snapshot.get("target_bytes", 10**12)
    summary = Text(
        f"{used/10**9:,.2f} / {target/10**9:,.0f} GB  |  "
        f"{snapshot.get('samples',0):,} samples  |  "
        f"jobs {snapshot.get('jobs_completed',0):,}/{snapshot.get('jobs_total',0):,}  |  "
        f"failed {len(snapshot.get('failed',[]))}  retries {snapshot.get('retries',0)}"
    )
    rate = float(snapshot.get("bytes_per_second", 0))
    elapsed = float(snapshot.get("elapsed_seconds", 0))
    throughput = Text("Rate / ETA: warming up")
    quotas = snapshot.get("quotas")
    if state == "running" and rate > 0 and elapsed >= 60:
        throughput = Text(f"Durable write rate {rate/10**6:,.1f} MB/s")
        # Fast fine frames can reach their cap before daily/hourly. Forecast the
        # slowest remaining timeframe/class quota, rather than total bytes alone.
        # Restored counters have no per-class rate baseline, so omit that estimate
        # after resume instead of presenting historical samples as newly written.
        if quotas and not snapshot.get("reused", 0):
            estimates = []
            for frame, goal in quotas["targets"].items():
                for cohort, weight in ((0, 0.25), (1, 0.75)):
                    saved = quotas["saved"][frame][cohort]
                    remaining = max(0, int(goal * weight) - saved)
                    estimates.append(
                        remaining / (saved / elapsed) if saved else float("inf")
                    )
            seconds = max(estimates, default=0)
            if seconds < float("inf"):
                throughput.append(
                    f" · quota-based remaining ~{seconds/3600:,.1f} hours"
                )
            else:
                throughput.append(" · quota ETA warming up")
    allocation = Table(box=None, padding=(0, 1), expand=True)
    allocation.add_column("Timeframe")
    allocation.add_column("Saved / target")
    allocation.add_column("Liquid / less liquid")
    if quotas:
        names = ("100ms", "1s", "10s", "1m", "1h", "1d")
        for label, frame in zip(
            names,
            ("100000", "1000000", "10000000", "60000000", "3600000000", "86400000000"),
        ):
            low, high = quotas["saved"][frame]
            allocation.add_row(
                label,
                f"{low+high:,} / {quotas['targets'][frame]:,}",
                f"{high:,} / {low:,}",
            )
    table = Table(expand=True, box=None, padding=(0, 1))
    for name in ("Worker", "Ticker / date", "Stage", "Resolved units", "Progress"):
        table.add_column(name, no_wrap=True)
    workers = snapshot.get("workers", [])
    page_size = max(1, min(16 if width >= 90 else 12, height - (22 if quotas else 12)))
    pages = max(1, (len(workers) + page_size - 1) // page_size)
    first = (
        int(time.time()) // 10 % pages
    ) * page_size  # Rotate every ten seconds; retain readable worker rows.
    for worker in workers[first : first + page_size]:
        done, total = worker.get("done", 0), worker.get("total", 0)
        focus = f"{worker.get('ticker','')} {worker.get('day','')}"
        table.add_row(
            str(worker["id"]),
            focus,
            worker.get("phase", "queued"),
            f"{done}/{total}" if total else "—",
            ProgressBar(
                total=max(total, 1),
                completed=min(done, total),
                width=12 if width < 90 else 22,
            ),
        )
    detail = Text(
        "Per-worker progress: completed work units; saved counts advance only after publication."
        if quotas
        else "Per-worker progress: resolved sessions (saved, reused, or explicitly unavailable)."
    )
    config = snapshot.get("configuration")
    configuration = Text("")
    if config:
        configuration = Text(
            f"Queries {config['workers']} · I/O {config['io_workers']} · chunks {config['chunk_events']:,}–{config['max_chunk_events']:,} events · coarse {config['coarse_days']} days"
        )
        if state == "planning":
            configuration.append(
                f" · plan {snapshot.get('plan_done',0)}/{snapshot.get('plan_total',112)}"
            )
        if state.startswith("migration") or state == "migrating":
            configuration = Text(
                f"Market-only migration · {config['io_workers']} I/O workers · every retained field verified"
            )
    if pages > 1:
        detail.append(
            f"\nWorkers {first+1}–{min(first+page_size,len(workers))} of {len(workers)}; pages rotate every 10s."
        )
    errors = [
        Text(str(e.get("error", e))[: max(70, width * 2)], style="red")
        for e in snapshot.get("failed", [])[-2:]
    ]
    return Group(
        heading,
        summary,
        ProgressBar(total=target, completed=min(used, target)),
        Text(""),
        configuration,
        *([throughput] if quotas else []),
        *([allocation] if quotas else []),
        Text(""),
        table,
        Text(""),
        detail,
        *errors,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("D:/TradingML/runtimes/bar_gpt/shards-2019-2025-v6"),
    )
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
    if os.name == "nt":
        from research.bar_gpt.v5.progress_windows import WindowsFrame

        try:
            frame = WindowsFrame()
        except OSError:
            frame = (
                None  # ConPTY/redirected hosts may not expose a native screen buffer.
            )
        if frame is not None:
            try:
                while True:
                    snapshot = read()
                    width, height = frame.size()
                    frame.draw(render(snapshot, width, height))
                    if snapshot.get("state") in {
                        "failed",
                        "scope_complete",
                        "budget_reached",
                        "inventory_exhausted",
                        "stopped",
                    }:
                        break
                    time.sleep(1)
            except KeyboardInterrupt:
                pass
            finally:
                frame.close()
            return
    try:
        with Live(
            render(read(), console.width),
            console=console,
            auto_refresh=False,
            screen=True,
        ) as live:
            while True:
                snapshot = read()
                live.update(
                    render(snapshot, console.width, console.height), refresh=True
                )
                if snapshot.get("state") in {
                    "failed",
                    "scope_complete",
                    "budget_reached",
                    "inventory_exhausted",
                    "stopped",
                }:
                    break
                time.sleep(1)
    except KeyboardInterrupt:
        console.print("Observer closed. Extraction is unchanged.")


if __name__ == "__main__":
    main()
