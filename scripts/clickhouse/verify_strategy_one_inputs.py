"""Read-only certification and timing of Strategy 1's sparse ARTE inputs.

This is not a Backtest run, producer, or journal writer. It checks the full
tradable population's candidate seal, then only the selected candidate
tickers' pivot intervals and exact activation bars.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import date
import os
from pathlib import Path
import platform
import sys
from time import perf_counter

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True

from scripts.clickhouse.publish_strategy_one_candidates import (
    FULL_SESSION_BOUNDARY_MS, _certified_plan,
)
from src.backend.backtest_market_data import readonly_clickhouse_client
from src.backend.backtest_strategy_one_activation import load_strategy_one_activations
from src.backend.backtest_strategy_one_candidate_store import certify_candidate_plan
from src.backend.backtest_strategy_one_pivot_store import certify_pivot_plan
from src.backend.backtest_strategy_one_preparation import strategy_one_v7_tickers
from src.trading_runtime.strategy_one_candidate_schema import RULE_DIGEST


def verify(*, session_date: str, build_id: str) -> dict[str, int | float | str]:
    started = perf_counter()
    market = _certified_plan(session_date=session_date, build_id=build_id)
    source_seconds = perf_counter() - started
    with closing(readonly_clickhouse_client(
            market_stream=True, v3_read_principal=True)) as reader:
        started = perf_counter()
        candidates = certify_candidate_plan(
            market, candidate_rule_digest=RULE_DIGEST,
            through_boundary_ms=FULL_SESSION_BOUNDARY_MS, client=reader)
        candidate_seconds = perf_counter() - started
        selected = strategy_one_v7_tickers(candidates.prepared)
        if not selected:
            raise RuntimeError("Strategy 1 has no candidate ticker to certify")
        started = perf_counter()
        pivots = certify_pivot_plan(
            market, session_date=session_date,
            candidate_tickers=selected, client=reader)
        pivot_seconds = perf_counter() - started
        started = perf_counter()
        activations = load_strategy_one_activations(
            market, candidates, client=reader)
        activation_seconds = perf_counter() - started
    return {
        "population": len(market.tickers),
        "candidate_tickers": len(selected),
        "candidate_boundaries": sum(len(item.boundary_ms)
                                    for item in candidates.prepared),
        "pivot_intervals": sum(len(rows) for _, rows in pivots.intervals),
        "activations": len(activations.rows),
        "source_seconds": source_seconds,
        "candidate_seconds": candidate_seconds,
        "pivot_seconds": pivot_seconds,
        "activation_seconds": activation_seconds,
        "candidate_token": candidates.token,
        "pivot_token": pivots.token,
        "activation_token": activations.token,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session-date", default="2026-08-18")
    parser.add_argument("--build-id", default="")
    args = parser.parse_args(argv)
    try:
        day = date.fromisoformat(args.session_date).isoformat()
    except ValueError as exc:
        parser.error(str(exc))
    if platform.node().upper() != "DESKTOP-SAAI85T":
        print("Blocked: Strategy 1 input verification requires the managed "
              "workstation's read principal.", file=sys.stderr)
        return 1
    try:
        result = verify(session_date=day, build_id=args.build_id)
    except Exception as exc:
        # Driver exceptions can embed credentials or SQL; do not print them.
        print(f"Strategy 1 input verification failed: {type(exc).__name__}.",
              file=sys.stderr)
        return 1
    print(f"Strategy 1 inputs certified | {day} | "
          f"{result['population']} tradable tickers")
    print(f"Candidates {result['candidate_boundaries']} / "
          f"{result['candidate_tickers']} tickers | "
          f"pivot intervals {result['pivot_intervals']} | "
          f"activation episodes {result['activations']}")
    print(f"Market plan {result['source_seconds']:.3f}s | "
          f"candidate seal {result['candidate_seconds']:.3f}s | "
          f"pivot seal {result['pivot_seconds']:.3f}s | "
          f"activation bars {result['activation_seconds']:.3f}s")
    print(f"Candidate token {result['candidate_token']}")
    print(f"Pivot token {result['pivot_token']}")
    print(f"Activation token {result['activation_token']}")
    print("Read-only verification complete; no Backtest was run or data written.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
