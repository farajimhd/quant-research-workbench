"""Independent Rich observer for training/profiling; closing it never stops the run."""

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


def render(state: dict, width: int = 100):
    """Keep configuration, completed work, performance, and failures visible in stable rows."""
    age = max(0, time.time() - state.get("updated", 0))
    title = Text(f"BarGPT MAE | {state.get('state', 'waiting').upper()}", style="bold")
    if age > 10:
        title.append(f" | STALE {age:.0f}s", style="yellow")
    config = state.get("config", {})
    configuration = Text(
        f"{config.get('name', '-')} | batch {config.get('batch_size', '-')} | "
        f"{config.get('precision', '-')} | CPU workers {config.get('workers', '-')} | "
        f"prefetch {config.get('prefetch', '-')} per worker"
    )
    done, total = state.get("done", 0), state.get("total", 0)
    stage = Text(f"{state.get('stage', 'waiting for controller')} | {done:,}/{total:,} | "
                 f"trial {state.get('trial', 0)}/{state.get('trials', 0)}")
    metrics = state.get("metrics", {})
    summary = Text(f"{metrics.get('samples_per_second', 0):,.0f} samples/s | "
                   f"GPU {state.get('gpu_util', 0):.0f}% | allocated {metrics.get('peak_gib', 0):.2f} GiB | "
                   f"loader wait {metrics.get('loader_wait_pct', 0):.1f}% | loss {metrics.get('loss', 0):.5f}")
    workers = Table(box=None, expand=True)
    for name in ("Worker", "Stage", "Batches", "No-price exclusions", "Prepare ms"):
        workers.add_column(name)
    for item in state.get("workers", [])[:8 if width >= 90 else 4]:
        workers.add_row(str(item.get("id", "-")), item.get("stage", "queued"),
                        str(item.get("batches", 0)), f"{item.get('excluded_no_price', 0):,}",
                        f"{item.get('prepare_ms', 0):.1f}")
    comparisons = Table(box=None, expand=True)
    for name in ("Completed configuration", "samples/s", "GiB", "Status"):
        comparisons.add_column(name)
    for item in state.get("results", [])[-5:]:
        comparisons.add_row(item.get("label", "-"), f"{item.get('samples_per_second', 0):,.0f}",
                            f"{item.get('peak_gib', 0):.2f}", item.get("status", "-"))
    error = Text(str(state.get("error", "")), style="red")
    return Group(title, configuration, stage, ProgressBar(total=max(1, total), completed=done),
                 summary, workers, comparisons, error)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    console = Console()
    previous = dict(state="waiting", updated=time.time())

    def read():
        nonlocal previous
        try:
            previous = json.loads((args.run / "progress.json").read_text())
        except (OSError, ValueError):
            pass  # Preserve last trustworthy snapshot; its age exposes a dead controller.
        return previous

    window = dict(pid=os.getpid())
    if os.name == "nt" and console.is_terminal:
        import ctypes
        ctypes.windll.kernel32.GetConsoleWindow.restype = ctypes.c_void_p
        handle = ctypes.windll.kernel32.GetConsoleWindow()
        ctypes.windll.user32.ShowWindow(ctypes.c_void_p(handle), 9)
        window["visible"] = bool(ctypes.windll.user32.IsWindowVisible(ctypes.c_void_p(handle)))
    (args.run / "panel-pid.json").write_text(json.dumps(window))
    if args.once or not console.is_terminal:
        console.print(render(read(), console.width))
        return
    try:
        with Live(render(read(), console.width), console=console, refresh_per_second=1) as live:
            while True:
                snapshot = read()
                live.update(render(snapshot, console.width))
                if snapshot.get("state") in {"complete", "failed", "stopped"}:
                    break
                time.sleep(1)
    except KeyboardInterrupt:
        console.print("Observer closed; controller is unchanged.")


if __name__ == "__main__":
    main()
