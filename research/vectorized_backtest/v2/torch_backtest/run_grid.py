"""Plan by default. Historical replay requires the exact explicit grid digest.

Campaign progress is sealed at completed batch boundaries. Interrupted active
batches are replayed from their original account state; no partial financial
result is accepted as complete. Runner snapshots separately support exact
prefix/resume validation. No strategy registration, publication or live action.
"""
import os
import sys

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True

import argparse
from dataclasses import asdict
from datetime import datetime
import json
import math
from pathlib import Path
import subprocess
from uuid import uuid4
from zoneinfo import ZoneInfo

import polars as pl
import torch

from .encoding.config import Session, DEFAULT_EXCLUDED_TICKERS
from .grid import Settings, build_grid, grid_manifest
from .prepare import prepare_tape
from .progress import preparation_event
from .runner import SqueezeRunner
from .runtime import DEFAULT, code_hash, configure_caches, file_hash, require_runtime, write_json, source_revision


def approval_check(digest, manifest):
    if not digest or digest != manifest["approval_digest"]:
        raise ValueError("Historical execution requires explicit approval of this exact grid/settings digest")


def export_ledger(runner, candidate_ids, path):
    rows = []
    counts = runner.fill_count.cpu().tolist()
    ledger = runner.ledger[:, :max(counts[:len(candidate_ids)], default=0)].cpu()
    for b, count in enumerate(counts[:len(candidate_ids)]):
        if count:
            frame = pl.DataFrame(ledger[b, :count].numpy(), schema=["utc_second", "ticker_index",
                "position_slot", "side", "quantity", "price", "fee", "exit_reason", "clock_index"], orient="row")
            frame = frame.with_columns(pl.lit(candidate_ids[b]).alias("candidate_id"),
                *[pl.col(k).cast(pl.Int64) for k in ("utc_second", "ticker_index", "position_slot", "side",
                                                    "quantity", "exit_reason", "clock_index")])
            frame = frame.with_columns(pl.col("ticker_index").replace_strict(dict(enumerate(runner.tape.tickers))).alias("ticker"))
            rows.append(frame)
    if rows:
        pl.concat(rows).write_parquet(path)
    else:
        pl.DataFrame(schema={"candidate_id": pl.String, "ticker": pl.String, "quantity": pl.Int64}).write_parquet(path)


def export_orders(runner, candidate_ids, path):
    """Normalized independent parent/protection witnesses, excluding padding."""
    data = {name: getattr(runner, name)[:len(candidate_ids)].cpu() for name in
            ("requested_quantity", "buy_filled", "remaining", "quantity", "buy_submitted", "buy_deadline",
             "first_fill", "average", "initial_stop", "stop", "target", "buy_paid", "exit_filled", "exit_paid")}
    keep = data["requested_quantity"] > 0
    b, n, slot = keep.nonzero(as_tuple=True)
    columns = {"candidate_id": [candidate_ids[i] for i in b.tolist()],
               "ticker": [runner.tape.tickers[i] for i in n.tolist()], "position_slot": slot.numpy()}
    for name, values in data.items():
        if values.ndim == 3:
            columns[name] = values[keep].numpy()
        else:
            for i, role in enumerate(("target", "stop", "rotation", "terminal")):
                columns[role + "_" + name] = values[..., i][keep].numpy()
    frame = pl.DataFrame(columns).with_columns(
        (pl.col("requested_quantity") - pl.col("buy_filled") - pl.col("remaining")).alias("cancelled_quantity"))
    frame.write_parquet(path)


def aggregate_results(run, receipt, grid):
    """Complete session/candidate reconciliation before ranking any fitness."""
    rows = []
    seen = set()
    for unit in receipt["completed"].values():
        summary_name = next(name for name in unit["files"] if name.endswith(".json"))
        summary = json.loads((run / summary_name).read_text())
        day = Path(summary_name).parts[0]
        for i, identity in enumerate(summary["candidate_ids"]):
            if (day, identity) in seen:
                raise ValueError("Duplicate session/candidate result")
            seen.add((day, identity))
            rows.append({"session": day, "candidate_id": identity,
                         "valid": summary["terminal_valid"][i], "objective": summary["objective"][i],
                         **{key: summary[key][i] for key in
                            ("net_pnl", "fees", "drawdown", "entered", "rotations", "fill_count",
                             "requested_entry_shares", "filled_entry_shares", "filled_entry_orders",
                             "unfilled_entry_orders", "partially_filled_entry_orders")}})
    expected = {(day, c.identity) for day in receipt["request"]["dates"] for c in grid}
    if seen != expected:
        raise ValueError("Incomplete grid reconciliation; no ranking published")
    frame = pl.DataFrame(rows)
    frame.write_parquet(run / "session-results.parquet")
    summary = frame.group_by("candidate_id").agg(
        pl.col("valid").all().alias("valid"), pl.len().alias("sessions"),
        pl.col("objective").mean().alias("mean_objective"),
        pl.col("net_pnl").mean().alias("mean_net_pnl"),
        pl.col("drawdown").max().alias("worst_drawdown"),
        *[pl.col(key).sum().alias("total_" + key) for key in ("fees", "entered", "rotations", "fill_count",
            "requested_entry_shares", "filled_entry_shares", "filled_entry_orders", "unfilled_entry_orders",
            "partially_filled_entry_orders")]
    ).with_columns(pl.when(pl.col("valid")).then(pl.col("mean_objective")).otherwise(None).alias("mean_objective"))
    settings = pl.DataFrame([{"candidate_id": c.identity, **asdict(c)} for c in grid])
    summary = settings.join(summary, on="candidate_id", validate="1:1").sort(
        ["mean_objective", "candidate_id"], descending=[True, False], nulls_last=True)
    summary.write_csv(run / "grid-results.csv")
    return {"evaluated_configurations": len(grid), "valid_configurations": summary["valid"].sum(),
            "invalid_configurations": summary.filter(~pl.col("valid")).height,
            "session_candidate_results": len(rows)}


def main(argv=None, *, progress=None, preloaded=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="Historical experiment; needs approved digest")
    parser.add_argument("--approval-digest")
    parser.add_argument("--settings", type=Path, help="JSON Settings overrides, included in approval digest")
    parser.add_argument("--runtime", type=Path, default=DEFAULT)
    parser.add_argument("--resume", type=Path, help="Existing campaign directory with identical code/grid/source inputs")
    parser.add_argument("--manifest", type=Path, help="Certified market-day-core-v5 build manifest")
    parser.add_argument("--ledger", type=Path, help="Read-only source certification ledger")
    parser.add_argument("--dates", nargs="+", help="Explicit YYYY-MM-DD sessions; no hidden default population")
    parser.add_argument("--start", default="04:00")
    parser.add_argument("--end", default="09:30")
    parser.add_argument("--exclude-tickers", nargs="*", default=list(DEFAULT_EXCLUDED_TICKERS))
    parser.add_argument("--batch", type=int, default=8)
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cuda")
    parser.add_argument("--backend", choices=("eager", "compile", "cudagraph", "compiled_graph"), default="compiled_graph")
    parser.add_argument("--graph-steps", type=int, default=16)
    parser.add_argument("--maximum-fills", type=int, default=16384)
    parser.add_argument("--maximum-tape-gib", type=float, default=4.0)
    parser.add_argument("--maximum-state-gib", type=float, default=2.0)
    args = parser.parse_args(argv)
    settings = Settings(**json.loads(args.settings.read_text())) if args.settings else Settings()
    manifest = grid_manifest(settings)
    grid = build_grid()
    if len(grid) != manifest["candidate_count"]:
        raise ValueError("Grid count differs from approval manifest")
    if args.execute:
        approval_check(args.approval_digest, manifest)
        if not args.manifest or not args.ledger or not args.dates:
            parser.error("Execution requires explicit --manifest, --ledger and --dates")
        if len(set(args.dates)) != len(args.dates) or not 1 <= args.batch <= 1024:
            parser.error("Require unique dates and batch 1..1024")
    runtime = require_runtime(args.runtime)
    configure_caches(runtime)
    torch.set_num_threads(1)
    if not args.execute:
        destination = require_runtime(runtime / "plans" / uuid4().hex)
        write_json(destination / "grid.json", manifest)
        pl.DataFrame([{"candidate_id": c.identity, **asdict(c)} for c in grid]).write_csv(destination / "grid.csv")
        print(json.dumps({"status": "awaiting_user_approval", "configurations": len(grid),
                          "approval_digest": manifest["approval_digest"], "plan": str(destination)}, indent=2))
        return 0
    # Environment discovery may load credentials; none enter reports or output.
    from research.mlops.env import discover_env_files, load_env_files
    repo = Path(__file__).resolve().parents[4]
    load_env_files(discover_env_files(repo), verbose=False)
    commit = source_revision(repo)
    run = require_runtime(args.resume or runtime / "campaigns" / uuid4().hex)
    request = {"grid": manifest["approval_digest"], "code": code_hash(), "commit": commit,
               "population_rule": "pinned-preopen-is_tradable=1", "excluded_tickers": sorted(args.exclude_tickers),
               "market_manifest_hash": file_hash(args.manifest), "dates": args.dates,
               "market_ledger": str(args.ledger.resolve()), "start": args.start, "end": args.end,
               "batch": args.batch, "device": args.device, "backend": args.backend,
               "graph_steps": args.graph_steps, "maximum_fills": args.maximum_fills,
               "maximum_tape_gib": args.maximum_tape_gib, "maximum_state_gib": args.maximum_state_gib}
    receipt_path = run / "campaign.json"
    if receipt_path.exists():
        receipt = json.loads(receipt_path.read_text())
        if receipt["request"] != request:
            raise ValueError("Resume request differs from frozen campaign")
    else:
        receipt = {"request": request, "status": "active", "completed": {}, "failed": {},
                   "total_units": len(args.dates) * ((len(grid) + args.batch - 1) // args.batch)}
        write_json(receipt_path, receipt)
        write_json(run / "grid.json", manifest)
    ny = ZoneInfo("America/New_York")
    current = None
    try:
        for day in args.dates:
            if progress:
                progress({"stage": "Preparing certified tape", "focus": f"{day} · {args.start}–{args.end} New York",
                          "ticks": 0, "total_ticks": 0, "message": "Checking source certificates and causal products"})
            session = Session(args.manifest, args.ledger, runtime / "source_cache",
                datetime.fromisoformat(f"{day}T{args.start}").replace(tzinfo=ny),
                datetime.fromisoformat(f"{day}T{args.end}").replace(tzinfo=ny), warmup_seconds=57600,
                max_prepared_gib=args.maximum_tape_gib, excluded_tickers=tuple(sorted(args.exclude_tickers)))
            tape = (preloaded.pop(day) if preloaded and day in preloaded else
                    prepare_tape(session, settings, maximum_gib=args.maximum_tape_gib,
                        progress=(lambda value: progress(preparation_event(value))) if progress else print)
                    .to(args.device, args.maximum_tape_gib))
            if progress:
                progress({"listings": len(tape.tickers), "tape_gib": tape.bytes/1024**3, "batch": args.batch})
            session_dir = require_runtime(run / day)
            runner = None
            for offset in range(0, len(grid), args.batch):
                current = f"{day}/{offset}"
                if current in receipt["completed"]:
                    saved = receipt["completed"][current]
                    if saved["source"] != tape.provenance["fingerprint"]:
                        raise ValueError("Completed batch source changed")
                    for name, digest in saved["files"].items():
                        if file_hash(run / name) != digest:
                            raise ValueError("Completed batch artifact corrupt")
                    if progress:
                        output = json.loads((run / next(name for name in saved["files"] if name.endswith(".json"))).read_text())
                        progress({"saved_delta": len(output["candidate_ids"]), "reused_delta": len(output["candidate_ids"]),
                                  "valid_delta": output["valid_count"], "invalid_delta": output["invalid_count"]})
                    continue
                selected = grid[offset:offset + args.batch]
                actual = len(selected)
                selected += [selected[-1]] * (args.batch - actual)
                if runner is None:
                    if progress:
                        progress({"stage": "Compiling GPU replay", "message": "Fixed batch graph; setup is not replay progress"})
                    runner = SqueezeRunner(tape, selected, settings, backend=args.backend,
                        graph_steps=args.graph_steps, maximum_fills=args.maximum_fills,
                        maximum_state_gib=args.maximum_state_gib).compile()
                    if progress:
                        progress({"compile_delta": runner.setup_seconds})
                else:
                    runner.set_candidates(selected)
                if progress:
                    progress({"stage": "Replaying configurations", "message": f"Candidates {offset+1:,}–{offset+actual:,} of {len(grid):,}",
                              "ticks": 0, "total_ticks": len(tape.clocks)})
                else:
                    print(json.dumps({"active": current, "completed": len(receipt["completed"]),
                    "queued": receipt["total_units"] - len(receipt["completed"]) - 1,
                    "failed": len(receipt["failed"])}), flush=True)
                result = runner.run(progress=(lambda value: progress({"ticks": value["completed_seconds"],
                                     "total_ticks": value["total_seconds"]})) if progress else None)
                if progress:
                    progress({"stage": "Saving verified batch", "message": "Writing order/fill ledgers and checksums"})
                output = {key: value.cpu().tolist()[:actual] if isinstance(value, torch.Tensor) else value
                          for key, value in result.items()}
                output["objective"] = [x if math.isfinite(x) else None for x in output["objective"]]
                output["candidate_ids"] = [c.identity for c in selected[:actual]]
                output["source"] = tape.provenance["fingerprint"]
                output["candidate_status"] = ["valid" if valid else "invalid_terminal_exposure"
                                              for valid in output["terminal_valid"]]
                output["valid_count"] = sum(output["terminal_valid"])
                output["invalid_count"] = actual - output["valid_count"]
                summary = session_dir / f"batch-{offset:05}.json"
                fills = session_dir / f"batch-{offset:05}-fills.parquet"
                orders = session_dir / f"batch-{offset:05}-orders.parquet"
                export_ledger(runner, [c.identity for c in selected[:actual]], fills)
                export_orders(runner, [c.identity for c in selected[:actual]], orders)
                write_json(summary, output)
                receipt["completed"][current] = {"source": output["source"], "files":
                    {str(path.relative_to(run)): file_hash(path) for path in (summary, fills, orders)}}
                receipt["failed"].pop(current, None)
                write_json(receipt_path, receipt)
                if progress:
                    progress({"saved_delta": actual, "valid_delta": output["valid_count"],
                              "invalid_delta": output["invalid_count"], "replay_delta": result["replay_seconds"]})
            del runner, tape
            if args.device == "cuda":
                torch.cuda.empty_cache()
        receipt["reconciliation"] = aggregate_results(run, receipt, grid)
        receipt["status"] = "complete"
    except BaseException as exc:
        receipt["status"] = "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed"
        receipt["failed"][current or "preflight"] = {"type": type(exc).__name__}
        write_json(receipt_path, receipt)
        raise
    write_json(receipt_path, receipt)
    if not progress:
        print(json.dumps({"status": receipt["status"], "run": str(run)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
