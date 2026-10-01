"""Read-only, reproducible app-reference audit for the faithful Torch port.

This launcher DOES NOT run Strategy 1 through the approximate ReplayRunner.
It verifies the saved app run and writes an explicit incomplete comparison.
Market and journal operations are SELECT-only; outputs stay in runtime roots.
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from time import perf_counter
from uuid import uuid4

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True

from scripts.clickhouse.report_strategy_one_trades import SelectOnly
from scripts.clickhouse.smoke_strategy_one_backtest import _load_private_credentials
from src.backend.backtest_market_keeper_pool import MARKET_CERTIFICATE_KEEPER_POOL
from src.backend.backtest_strategy_one_configuration import (
    certify_numbered_configuration,
)
from src.backend.backtest_v3_clients import v3_client
from src.backend.backtest_v4_chart import certified_saved_run_plan
from src.backend.backtest_v4_saved_review import (
    load_v4_performance_report,
    load_v4_terminal_review_page,
)
from src.trading_runtime.arte_backtest_definition import load_backtest_definition
from src.trading_runtime.arte_journal_writer import backtest_v4_operator_client_from_env

from .strategy_one import STRATEGY_ONE_ARRAY

REPO = Path(__file__).resolve().parents[4]
RUNTIME = Path("D:/TradingML/runtimes")

GAPS = (
    {
        "contract": "certified_strategy_one_input_adapter",
        "installed": False,
        "reason": "Existing tape uses Early Squeeze Release 11/V6 ranked slots; Strategy 1 needs its certified candidate, activation, BOS/HOD/pivot and stable-ID V7 products.",
    },
    {
        "contract": "post_broker_position_lifecycle_and_pending_witnesses",
        "installed": False,
        "reason": "Filled-entry ownership, first-held boundary, prior-position highs, same-boundary retirement and pending break retry are not wired into ReplayRunner.",
    },
    {
        "contract": "portfolio_and_oms",
        "installed": False,
        "reason": "App uses one-third mandate capital, three-position limits, sequential reservations and acknowledged bracket amendments; ReplayRunner uses cash fractions and proportional buys.",
    },
    {
        "contract": "broker_and_fees",
        "installed": False,
        "reason": "App uses displayed/price-specific liquidity, persistent adaptive limits, OCA brackets, delayed stop fills and cumulative per-order commissions; ReplayRunner uses interval VWAP, close-triggered exits and bps fees.",
    },
)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", default="51dcacfb-bc9e-44a0-a9c8-8e2d2f103156")
    parser.add_argument(
        "--runtime",
        type=Path,
        default=RUNTIME / "vectorized_backtest" / "strategy_one_audit",
    )
    args = parser.parse_args(argv)
    runtime = args.runtime.resolve()
    if not RUNTIME.is_dir() or not runtime.is_relative_to(RUNTIME.resolve()):
        raise RuntimeError(
            "Required D:/TradingML/runtimes root must own the audit output"
        )
    runtime.mkdir(parents=True, exist_ok=True)
    run = runtime / uuid4().hex
    run.mkdir()
    print("Read-only Strategy 1 audit: " + args.run_id, flush=True)
    started = perf_counter()
    _load_private_credentials()
    with (
        closing(v3_client("read")) as raw_market,
        closing(backtest_v4_operator_client_from_env()) as raw_journal,
    ):
        market, journal = SelectOnly(raw_market), SelectOnly(raw_journal)
        release = certify_numbered_configuration(market, 1)
        terminal = load_v4_terminal_review_page(
            journal, args.run_id, after_sequence=0, limit=1
        )
        if terminal["status"] != "completed":
            raise RuntimeError(
                "Strategy 1 comparison requires a completed app baseline"
            )
        session, context, cursor, plan = certified_saved_run_plan(
            journal, market, run_id=args.run_id
        )
        if (
            context["strategy_revision"] != 1
            or context["configuration_hash"] != release.payload_hash
        ):
            raise RuntimeError("App baseline is not the certified Strategy 1 release")
        definition = load_backtest_definition(journal, args.run_id, run_context=context)
        performance = load_v4_performance_report(journal, args.run_id)
        if performance["verified_sequence"] != terminal["verified_sequence"]:
            raise RuntimeError("Reference financial and terminal heads differ")
    source_files = (
        "src/trading_runtime/strategy_one_contract.py",
        "src/trading_runtime/strategy_one_stateful.py",
        "src/trading_runtime/strategy_one_position.py",
        "src/trading_runtime/strategy_one_resistance.py",
        "src/trading_runtime/strategy_one_add.py",
        "src/trading_runtime/strategy_one_intent.py",
        "src/trading_runtime/simulated_broker.py",
        "research/vectorized_backtest/v1/torch_backtest/strategy_one.py",
        "research/vectorized_backtest/v1/torch_backtest/replay.py",
        "research/vectorized_backtest/v1/torch_backtest/audit_strategy_one.py",
    )
    row = definition["definition"]
    summary = performance["report"]["summary"]
    open_positions = [
        value
        for value in performance["position_lifecycles"]
        if value["status"] != "closed"
    ]
    report = {
        "version": "strategy-one-torch-port-audit-v1",
        "comparison_complete": False,
        "comparison_status": "policy_blocks_only_full_runner_not_integrated",
        "code_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO, text=True
        ).strip(),
        "source_hashes": {
            name: hashlib.sha256((REPO / name).read_bytes()).hexdigest()
            for name in source_files
        },
        "session": {
            "date": session.isoformat(),
            "timezone": "America/New_York",
            "start_local_ms": row["start_local_ms"],
            "end_local_ms": row["end_local_ms"],
            "strategy_ms": 100,
            "initial_cash": row["initial_cash"],
        },
        "baseline": {
            "run_id": args.run_id,
            "configuration_hash": release.payload_hash,
            "market_plan_token": plan.token,
            "cursor": cursor,
            "status": terminal["status"],
            "verified_sequence": performance["verified_sequence"],
            "net_pnl": summary["net_pnl"],
            "fees": summary["total_fees"],
            "episode_count": summary["episode_count"],
            "open_position_count": len(open_positions),
            "open_positions": open_positions,
            "original_execution_seconds": None,
            "timing_note": "Audit read time is not original backtest execution time.",
        },
        "policy_block_array": STRATEGY_ONE_ARRAY,
        "array_note": "Typed stateful block prototype, not the complete executable/searchable Strategy 1 program.",
        "gaps": GAPS,
        "gpu_1s_broker": {
            "executed": False,
            "net_pnl": None,
            "open_positions": None,
            "seconds": None,
        },
        "gpu_100ms_broker": {
            "executed": False,
            "net_pnl": None,
            "open_positions": None,
            "seconds": None,
        },
        "audit_read_seconds": perf_counter() - started,
    }
    for name, payload in (
        ("report", report),
        ("release", release.payload),
        ("reference_performance", performance),
        ("reference_definition", definition),
    ):
        (run / f"{name}.json").write_text(
            json.dumps(payload, indent=2, default=str), encoding="utf-8"
        )
    print(json.dumps(report["baseline"], indent=2, default=str), flush=True)
    print("Comparison incomplete: " + str(run / "report.json"), flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    finally:
        MARKET_CERTIFICATE_KEEPER_POOL.close()
