"""Read-only, full-session Strategy 1 scan and columnar-preparation profile.

This diagnoses the inactive fixed Backtest path; it does not launch a run,
publish a signal, create a table, or certify journal/recovery readiness.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import date
import os
from pathlib import Path
import platform
import re
import sys
from time import perf_counter

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True

from scripts.clickhouse.provision_fixed_backtest_v3_principals import _secret_path
from src.backend.backtest_strategy_one_preparation import (
    prepare_strategy_one_session, strategy_one_v7_tickers,
)
from src.backend.backtest_v3_clients import v3_client
from src.backend.fixed_bar_signal import load_first_squeeze_occurrences
from src.trading_runtime.arte_market_day_cold_preflight import cold_certified_market_day_plan
from src.trading_runtime.arte_market_day_keeper import MarketDayKeeperReader
from src.trading_runtime.keeper_session import open_workstation_keeper_session


def _rules() -> tuple[dict, dict]:
    stream = {
        "signal_stream_id": "price-squeeze-early",
        "occurrence_source": "qmd_squeeze_episode", "episode_role": "start",
        "episode_ttl_ms": 300_000,
        "inclusion_rule_sets": ["watchlist-squeeze-early-impulse-100ms"],
        "trigger_policy": "false_to_true", "rearm_policy": "after_false",
        "cooldown_ms": 0,
    }
    conditions = (
        ("price_change_1_bar_pct", "greater_or_equal", 0.05),
        ("trade_count_change", "greater_than", 0),
        ("volume_change", "greater_than", 0),
    )
    activation = {"rule_sets": [{
        "rule_set_id": "watchlist-squeeze-early-impulse-100ms", "operator": "all",
        "conditions": [{
            "left_source_id": source, "comparator": comparator, "value": value,
            "right_source_id": "", "enabled": True,
            "left_interval": {"value": 100, "unit": "milliseconds"},
        } for source, comparator, value in conditions],
    }]}
    return stream, activation


def profile(build_id: str, day: date, tickers: tuple[str, ...], *,
            through_boundary_ms: int, max_workers: int) -> tuple[int, int, int, int,
                                                                  float, float, float]:
    if platform.node().upper() != "DESKTOP-SAAI85T":
        raise RuntimeError("Strategy 1 profile requires the workstation read principal")
    credential = _secret_path("read")
    if not credential.is_file():
        raise RuntimeError("Private V3 reader credential is unavailable")
    environment = {"BACKTEST_V3_READ_CREDENTIAL_FILE": str(credential)}
    def client():
        return v3_client("read", environment=environment, market_stream=True)
    with closing(client()) as reader, closing(open_workstation_keeper_session()) as keeper:
        started = perf_counter()
        plan = cold_certified_market_day_plan(
            reader, MarketDayKeeperReader(keeper.client), build_id,
            sessions=(day.isoformat(),), tickers=tickers,
            configuration={"strategy": {"strategy_number": 1,
                                          "execution_interval": "100ms"}})
        preflight_seconds = perf_counter() - started
        stream, activation = _rules()
        started = perf_counter()
        scan = load_first_squeeze_occurrences(
            plan, stream=stream, activation=activation,
            through_boundary_ms=through_boundary_ms, client=reader)
        scan_seconds = perf_counter() - started
        started = perf_counter()
        prepared = prepare_strategy_one_session(
            plan, session_date=day.isoformat(),
            through_boundary_ms=through_boundary_ms,
            stream=stream, activation=activation, scan_client=reader,
            client_factory=client, max_workers=max_workers, certified_scan=scan)
        preparation_seconds = perf_counter() - started
    return (len(plan.tickers), len(scan["occurrences"]), len(prepared),
            len(strategy_one_v7_tickers(prepared)), preflight_seconds,
            scan_seconds, preparation_seconds)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-id", required=True)
    parser.add_argument("--date", required=True, type=date.fromisoformat)
    parser.add_argument("--tickers", default="", help="Comma-separated subset; default all attested tickers")
    parser.add_argument("--through-boundary-ms", type=int, default=57_600_000,
                        help="Completed boundary after 04:00 New York; default full 16-hour session")
    parser.add_argument("--max-workers", type=int, default=4)
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[0-9a-f]{64}(?:-[0-9a-f]{12})?", args.build_id):
        parser.error("Invalid market-day build ID")
    tickers = tuple(sorted({ticker.strip().upper() for ticker in
                            args.tickers.split(",") if ticker.strip()}))
    if any(not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,15}", ticker)
           for ticker in tickers):
        parser.error("Invalid ticker")
    if (not 0 < args.through_boundary_ms <= 57_600_000
            or args.through_boundary_ms % 100
            or not 1 <= args.max_workers <= 16):
        parser.error("Boundary must be a completed 100ms session clock; workers 1-16")
    try:
        tickers_count, episodes, loaded, candidates, preflight, scan, preparation = profile(
            args.build_id, args.date, tickers,
            through_boundary_ms=args.through_boundary_ms,
            max_workers=args.max_workers)
    except Exception as exc:
        print(f"Strategy 1 profile failed: {exc}", file=sys.stderr)
        return 1
    print(f"Strategy 1 read-only profile | {args.date} | {tickers_count} tickers | "
          f"through {args.through_boundary_ms} ms")
    print(f"Episodes {episodes} | loaded tickers {loaded} | V7 candidate tickers {candidates}")
    print(f"Preflight {preflight:.3f}s | squeeze scan {scan:.3f}s | "
          f"columnar preparation {preparation:.3f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
