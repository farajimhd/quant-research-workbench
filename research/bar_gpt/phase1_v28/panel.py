"""Read-only Rich monitor: training owns progress.json; this viewer owns no job."""

import os

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

import argparse
import json
import time
import sys
from pathlib import Path
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

TERMINAL_STATES = {
    "complete",
    "failed",
    "stopped",
    "bounded_complete",
    "qualification_complete",
    "interrupted_use_last_checkpoint",
}

# Display order follows the canonical six Phase1 timeframes. Reading the already
# reduced report avoids importing Torch or doing any validation work in the viewer.
DISPLAY_FRAMES = ("100ms", "1s", "10s", "1m", "1h", "daily")


class EvaluationReader:
    """Cache complete evaluation reports; never mix metrics from different steps."""

    def __init__(self, root, latest_key="latest_evaluation", metrics_key="timeframe_metrics"):
        self.root, self.stamp, self.report = root, None, {}
        self.latest_key, self.metrics_key = latest_key, metrics_key

    def enrich(self, state):
        latest = state.get(self.latest_key, {})
        scope = latest.get("scope")
        if scope not in ("development", "validation", "training_sample"):
            return state
        path = self.root / f"{scope}-latest.json"
        try:
            stamp = (scope, path.stat().st_mtime_ns)
            if stamp != self.stamp:
                report = json.loads(path.read_text(encoding="utf-8"))
                self.report, self.stamp = report, stamp
        except (OSError, ValueError):
            pass
        state = dict(state)
        state[self.metrics_key] = (
            self.report.get("metrics", {})
            if self.stamp and self.stamp[0] == scope and self.report.get("step") == latest.get("step")
            else {}
        )
        return state


def number(value, suffix="", precision=2):
    """Unknown is not zero; omit numerical claims until a measurement exists."""
    return "n/a" if value is None else f"{value:,.{precision}f}{suffix}"


def enable_virtual_terminal():
    """Enable buffered ANSI frames instead of legacy per-cell console writes."""
    if os.name != "nt" or not sys.stdout.isatty():
        return os.name != "nt"
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetStdHandle.restype = wintypes.HANDLE
    kernel.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    handle = kernel.GetStdHandle(-11)
    mode = wintypes.DWORD()
    if not kernel.GetConsoleMode(handle, ctypes.byref(mode)):
        return False
    return bool(kernel.SetConsoleMode(handle, mode.value | 0x0004))


def maximize_console():
    """Size only this interactive viewer; never change other application windows."""
    if os.name != "nt" or not sys.stdout.isatty():
        return
    import ctypes
    from ctypes import wintypes

    class Coord(ctypes.Structure):
        _fields_ = [("X", ctypes.c_short), ("Y", ctypes.c_short)]

    class Font(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.ULONG), ("nFont", wintypes.DWORD),
                    ("dwFontSize", Coord), ("FontFamily", wintypes.UINT),
                    ("FontWeight", wintypes.UINT), ("FaceName", wintypes.WCHAR * 32)]

    kernel, user = ctypes.windll.kernel32, ctypes.windll.user32
    kernel.GetStdHandle.restype = wintypes.HANDLE
    kernel.GetConsoleWindow.restype = wintypes.HWND
    kernel.SetCurrentConsoleFontEx.argtypes = [wintypes.HANDLE, wintypes.BOOL, ctypes.POINTER(Font)]
    user.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    font = Font()
    font.cbSize, font.dwFontSize, font.FontWeight, font.FaceName = ctypes.sizeof(Font), Coord(8, 16), 400, "Consolas"
    kernel.SetCurrentConsoleFontEx(kernel.GetStdHandle(-11), False, ctypes.byref(font))
    user.ShowWindow(kernel.GetConsoleWindow(), 3)


class SolidBar:
    """Use solid cells even on consoles where Rich's thin bar looks like dashes."""

    def __init__(self, total, completed):
        self.fraction = min(1.0, max(0.0, completed / total))

    def __rich_console__(self, console, options):
        width = max(1, options.max_width - 8)
        filled = round(width * self.fraction)
        bar = Text("\u2588" * filled, style="bold cyan")
        bar.append("\u2591" * (width - filled), style="bright_black")
        bar.append(f" {self.fraction:6.1%}")
        yield bar


def timeframe_table(metrics, title, width):
    """One column definition serves both populations, making comparison direct."""
    table = Table(title=title, title_justify="left", box=None, padding=(0, 1), expand=True)
    expanded = width >= 180
    if expanded:
        headers = ("Frame", "Close $", "MAPE%", "Med%~", "P90%~", "Tdir%", "Bdir%", "Adir%",
                   "Range%", "Vbal%", "Bbal%", "Abal%", "Vlog", "qlog",
                   "<.1%", ".1-1%", "1-5%", "5-20%", "20-100%", "100+%", "Keep%")
    elif width < 100:
        headers = ("Frame", "Close $", "Price T/B/A %", "Act V/B/A %")
    else:
        headers = ("Frame", "Close MAE $", "Trade dir %", "Bid dir %", "Ask dir %",
                   "Vol bal %", "BidSz bal %", "AskSz bal %")
    for i, label in enumerate(headers):
        table.add_column(label, justify="left" if i == 0 else "right",
                         no_wrap=True,
                         width=(6 if expanded else [6, 8, 15, 15][i] if width < 100 else 6 if i == 0 else 12),
                         style="cyan" if i == 0 else "yellow" if i == 1 or expanded and i >= 14 else "magenta")
    for frame in DISPLAY_FRAMES:
        price = [metrics.get(f"candle/{frame}/{family}/moving_direction_pct") for family in ("trade", "bid", "ask")]
        activity = [metrics.get(f"activity_direction/{frame}/{channel}/balanced_accuracy_pct") for channel in ("trade_volume", "bid_size", "ask_size")]
        if expanded:
            quality = f"quality/{frame}/trade_close"
            scanned = metrics.get(f"eligibility/{frame}/scanned_bars", 0)
            kept = metrics.get(f"eligibility/{frame}/eligible_bars", 0)
            values = [metrics.get(f"mape_pct/{frame}/trade_close"),
                      metrics.get(quality + "/ape_median_upper_bound_pct"),
                      metrics.get(quality + "/ape_p90_upper_bound_pct"), *price,
                      metrics.get(f"range/{frame}/trade/bin_accuracy_pct"), *activity,
                      metrics.get(f"quality/{frame}/trade_volume/log_mae"),
                      metrics.get(f"average_volume_ratio/{frame}/log_mae"),
                      *[metrics.get(f"price_band/{frame}/{band}/mape_pct") for band in
                        ("below_0.1", "0.1_1", "1_5", "5_20", "20_100", "100_plus")],
                      100 * kept / scanned if scanned else None]
            table.add_row(frame, number(metrics.get(f"raw_mae/{frame}/trade_close"), precision=3),
                          *[number(v) for v in values])
            continue
        values = (["/".join(number(v, precision=1) for v in group) for group in (price, activity)]
                  if width < 100 else [number(v) for v in price + activity])
        table.add_row(frame, number(metrics.get(f"raw_mae/{frame}/trade_close"), precision=2 if width < 100 else 4), *values)
    return table


def render(state, now=None, width=120, height=40):
    """Pure renderer, allowing narrow-terminal and stale-state tests without GPU."""
    now = time.time() if now is None else now
    age = now - state.get("updated", 0)
    status = state.get("state", "waiting for trainer")
    if status in TERMINAL_STATES:
        now = state.get("updated", now)
    stale = age > 30 and status not in TERMINAL_STATES
    color = "red" if status == "failed" or stale else "green" if status == "complete" else "cyan"
    heading = f"{status.upper()} | {state.get('stage', 'no status received')}"
    coverage_mismatch = bool(state.get("validation_complete")) and (
        state.get("validation_windows") != state.get("validation_total")
    )
    if coverage_mismatch:
        heading += " | COVERAGE MISMATCH"
        color = "red"
    if stale:
        heading += " | STALE: check trainer"
    elif (
        state.get("stage") in ("training", "development", "validation")
        and state.get("last_progress_at")
        and now - state["last_progress_at"] > 300
        and status not in TERMINAL_STATES
    ):
        heading += " | NO PROGRESS >5 min"
        color = "yellow"
    progress = Table.grid(expand=True)
    progress.add_column(ratio=1)
    for label, done, total in (
        (
            f"Training epoch {state.get('epoch', 'n/a')}/{state.get('epochs', 'n/a')}",
            state.get("epoch_done", 0),
            state.get("epoch_total", 0),
        ),
        (
            "Validation"
            + (
                " complete"
                if state.get("validation_complete") and not coverage_mismatch
                else " (partial)"
            ),
            state.get("validation_windows", 0),
            state.get("validation_total", 0),
        ),
    ):
        if label.startswith("Validation") and state.get("stage") not in ("development", "validation") and not coverage_mismatch:
            continue
        if label.startswith("Training") and state.get("stage") in ("development", "validation"):
            continue
        progress.add_row(
            Text(f"{label} | {done:,}/{total:,} windows" if total else f"{label} | not started")
        )
        if total:
            progress.add_row(SolidBar(total, done))
    latest = state.get("latest_development_evaluation", state.get("latest_evaluation", {}))
    metrics = state.get("timeframe_metrics", {})
    training = state.get("latest_training_evaluation", {})
    train_table = timeframe_table(state.get("training_timeframe_metrics", {}),
        f"TRAINING SAMPLE | s{training.get('step', '-')} | n{training.get('windows', 0):,} | L{number(training.get('loss'))}", width)
    frames = timeframe_table(metrics,
        f"DEVELOPMENT | s{latest.get('step', '-')} | n{latest.get('windows', 0):,} | L{number(latest.get('loss'))}", width)
    full = state.get("latest_full_evaluation", {})
    full_table = timeframe_table(state.get("full_timeframe_metrics", {}),
        f"FULL VALIDATION | s{full.get('step', '-')} | n{full.get('windows', 0):,} | L{number(full.get('loss'))}", width)
    # Stable label/value pairs make the run-level status distinct from the two
    # population-specific metric tables. Values stay next to their headings.
    global_metrics = Table.grid(padding=(0, 1), expand=True)
    pairs = 3 if width >= 100 else 2
    for _ in range(pairs):
        global_metrics.add_column(ratio=1, no_wrap=True)
    entries = [
        ("Train loss", number(state.get("loss"), precision=4)),
        ("Train speed", number(state.get("windows_per_second"), " w/s", 0)),
        ("Epoch ETA", number(state.get("epoch_eta_seconds"), " s", 0)),
        ("Learning rate", format(state["learning_rate"], ".2e") if state.get("learning_rate") is not None else "n/a"),
        ("Encoder LR", format(state["encoder_learning_rate"], ".2e") if state.get("encoder_learning_rate") is not None else "n/a"),
        ("Eval speed", number(state.get("validation_windows_per_second"), " w/s", 0)),
        ("Eval ETA", number(state.get("validation_eta_seconds"), " s", 0)),
        ("GPU / memory", number(state.get("gpu_util"), "%", 0) + " / " + number(state.get("gpu_memory_mib", 0) / 1024, " GiB", 1) if state.get("gpu_memory_mib") is not None else "n/a"),
    ]
    for start in range(0, len(entries), pairs):
        cells = []
        for label, value in entries[start:start+pairs]:
            cell = Text(f"{label:<13} ", style="dim")
            cell.append(value, style="bold cyan")
            cells.append(cell)
        global_metrics.add_row(*cells)
    resources = Panel(global_metrics, title="RUN STATUS", title_align="left", border_style="blue")
    legend = "Close MAE: trade. Price: open-close moving direction. Activity: previous-bar balanced direction."
    if width >= 180:
        legend = "Close errors: trade; ~ histogram upper bound. Bands: all 12 price fields MAPE%. T/B/A direction: open-close. V/B/A activity: adjacent-bar balanced%. Range: trade bin accuracy. Vlog/qlog: log MAE."
    if width < 100:
        legend = "T=trade, B=bid, A=ask, V=volume; activity balanced."
    content = [
        Text(heading, style=color),
        progress,
        resources,
        train_table,
        frames,
        full_table,
        Text(legend, style="dim"),
    ]
    if height < 36:
        compact = Text("RUN STATUS | ", style="bold blue")
        compact.append(f"L {number(state.get('loss'))} | {number(state.get('windows_per_second'), ' w/s', 0)} | ETA {number(state.get('epoch_eta_seconds'), 's', 0)}", style="cyan")
        if width >= 100:
            compact.append(f" | Eval {number(state.get('validation_windows_per_second'), ' w/s', 0)} | LR {state.get('learning_rate', 0):.1e}", style="cyan")
        content = [progress, compact, train_table, frames, full_table]
    if state.get("error"):
        content.append(Text(str(state["error"]), style="red"))
    if height >= 36: content.append(Text(f"Step {state.get('step', 'n/a')} | saved {state.get('checkpoint_step', 'n/a')} | windows/s (64 bars each)", style="dim"))
    if height >= 40:
        baseline = state.get("frozen_baseline_loss")
        current = latest.get("loss")
        gain = 100 * (1-current/baseline) if baseline and current is not None else None
        masked=state.get('latest_masked_evaluation',{})
        content.insert(3, Text(f"Parent recon baseline {number(baseline, precision=4)} | visible gain {number(gain, '%')} | masked dev recon {number(masked.get('loss'))} at step {masked.get('step','-')} | 4-8 masked bars | 64 x 32 exported", style="green"))
    return Panel(Group(*content), title=f"Phase1 V28 | {state.get('precision', 'FP32')} | masked mixer + encoder fine-tuning", border_style=color)


def main():
    """Use --once for redirected logs; never emit cursor control to a pipe."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--maximize", action="store_true", help="Maximize this viewer's Windows console")
    parser.add_argument(
        "--keep-open", action="store_true", help="Keep final status visible until Ctrl+C"
    )
    args = parser.parse_args()
    if args.maximize and not args.once:
        maximize_console()
    # Redirected Windows streams often default to cp1252, which cannot encode
    # solid bars. Real consoles and UTF-8 log readers both support these glyphs.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    vt = enable_virtual_terminal()
    if vt and os.environ.get("TERM") in ("dumb", "unknown"):
        # Capability was checked on the actual console handle, so an inherited
        # noninteractive launcher label must not disable its live rendering.
        os.environ["TERM"] = "xterm-256color"
    console = Console(legacy_windows=False if vt else None)
    last = {}
    precision = None
    evaluation = EvaluationReader(args.run, "latest_development_evaluation")
    full_evaluation = EvaluationReader(args.run, "latest_full_evaluation", "full_timeframe_metrics")
    training_evaluation = EvaluationReader(args.run, "latest_training_evaluation", "training_timeframe_metrics")

    def read():
        nonlocal last, precision
        try:
            last = json.loads((args.run / "progress.json").read_text())
        except (OSError, ValueError):
            # Keep the last trustworthy observation if a publication is briefly
            # unavailable. Its age still increases, making staleness visible.
            pass
        if precision is None:
            try:
                manifest = json.loads((args.run / "manifest.json").read_text())
                precision = manifest.get("arguments", {}).get("precision", "fp32").upper()
            except (OSError, ValueError):
                pass
        if precision is not None:
            last = dict(last, precision=precision)
        return full_evaluation.enrich(training_evaluation.enrich(evaluation.enrich(last)))

    if args.once or not console.is_terminal:
        console.print(render(read(), width=console.width, height=console.height))
        return
    try:
        # Home and overwrite a buffered frame; do not erase every previous line.
        # This is the ONLY refresh loop, avoiding duplicate timer-driven paints.
        with Live(render(read(), width=console.width, height=console.height), console=console, screen=vt, auto_refresh=False) as live:
            frames = 0
            while True:
                state = read()
                live.update(render(state, width=console.width, height=console.height), refresh=True)
                frames += 1
                if frames == 1 or frames % 10 == 0:
                    # This viewer receipt proves the interactive mode and advancing
                    # redraws without modifying the trainer's progress authority.
                    receipt = dict(pid=os.getpid(), updated=time.time(), frames=frames,
                                   virtual_terminal=vt, alternate_screen=vt,
                                   columns=console.width, rows=console.height)
                    (args.run / "panel-status.json").write_text(json.dumps(receipt), encoding="utf-8")
                if state.get("state") in TERMINAL_STATES and not args.keep_open:
                    break
                time.sleep(1)
    except KeyboardInterrupt:
        console.print("Viewer closed; trainer was not interrupted.")


if __name__ == "__main__":
    main()
