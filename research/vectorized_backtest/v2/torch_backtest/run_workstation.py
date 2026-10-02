"""Workstation entry point: dates, plan, one-session preflight, or resumable run.

No arguments means all available dates, premarket, measured GPU batch sizing.
The human starts this command; the coding agent never launches the campaign.
"""
import os
import sys
from pathlib import Path
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parents[4]
if __package__ in (None, ""):
    sys.path.insert(0, str(REPO))

import argparse
import importlib.util
from importlib.metadata import version, PackageNotFoundError
from dataclasses import asdict
from datetime import datetime
import json
from uuid import uuid4
from zoneinfo import ZoneInfo
import torch

from research.vectorized_backtest.v2.torch_backtest.availability import (
    WINDOWS, configure_reader, discover_sources, select_dates, session_bounds)
from research.vectorized_backtest.v2.torch_backtest.encoding.config import Session
from research.vectorized_backtest.v2.torch_backtest.grid import Settings, build_grid, grid_manifest
from research.vectorized_backtest.v2.torch_backtest.gpu import calibrate, memory_plan
from research.vectorized_backtest.v2.torch_backtest.prepare import prepare_tape
from research.vectorized_backtest.v2.torch_backtest.progress import Progress, preparation_event, safe_diagnostic
from research.vectorized_backtest.v2.torch_backtest.runtime import (
    DEFAULT, ROOT, code_hash, configure_caches, require_runtime, source_revision, write_json)
from research.vectorized_backtest.v2.torch_backtest import run_grid


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", nargs="?", choices=("run", "preflight", "dates", "plan"), default="run")
    p.add_argument("--date", help="One YYYY-MM-DD date")
    p.add_argument("--from", dest="start_date", help="Inclusive YYYY-MM-DD")
    p.add_argument("--to", dest="end_date", help="Inclusive YYYY-MM-DD")
    p.add_argument("--sessions", "--session", nargs="+", choices=tuple(WINDOWS), default=["premarket"])
    p.add_argument("--batch", default="auto", help="auto measures 32..1024; or explicit integer")
    p.add_argument("--maximum-tape-gib", type=float, default=48.0)
    p.add_argument("--maximum-fills", type=int, default=65536)
    p.add_argument("--graph-steps", type=int, default=16)
    p.add_argument("--runtime", type=Path, default=DEFAULT)
    p.add_argument("--resume", type=Path, help="Resume the exact workstation job directory")
    p.add_argument("--plain", action="store_true", help="No live panel (also automatic for redirected output)")
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    if len(set(args.sessions)) != len(args.sessions):
        raise ValueError("Duplicate sessions")
    if args.command == "preflight" and args.sessions != ["premarket"]:
        raise ValueError("Preflight is one premarket session; use --date to choose it")
    batch = None if args.batch == "auto" else int(args.batch)
    if batch is not None and not 1 <= batch <= 1024:
        raise ValueError("Explicit batch must be 1..1024")
    runtime = require_runtime(args.runtime)
    configure_caches(runtime)
    torch.set_num_threads(1)
    configure_reader(REPO)
    job = require_runtime(args.resume or runtime / "jobs" / uuid4().hex)
    with Progress(job / "progress.jsonl", plain=args.plain) as ui:
        ui.emit({"mode": args.command, "stage": "Source catalogue", "message": "Discovering certified dates and source builds"})
        catalog = discover_sources()
        write_json(job / "available-dates.json", catalog)
        chosen, closed = select_dates(catalog["sources"], single=args.date, start=args.start_date,
            end=args.end_date, preflight=args.command == "preflight")
        units = [{**s, "session": name, "hours": session_bounds(s["day"], name)}
                 for s in chosen for name in args.sessions]
        settings, grid = Settings(), build_grid()
        manifest = grid_manifest(settings)
        try:
            compiler_version = version("triton-windows")
        except PackageNotFoundError:
            compiler_version = None
        request = {"command": args.command, "units": units, "closed_calendar_dates": closed,
                   "code": code_hash(), "commit": source_revision(REPO), "grid": manifest["approval_digest"],
                   "batch": args.batch, "maximum_tape_gib": args.maximum_tape_gib,
                   "maximum_fills": args.maximum_fills, "graph_steps": args.graph_steps,
                   "versions": {"python": sys.version.split()[0], "torch": torch.__version__,
                                "cuda": torch.version.cuda, "triton_windows": compiler_version}}
        receipt_path = job / "job.json"
        if receipt_path.exists():
            receipt = json.loads(receipt_path.read_text())
            # JSON normalizes tuples to lists; compare canonical values.
            if receipt["request"] != json.loads(json.dumps(request)):
                raise ValueError("Resume differs from frozen source/date/session/code request")
        else:
            receipt = {"request": request, "status": "active", "completed_groups": [], "gpu": None}
            write_json(receipt_path, receipt)
        write_json(job / "grid.json", manifest)
        ui.emit({"status": "Ready", "stage": "Source catalogue", "total": len(units)*len(grid),
                 "message": f"{len(chosen)} trading dates · {', '.join(args.sessions)} · {len(closed)} calendar dates excluded"})
        if args.command in ("dates", "plan"):
            for source in chosen:
                ui.console.print(f"{source['day']}  {source['tickers']:,} certified tickers  build {source['build_id'][:12]}")
            ui.emit({"status": "Complete", "stage": "Plan saved", "message": "Catalogue only; no tape, GPU calibration or financial replay"})
            receipt["status"] = "plan_complete"
            write_json(receipt_path, receipt)
            return 0
        if not torch.cuda.is_available() or torch.cuda.get_device_properties(0).total_memory < 80*1024**3:
            raise RuntimeError("Workstation run requires the 96 GB CUDA GPU; no laptop/CPU fallback")
        if importlib.util.find_spec("triton") is None:
            raise RuntimeError("Triton compiler missing. Run setup_gpu.py with workstation ml4t Python before preflight/run")
        ui.emit({"status": "Running", "gpu_total_gib": torch.cuda.get_device_properties(0).total_memory/1024**3})
        totals = {"completed": 0, "skipped": 0, "valid": 0, "invalid": 0, "replay_seconds": 0., "compile_seconds": 0.}

        def progress(event):
            for source, key in (("saved_delta", "completed"), ("reused_delta", "skipped"),
                ("valid_delta", "valid"), ("invalid_delta", "invalid"),
                ("replay_delta", "replay_seconds"), ("compile_delta", "compile_seconds")):
                totals[key] += event.pop(source, 0)
            ui.emit({**event, **totals, "gpu_gib": torch.cuda.memory_allocated()/1024**3})

        try:
            # Group equal build/window hours; early-close sessions form a separate group.
            groups = {}
            for unit in units:
                key = (unit["build_id"], unit["session"], *unit["hours"])
                groups.setdefault(key, []).append(unit)
            for index, (key, group) in enumerate(groups.items()):
                first = group[0]
                start, end = first["hours"]
                preloaded = {}
                if receipt["gpu"] is None or args.command == "preflight":
                    progress({"stage": "One-session source preflight", "focus": f"{first['day']} · {first['session']} {start}–{end} New York",
                              "message": "Preparing full selected population; missing products fail closed"})
                    session = Session(Path(first["manifest"]), Path(first["ledger"]), runtime / "source_cache",
                        datetime.fromisoformat(f"{first['day']}T{start}").replace(tzinfo=ZoneInfo("America/New_York")),
                        datetime.fromisoformat(f"{first['day']}T{end}").replace(tzinfo=ZoneInfo("America/New_York")),
                        max_prepared_gib=args.maximum_tape_gib, warmup_seconds=57600)
                    tape = prepare_tape(session, settings, maximum_gib=args.maximum_tape_gib,
                        progress=lambda v: progress(preparation_event(v))).to("cuda", args.maximum_tape_gib)
                    progress({"listings": len(tape.tickers), "tape_gib": tape.bytes/1024**3})
                    receipt["gpu"] = calibrate(tape, grid, settings, maximum_fills=args.maximum_fills,
                        graph_steps=args.graph_steps, progress=progress, batches=[batch] if batch else None)
                    write_json(receipt_path, receipt)
                    preloaded[first["day"]] = tape
                    del tape
                gpu = receipt["gpu"]
                progress({"batch": gpu["batch"], "stage": "GPU configured", "message": f"Batch {gpu['batch']} · FP64 accounting · compiled CUDA graphs"})
                if args.command == "preflight":
                    receipt["status"] = "preflight_complete"
                    write_json(receipt_path, receipt)
                    ui.emit({"status": "Complete", "stage": "Preflight passed", "message": "One premarket tape and GPU sizing checked; 4,320-grid experiment not run"})
                    return 0
                group_run = job / "campaigns" / f"{index:02}-{first['session']}-{first['build_id'][:12]}"
                command = ["--execute", "--approval-digest", manifest["approval_digest"], "--runtime", str(runtime),
                    "--resume", str(group_run), "--manifest", first["manifest"], "--ledger", first["ledger"],
                    "--dates", *[u["day"] for u in group], "--start", start, "--end", end,
                    "--batch", str(gpu["batch"]), "--device", "cuda", "--backend", "compiled_graph",
                    "--maximum-tape-gib", str(args.maximum_tape_gib), "--maximum-state-gib", str(gpu["state_gib"]),
                    "--maximum-fills", str(args.maximum_fills), "--graph-steps", str(args.graph_steps)]
                # Print the exact low-level equivalent without credentials; keep full text in artifacts.
                import subprocess
                equivalent = subprocess.list2cmdline([sys.executable, "-B", "-m",
                    "research.vectorized_backtest.v2.torch_backtest.run_grid", *command])
                (job / f"command-{index:02}.txt").write_text(equivalent, encoding="utf-8")
                if not ui.interactive:
                    ui.console.print(equivalent, markup=False)
                run_grid.main(command, progress=progress, preloaded=preloaded)
                if index not in receipt["completed_groups"]:
                    receipt["completed_groups"].append(index)
                write_json(receipt_path, receipt)
            receipt["status"] = "complete"
            write_json(receipt_path, receipt)
            ui.emit({"status": "Complete", "stage": "All selected sessions saved", "message": "Results reconciled; invalid terminal exposure has null fitness"})
        except BaseException as exc:
            receipt["status"] = "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed"
            receipt["failure_type"] = type(exc).__name__
            write_json(receipt_path, receipt)
            raise
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as exc:
        # Progress has retained the useful reason and redacted traceback in error.json.
        print(safe_diagnostic(exc), file=sys.stderr)
        raise SystemExit(1)
