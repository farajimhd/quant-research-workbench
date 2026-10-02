"""Certify Strategy 1 inputs and compare saved app / GPU 100ms / GPU 1s.

SELECT-only inputs, immutable runtime evidence, separately measured setup and
replay. The 1s variant changes the broker clock; strategy observations remain
100ms. Saved app timing is documentary evidence, not a fresh app measurement.
"""

import argparse
import json
import os
import subprocess
import sys
from contextlib import closing
from dataclasses import asdict
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from time import perf_counter
from uuid import uuid4

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True

from scripts.clickhouse.report_strategy_one_trades import SelectOnly
from scripts.clickhouse.smoke_strategy_one_backtest import _load_private_credentials
from src.backend.backtest_market_data import market_day_boundary
from src.backend.backtest_market_keeper_pool import MARKET_CERTIFICATE_KEEPER_POOL
from src.backend.backtest_v3_clients import v3_client
from src.backend.backtest_v4_saved_review import (
    load_v4_performance_report,
    load_v4_terminal_review_page,
)
from src.trading_runtime.arte_journal_writer import backtest_v4_operator_client_from_env

from .strategy_one_inputs import load_replay_frames, load_saved_inputs

RUNTIME = Path("D:/TradingML/runtimes")
REPO = Path(__file__).resolve().parents[4]


def write(path, value):
    path.write_text(json.dumps(value, indent=2, default=str), encoding="utf-8")


def reference_fills(performance, session, through_ms):
    origin = market_day_boundary(session, 0).timestamp()
    result = []
    for row in performance["position_executions"]:
        at = round(
            (datetime.fromisoformat(row["source_event_time"]).timestamp() - origin)
            * 1000
        )
        if at <= through_ms:
            result.append(
                {
                    "boundary_ms": at,
                    "ticker": row["instrument"]["symbol"],
                    "quantity": float(row["quantity"])
                    * (1 if row["side"] == "BUY" else -1),
                    "price": float(row["price"]),
                    "fee": float(row["commission"]),
                    "broker_order_id": row["broker_order_id"],
                }
            )
    return result


def fill_comparison(reference, observed):
    fields = ("boundary_ms", "ticker", "quantity", "price", "fee")
    mismatches = []
    for index in range(max(len(reference), len(observed))):
        a = reference[index] if index < len(reference) else None
        b = observed[index] if index < len(observed) else None
        if (
            a is None
            or b is None
            or any(
                abs(a[name] - b[name]) > 1e-8
                if isinstance(a[name], (int, float))
                else a[name] != b[name]
                for name in fields
            )
        ):
            mismatches.append({"fill_index": index, "reference": a, "observed": b})
    return {
        "equal": not mismatches,
        "reference_count": len(reference),
        "observed_count": len(observed),
        "mismatch_count": len(mismatches),
        "first_mismatch": mismatches[0] if mismatches else None,
    }


def episodes_from_fills(fills):
    """Host reporting only: reconcile flat-to-flat campaigns from actual fills."""
    active, completed = {}, []
    for fill in fills:
        ticker, quantity = fill["ticker"], fill["quantity"]
        if ticker not in active:
            if quantity <= 0:
                raise RuntimeError("Fill ledger begins an episode with a short sale")
            active[ticker] = {
                "ticker": ticker,
                "opened_ms": fill["boundary_ms"],
                "quantity": 0.0,
                "bought": 0.0,
                "sold": 0.0,
                "buy_notional": 0.0,
                "sell_notional": 0.0,
                "fees": 0.0,
            }
        episode = active[ticker]
        episode["quantity"] += quantity
        episode["fees"] += fill["fee"]
        if quantity > 0:
            episode["bought"] += quantity
            episode["buy_notional"] += quantity * fill["price"]
        else:
            episode["sold"] -= quantity
            episode["sell_notional"] -= quantity * fill["price"]
        if episode["quantity"] < 0:
            raise RuntimeError("GPU fill ledger violates the no-short contract")
        if episode["quantity"] == 0:
            episode["closed_ms"] = fill["boundary_ms"]
            episode["gross_pnl"] = episode["sell_notional"] - episode["buy_notional"]
            episode["net_pnl"] = episode["gross_pnl"] - episode["fees"]
            completed.append(active.pop(ticker))
    return completed, list(active.values())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", default="51dcacfb-bc9e-44a0-a9c8-8e2d2f103156")
    parser.add_argument(
        "--runtime",
        type=Path,
        default=RUNTIME / "vectorized_backtest/strategy_one_audit",
    )
    parser.add_argument(
        "--cache",
        type=Path,
        help="Reuse a hash-verified tape after current source certification",
    )
    parser.add_argument(
        "--slots",
        type=int,
        help="Diagnostic prefix only; never reported as full comparison",
    )
    parser.add_argument(
        "--broker", type=int, choices=(100, 1000), nargs="+", default=[100, 1000]
    )
    parser.add_argument(
        "--profile-kernels",
        action="store_true",
        help="Trace one static GPU tick after each measured replay",
    )
    args = parser.parse_args(argv)
    if not RUNTIME.is_dir() or not args.runtime.resolve().is_relative_to(
        RUNTIME.resolve()
    ):
        raise RuntimeError("Required runtime root must own all generated artifacts")
    run = args.runtime / uuid4().hex
    run.mkdir(parents=True)
    started = perf_counter()
    progress = lambda value: print(value, flush=True)
    _load_private_credentials()
    with (
        closing(v3_client("read")) as m,
        closing(backtest_v4_operator_client_from_env()) as j,
    ):
        market, journal = SelectOnly(m), SelectOnly(j)
        fixed, release, saved, context, cursor, timings = load_saved_inputs(
            journal, market, args.run_id, progress=progress
        )
        terminal = load_v4_terminal_review_page(
            journal, args.run_id, after_sequence=0, limit=1
        )
        performance = load_v4_performance_report(journal, args.run_id)
        if (
            terminal["status"] != "completed"
            or performance["verified_sequence"] != terminal["verified_sequence"]
        ):
            raise RuntimeError(
                "Saved app baseline lacks agreeing completed financial/terminal heads"
            )
    definition = saved["definition"]
    if (
        context["strategy_revision"] != 1
        or context["configuration_hash"] != release.payload_hash
    ):
        raise RuntimeError(
            "Saved baseline differs from the certified Strategy 1 release"
        )
    if (
        definition["start_local_ms"] != 14_400_000
        or definition["simulation_profile"] != "baseline"
        or definition["activation_delay_us"] != 0
    ):
        raise RuntimeError(
            "This release runner requires the certified 04:00 baseline/no-delay contract"
        )
    through = definition["end_local_ms"] - definition["start_local_ms"]
    cache = args.cache.resolve() if args.cache else run / "tape"
    if args.cache:
        manifest = json.loads((cache / "manifest.json").read_text())
        for key, value in (
            ("market_token", fixed.market.token),
            ("seed_token", fixed.seeds.token),
            ("entry_token", fixed.entry.token),
            ("price_token", fixed.prices.token),
        ):
            if manifest[key] != value:
                raise RuntimeError(
                    "Reused tape differs from current certified saved inputs: " + key
                )
        if manifest["through_ms"] != through:
            raise RuntimeError("Reused tape differs from saved session bounds")
        timings["fetch_seconds"] = 0.0
        timings["cache_reused"] = True
    else:
        _, _, _, timings["fetch_seconds"] = load_replay_frames(
            fixed, through_ms=through, output=cache, progress=progress
        )
    import torch

    from .strategy_one_replay import StrategyOneReplay
    from .strategy_one_tape import prepare

    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is required for the requested GPU comparison")
    setup = perf_counter()
    tape = prepare(cache).to("cuda")
    torch.cuda.synchronize()
    timings["prepare_and_transfer_seconds"] = perf_counter() - setup
    session = fixed.market.sessions[0]
    if not hasattr(session, "isoformat"):
        from datetime import date

        session = date.fromisoformat(str(session))
    expected = reference_fills(
        performance, session, (args.slots * 100) if args.slots else through
    )
    summary = performance["report"]["summary"]
    report = {
        "version": "strategy-one-gpu-comparison-v1",
        "comparison_complete": False,
        "session_date": str(session),
        "through_ms": through,
        "strategy_ms": 100,
        "configuration_hash": release.payload_hash,
        "source_tokens": tape.manifest,
        "verified_sequence": performance["verified_sequence"],
        "timings": timings,
        "device": torch.cuda.get_device_name(),
        "resident_bytes": torch.cuda.memory_allocated(),
        "code_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO, text=True
        ).strip(),
        "source_hashes": {
            str(path.relative_to(REPO)): sha256(path.read_bytes()).hexdigest()
            for path in Path(__file__).parent.glob("*.py")
        },
        "app": {
            "run_id": args.run_id,
            "net_pnl": float(summary["net_pnl"]),
            "gross_realized": float(summary["gross_pnl"]),
            "fees": float(summary["total_fees"]),
            "closed_episodes": summary["episode_count"],
            "open_positions": [
                row
                for row in performance["position_lifecycles"]
                if row["status"] != "closed"
            ],
            "fill_count": performance["fill_count"],
            "timing_seconds": None,
            "timing_note": "Saved journal does not expose original execution timing; no fresh app run performed.",
        },
        "gpu": {},
    }
    write(
        run / "reference.json",
        {
            "definition": saved,
            "performance": performance,
            "terminal": terminal,
            "context": context,
            "cursor": cursor,
        },
    )
    write(run / "release.json", release.payload)
    progress("Artifact directory: " + str(run))
    for broker_ms in args.broker:
        stage = perf_counter()
        replay = StrategyOneReplay(
            tape, broker_ms=broker_ms, initial_cash=float(definition["initial_cash"])
        )
        replay.compile()
        progress(
            f"Compiled {broker_ms}ms broker in {replay.compile_seconds:.3f}s; capture {replay.capture_seconds:.3f}s"
        )
        result = replay.run(slots=args.slots, progress=progress)
        count = int(replay.fill_count[0].item())
        result["fills"] = [
            {
                "boundary_ms": int(at),
                "ticker": tape.tickers[int(listing)],
                "order_slot": int(slot),
                "quantity": qty,
                "price": price,
                "fee": fee,
            }
            for at, listing, slot, qty, price, fee in replay.ledger[0, 1 : count + 1]
            .cpu()
            .tolist()
        ]
        result["open_positions"] = [
            {"ticker": ticker, "quantity": qty}
            for ticker, qty in zip(tape.tickers, result["open_quantities"][0])
            if qty
        ]
        result["closed_episodes"], result["open_episodes"] = episodes_from_fills(
            result["fills"]
        )
        result["closed_episode_count"] = len(result["closed_episodes"])
        if (
            abs(
                sum(
                    row["fees"]
                    for row in (*result["closed_episodes"], *result["open_episodes"])
                )
                - result["fees"][0]
            )
            > 1e-7
        ):
            raise RuntimeError("Fill ledger fees differ from the GPU financial state")
        if (
            not result["open_positions"]
            and abs(
                sum(row["net_pnl"] for row in result["closed_episodes"])
                - result["net_pnl"][0]
            )
            > 1e-7
        ):
            raise RuntimeError("Closed fill-ledger episodes differ from GPU P&L")
        result["app_fill_comparison"] = fill_comparison(expected, result["fills"])
        result["setup_and_replay_seconds"] = perf_counter() - stage
        if args.profile_kernels:
            replay.reset()
            with (
                torch.inference_mode(),
                torch.profiler.profile(
                    activities=[
                        torch.profiler.ProfilerActivity.CPU,
                        torch.profiler.ProfilerActivity.CUDA,
                    ]
                ) as profile,
            ):
                replay.tick()
                torch.cuda.synchronize()
            profile.export_chrome_trace(str(run / f"kernels_{broker_ms}.json"))
            events = [
                event
                for event in profile.events()
                if event.device_type == torch.autograd.DeviceType.CUDA
            ]
            result["profile"] = {
                "strategy_ticks": 1,
                "kernel_count": len(events),
                "profiled_device_microseconds": sum(
                    event.self_device_time_total for event in events
                ),
                "excluded_from_replay_timing": True,
            }
        write(run / f"gpu_{broker_ms}.json", result)
        write(
            run / f"policy_{broker_ms}.json",
            {
                "entry": asdict(replay.entry_graph),
                "add": asdict(replay.add_graph),
                "actions": {
                    name: asdict(graph) for name, graph in replay.action_graphs.items()
                },
                "resistance": replay.resistance_policy.manifest(),
                "protection": replay.protection_policy.manifest(),
            },
        )
        report["gpu"][str(broker_ms)] = result
        write(run / "report.json", report)
        progress(
            json.dumps(
                {
                    key: result[key]
                    for key in (
                        "broker_ms",
                        "replay_seconds",
                        "net_pnl",
                        "fees",
                        "fill_count",
                        "open_positions",
                        "app_fill_comparison",
                    )
                },
                indent=2,
            )
        )
        del replay
        torch.cuda.empty_cache()
    report["comparison_complete"] = args.slots is None and set(args.broker) == {
        100,
        1000,
    }
    report["full_elapsed_seconds"] = perf_counter() - started
    report["faithful_100ms_parity"] = (
        report["gpu"].get("100", {}).get("app_fill_comparison", {}).get("equal", False)
    )
    report["comparison_status"] = (
        "diagnostic_prefix"
        if args.slots
        else "complete_and_parity_verified"
        if report["faithful_100ms_parity"] and report["comparison_complete"]
        else "executed_with_divergences"
    )
    write(run / "report.json", report)
    progress("Comparison artifact: " + str(run / "report.json"))
    return 0 if args.slots or report["faithful_100ms_parity"] else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    finally:
        MARKET_CERTIFICATE_KEEPER_POOL.close()
