"""Read-only certification and timing of Strategy 1's sparse ARTE inputs.

This is not a Backtest run, producer, or journal writer. It checks the full
tradable population's candidate seal, then only the selected candidate
tickers' pivot intervals, HOD context, V7 seeds, activation bars, exact entry
evidence, and executable price levels. It never runs or writes a Backtest.
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
from src.backend.backtest_market_data import (
    project_market_day_plan, readonly_clickhouse_client,
)
from src.backend.backtest_liquidity_price import certify_price_level_plan
from src.backend.backtest_strategy_one_activation import (
    load_strategy_one_activations, project_activation_plan,
)
from src.backend.backtest_strategy_one_candidate_store import (
    certify_candidate_plan, project_candidate_plan,
)
from src.backend.backtest_strategy_one_entry_store import certify_entry_evidence_plan
from src.backend.backtest_strategy_one_hod_store import certify_hod_plan
from src.backend.backtest_strategy_one_pivot_store import certify_pivot_plan
from src.backend.backtest_strategy_one_preparation import strategy_one_v7_tickers
from src.backend.structural_v7_seed import certified_seed_plan
from src.trading_runtime.strategy_one_candidate_schema import RULE_DIGEST


def verify(*, session_date: str, build_id: str,
           through_boundary_ms: int = FULL_SESSION_BOUNDARY_MS,
           ) -> dict[str, int | float | str]:
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
        projected = project_market_day_plan(market, selected)
        started = perf_counter()
        prices = certify_price_level_plan(projected, reader)
        price_seconds = perf_counter() - started
        started = perf_counter()
        seeds = certified_seed_plan(projected, reader)
        seed_seconds = perf_counter() - started
        started = perf_counter()
        hod = certify_hod_plan(market, candidates, seeds, client=reader)
        hod_seconds = perf_counter() - started
        started = perf_counter()
        entry = certify_entry_evidence_plan(
            market, candidates, activations, pivots, hod, seeds,
            client=reader)
        entry_seconds = perf_counter() - started
        started = perf_counter()
        visible_candidates = project_candidate_plan(
            candidates, through_boundary_ms=through_boundary_ms)
        visible_activations = project_activation_plan(
            activations, candidates, through_boundary_ms=through_boundary_ms)
        projection_seconds = perf_counter() - started
    return {
        "population": len(market.tickers),
        "candidate_tickers": len(selected),
        "candidate_boundaries": sum(len(item.boundary_ms)
                                    for item in candidates.prepared),
        "pivot_intervals": sum(len(rows) for _, rows in pivots.intervals),
        "activations": len(activations.rows),
        "price_units": len(prices.units),
        "seed_units": len(seeds.units),
        "hod_tickers": len(hod.contexts),
        "entry_tickers": len(entry.coverage),
        "entry_candidates": len(entry.candidates),
        "through_boundary_ms": through_boundary_ms,
        "visible_candidate_tickers": len(visible_candidates.prepared),
        "visible_candidate_boundaries": sum(len(row.boundary_ms)
                                            for row in visible_candidates.prepared),
        "visible_activations": len(visible_activations.rows),
        "projection_seconds": projection_seconds,
        "source_seconds": source_seconds,
        "candidate_seconds": candidate_seconds,
        "pivot_seconds": pivot_seconds,
        "activation_seconds": activation_seconds,
        "price_seconds": price_seconds,
        "seed_seconds": seed_seconds,
        "hod_seconds": hod_seconds,
        "entry_seconds": entry_seconds,
        "candidate_token": candidates.token,
        "pivot_token": pivots.token,
        "activation_token": activations.token,
        "entry_token": entry.token,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session-date", default="2026-08-18")
    parser.add_argument("--build-id", default="")
    parser.add_argument("--through-boundary-ms", type=int,
                        default=FULL_SESSION_BOUNDARY_MS,
                        help="completed cutoff after 04:00 New York; default 20:00")
    args = parser.parse_args(argv)
    try:
        day = date.fromisoformat(args.session_date).isoformat()
        if (not 0 < args.through_boundary_ms <= FULL_SESSION_BOUNDARY_MS
                or args.through_boundary_ms % 100):
            raise ValueError("Cutoff must be a positive 100ms boundary through 20:00 NY")
    except ValueError as exc:
        parser.error(str(exc))
    if platform.node().upper() != "DESKTOP-SAAI85T":
        print("Blocked: Strategy 1 input verification requires the managed "
              "workstation's read principal.", file=sys.stderr)
        return 1
    try:
        result = verify(session_date=day, build_id=args.build_id,
                        through_boundary_ms=args.through_boundary_ms)
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
    print(f"Price {result['price_seconds']:.3f}s / {result['price_units']} units | "
          f"V7 seeds {result['seed_seconds']:.3f}s / {result['seed_units']} units | "
          f"HOD {result['hod_seconds']:.3f}s / {result['hod_tickers']} tickers | "
          f"entry seal {result['entry_seconds']:.3f}s / "
          f"{result['entry_candidates']} candidates")
    local_minutes = (4 * 3_600_000 + result['through_boundary_ms']) // 60_000
    print(f"Run projection through {local_minutes // 60:02}:{local_minutes % 60:02} NY: "
          f"{result['visible_candidate_boundaries']} candidate boundaries / "
          f"{result['visible_candidate_tickers']} tickers, "
          f"{result['visible_activations']} activations "
          f"in {result['projection_seconds']:.3f}s; no market reread")
    print(f"Candidate token {result['candidate_token']}")
    print(f"Pivot token {result['pivot_token']}")
    print(f"Activation token {result['activation_token']}")
    print(f"Entry token {result['entry_token']}")
    print("Read-only verification complete; no Backtest was run or data written.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
