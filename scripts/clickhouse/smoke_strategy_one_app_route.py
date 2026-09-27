"""Validate the actual Strategy 1 app launch against workstation ARTE data.

The default is SELECT-only preflight. --apply creates one normalized ClickHouse
Backtest journal through the public app route; it never creates market products
or run-local files. Generated evidence stays in ClickHouse, not this repository.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import date, datetime, time, timedelta
import os
from pathlib import Path
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True

from scripts.clickhouse.smoke_strategy_one_backtest import (  # noqa: E402
    _SqlCallProfile, _load_private_credentials, _print_completed_profile,
    _profile_sql_calls,
)


async def _run(day: date, ticker: str, minutes: int, cash: float, apply: bool) -> None:
    from src.backend.app import (  # noqa: PLC0415
        BacktestRunCreateRequest, HistoricalPreflightRequest,
        _trading_historical_preflight_payload, backtest_run_service,
        trading_backtest_run_create,
    )

    selected = (ticker,) if ticker else ()
    end = (datetime.combine(day, time(4)) + timedelta(minutes=minutes)).time()
    anchor = day + timedelta(days=1)
    began = perf_counter()
    preflight = await asyncio.to_thread(
        _trading_historical_preflight_payload,
        HistoricalPreflightRequest(
            mode="backtest", anchor_date=anchor, session_count=1,
            initial_cash=cash, start_time="04:00:00",
            end_time=end.isoformat(), tickers=list(selected),
        ),
    )
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
    with _profile_sql_calls(sql_profile):
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
    args = parser.parse_args()
    if (not 1 <= args.minutes <= 330 or not 1_000 <= args.cash <= 1_000_000_000
            or args.cash != args.cash or args.cash in (float("inf"), float("-inf"))
            or (args.ticker and (not args.ticker.isascii()
                                 or not args.ticker.isalnum()))):
        parser.error("require 1..330 minutes, 1,000..1,000,000,000 finite cash, "
                     "and an optional ASCII ticker")
    _load_private_credentials()
    asyncio.run(_run(args.session, args.ticker, args.minutes, args.cash, args.apply))


if __name__ == "__main__":
    main()
