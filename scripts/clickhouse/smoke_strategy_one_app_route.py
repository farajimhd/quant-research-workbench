"""Validate the actual Strategy 1 app launch against workstation ARTE data.

The default is SELECT-only preflight. --apply creates one normalized ClickHouse
Backtest journal through the public app route; it never creates market products
or run-local files. Generated evidence stays in ClickHouse, not this repository.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from contextlib import contextmanager
import cProfile
from datetime import date, datetime, time, timedelta
from io import StringIO
import os
from pathlib import Path
import pstats
import sys
from threading import Lock, get_ident
from time import perf_counter

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True

from scripts.clickhouse.smoke_strategy_one_backtest import (  # noqa: E402
    _SqlCallProfile, _load_private_credentials, _print_completed_profile,
    _profile_sql_calls,
)


@contextmanager
def _profile_v7_updates(enabled: bool):
    """Profile only completed-second engine CPU, keeping output off disk."""
    if not enabled:
        yield
        return
    from src.backend.fixed_v7_stream import FixedV7Stream
    from src.market_engine import reaction_band

    original = FixedV7Stream.update_second
    original_fit = reaction_band.fit
    profiles: dict[int, cProfile.Profile] = {}
    fit_shapes: Counter[tuple[str, int]] = Counter()
    lock = Lock()

    def wrapped(self, row, *, at):
        identity = get_ident()
        with lock:
            profile = profiles.setdefault(identity, cProfile.Profile())
        return profile.runcall(original, self, row, at=at)

    def counted_fit(prices, resolution):
        result = original_fit(prices, resolution)
        distinct = len(set(prices))
        shape = ("one_price" if distinct == 1 else
                 "two_prices" if distinct == 2 else "three_plus_prices")
        with lock:
            fit_shapes[(shape, min(len(prices), 20))] += 1
        return result

    FixedV7Stream.update_second = wrapped
    reaction_band.fit = counted_fit
    try:
        yield
    finally:
        FixedV7Stream.update_second = original
        reaction_band.fit = original_fit
        print("V7 fit observation shapes (length 20 means 20+): "
              + ", ".join(f"{shape}/{length}={count}"
                          for (shape, length), count in sorted(fit_shapes.items())),
              flush=True)
        if profiles:
            report = StringIO()
            stats = pstats.Stats(*profiles.values(), stream=report)
            stats.sort_stats("cumulative").print_stats(30)
            stats.sort_stats("tottime").print_stats(
                "streaming_level_book|reaction_band", 30)
            print("V7 completed-second engine profile:", flush=True)
            print(report.getvalue(), flush=True)


async def _run(day: date, ticker: str, minutes: int, cash: float, apply: bool,
               profile_v7: bool = False, profile_preflight: bool = False) -> None:
    from src.backend.app import (  # noqa: PLC0415
        BacktestRunCreateRequest, HistoricalPreflightRequest,
        _trading_historical_preflight_payload, backtest_run_service,
        trading_backtest_run_create,
    )

    selected = (ticker,) if ticker else ()
    end = (datetime.combine(day, time(4)) + timedelta(minutes=minutes)).time()
    anchor = day + timedelta(days=1)
    preflight_request = HistoricalPreflightRequest(
        mode="backtest", anchor_date=anchor, session_count=1,
        initial_cash=cash, start_time="04:00:00",
        end_time=end.isoformat(), tickers=list(selected),
    )
    def load_preflight():
        if not profile_preflight:
            return _trading_historical_preflight_payload(preflight_request)
        profile = cProfile.Profile()
        result = profile.runcall(_trading_historical_preflight_payload, preflight_request)
        report = StringIO()
        pstats.Stats(profile, stream=report).sort_stats("cumulative").print_stats(35)
        print("App preflight CPU profile:\n" + report.getvalue(), flush=True)
        return result
    began = perf_counter()
    preflight = await asyncio.to_thread(load_preflight)
    blocked = tuple(row["id"] for row in preflight["checks"]
                    if row.get("required", True) and row["status"] != "ready")
    print(f"App preflight: session={day} scope={ticker or 'full-market'} "
          f"cash={cash:g} interval={preflight['execution_interval']} "
          f"wall_s={perf_counter()-began:.3f} blocked={blocked}", flush=True)
    if (preflight["window"]["sessions"] != [day.isoformat()]
            or preflight["initial_cash"] != cash
            or preflight["execution_interval"] != "100ms"
            or not preflight["strategy_run_ready"] or blocked):
        raise RuntimeError("App preflight did not certify the requested Strategy 1 run")
    if not apply:
        print("Plan only: no Backtest journal or run was created", flush=True)
        return

    request = BacktestRunCreateRequest(
        anchor_date=anchor, session_count=1, initial_cash=cash,
        configuration_revision_id=preflight["configuration_revision_id"],
        run_plan_id=preflight["run_plan_id"], start_time="04:00:00",
        end_time=end.isoformat(), tickers=list(selected),
        experimental_structure_book="level-book-v7",
    )
    sql_profile = _SqlCallProfile()
    with _profile_v7_updates(profile_v7), _profile_sql_calls(sql_profile):
        began = perf_counter()
        response = await trading_backtest_run_create(request)
        launch_s = perf_counter() - began
        controller = backtest_run_service.get(response["run_id"])
        print(f"App launch: run_id={controller.run_id} wall_s={launch_s:.3f} "
              f"status={controller.status}", flush=True)
        if controller._task is None:
            raise RuntimeError("App route did not schedule Backtest execution")
        began = perf_counter()
        await controller._task
        execution_s = perf_counter() - began
    print(f"App result: run_id={controller.run_id} status={controller.status} "
          f"execution_s={execution_s:.3f} processed_rows={controller.processed_events} "
          f"error={controller.error[:300]}", flush=True)
    if controller.status != "completed" or controller.run_dir.exists():
        raise RuntimeError("App Backtest failed or created a run-local directory")
    _print_completed_profile(controller)
    sql_profile.print_summary()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", type=date.fromisoformat,
                        default=date(2026, 8, 18))
    parser.add_argument("--ticker", default="",
                        help="optional single-symbol scope; omit for all tradable tickers")
    parser.add_argument("--minutes", type=int, default=10,
                        help="whole minutes after 04:00 ET; maximum 330")
    parser.add_argument("--cash", type=float, default=10_000.0,
                        help="initial simulated cash; default matches the app")
    parser.add_argument("--apply", action="store_true",
                        help="create and await one normalized ClickHouse Backtest run")
    parser.add_argument("--profile-v7", action="store_true",
                        help="profile completed-second V7 engine calls in memory")
    parser.add_argument("--profile-preflight", action="store_true",
                        help="profile the read-only app preflight in memory")
    args = parser.parse_args()
    if (not 1 <= args.minutes <= 330 or not 1_000 <= args.cash <= 1_000_000_000
            or args.cash != args.cash or args.cash in (float("inf"), float("-inf"))
            or (args.ticker and (not args.ticker.isascii()
                                 or not args.ticker.isalnum()))):
        parser.error("require 1..330 minutes, 1,000..1,000,000,000 finite cash, "
                     "and an optional ASCII ticker")
    _load_private_credentials()
    asyncio.run(_run(args.session, args.ticker, args.minutes, args.cash,
                     args.apply, args.profile_v7, args.profile_preflight))


if __name__ == "__main__":
    main()
