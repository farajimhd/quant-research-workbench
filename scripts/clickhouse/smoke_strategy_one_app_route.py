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


async def _await_run_with_progress(controller, *, interval_s: float = 30,
                                   max_execution_s: float | None = None) -> None:
    """Observation polls do not cancel; an explicit execution deadline requests stop."""
    began = perf_counter()
    task = controller._task
    while not task.done():
        remaining = (max_execution_s - (perf_counter() - began)
                     if max_execution_s is not None else None)
        if remaining is not None and remaining <= 0:
            print(f"App timeout: run_id={controller.run_id} "
                  f"limit_s={max_execution_s:g} status=incomplete "
                  "action=graceful_stop financial_result_valid=false", flush=True)
            await controller.command("stop")
            # Drain the original controller, including its journal writer and lease.
            # Cancelling a to_thread await cannot stop its underlying worker safely.
            await task
            raise TimeoutError(f"Backtest {controller.run_id} exceeded "
                               f"{max_execution_s:g}s; incomplete financial result")
        done, _ = await asyncio.wait({task}, timeout=(min(interval_s, remaining)
                                     if remaining is not None else interval_s))
        if not done:
            current = getattr(controller, "current_time", None)
            requested = getattr(getattr(controller, "definition", None),
                                "requested_start", None)
            simulated = ((current - requested).total_seconds()
                         if current is not None and requested is not None else None)
            print(f"App progress: run_id={controller.run_id} status={controller.status} "
                  f"elapsed_s={perf_counter() - began:.1f} "
                  f"processed_broker_rows={controller.processed_events} "
                  f"market_time={current.isoformat() if current is not None else 'unavailable'} "
                  f"simulation_elapsed_s={simulated if simulated is not None else 'unavailable'}",
                  flush=True)
            stages = getattr(controller, "_stage_timings", {})
            for name, row in sorted(stages.items(),
                                    key=lambda item: (-item[1]['seconds'], item[0]))[:3]:
                print(f"App completed-stage timing: {name} calls={row['calls']} "
                      f"wall_s={row['seconds']:.3f} "
                      f"max_call_s={row['maximum_seconds']:.3f}", flush=True)
    await task


def _print_journal_writer_profile(controller: object) -> None:
    """Report bounded worker timings without putting persistence on the engine thread."""
    metrics = getattr(controller, "_journal_writer_final_metrics", None)
    if not isinstance(metrics, dict):
        return
    print(
        "Journal writer: "
        f"units={metrics.get('committed_units', 0)} "
        f"rows={metrics.get('committed_event_rows', 0)} "
        f"failed={metrics.get('failed_units', 0)} "
        f"publish_s={int(metrics.get('publish_ns_total', 0)) / 1e9:.3f} "
        f"max_unit_s={int(metrics.get('publish_ns_max', 0)) / 1e9:.3f}",
        flush=True,
    )
    by_unit = metrics.get("publish_by_unit", {})
    if isinstance(by_unit, dict):
        for name, row in sorted(
            by_unit.items(),
            key=lambda item: int(item[1].get("publish_ns_total", 0)),
            reverse=True,
        )[:20]:
            print(
                f"Journal unit {name}: units={int(row.get('units', 0))} "
                f"publish_s={int(row.get('publish_ns_total', 0)) / 1e9:.3f} "
                f"max_s={int(row.get('publish_ns_max', 0)) / 1e9:.3f}",
                flush=True,
            )
    stages = metrics.get("compound_publish_stages_ns", {})
    if isinstance(stages, dict):
        for name, duration in sorted(stages.items()):
            print(f"Journal compound {name}: worker_s={int(duration) / 1e9:.3f}",
                  flush=True)


@contextmanager
def _profile_v7_updates(enabled: bool):
    """Profile only completed-second engine CPU, keeping output off disk."""
    if not enabled:
        yield
        return
    from src.backend.fixed_v7_stream import FixedV7Cache, FixedV7Stream
    from src.backend.fixed_v7_interval_cache import FixedV7IntervalCache
    from src.market_engine import reaction_band

    original = FixedV7Stream.update_second
    original_stream = FixedV7Cache._stream
    original_interval_load = FixedV7IntervalCache._load_to
    original_interval_levels = FixedV7IntervalCache.strategy_one_levels
    original_interval_advance = FixedV7IntervalCache.advance_seconds
    original_fit = reaction_band.fit
    profiles: dict[int, cProfile.Profile] = {}
    fit_shapes: Counter[tuple[str, int]] = Counter()
    stream_calls: Counter[str] = Counter()
    interval_calls: Counter[str] = Counter()
    interval_seconds: Counter[str] = Counter()
    lock = Lock()

    def wrapped(self, row, **clock):
        identity = get_ident()
        with lock:
            profile = profiles.setdefault(identity, cProfile.Profile())
        return profile.runcall(original, self, row, **clock)

    def counted_fit(prices, resolution):
        result = original_fit(prices, resolution)
        distinct = len(set(prices))
        shape = ("one_price" if distinct == 1 else
                 "two_prices" if distinct == 2 else "three_plus_prices")
        with lock:
            fit_shapes[(shape, min(len(prices), 20))] += 1
        return result

    def counted_stream(self, ticker, *, as_of):
        with lock:
            stream_calls[ticker] += 1
        return original_stream(self, ticker, as_of=as_of)

    def timed_interval(label, original_method):
        def call(self, *args, **kwargs):
            started = perf_counter()
            try:
                return original_method(self, *args, **kwargs)
            finally:
                elapsed = perf_counter() - started
                with lock:
                    interval_calls[label] += 1
                    interval_seconds[label] += elapsed
        return call

    FixedV7Stream.update_second = wrapped
    FixedV7Cache._stream = counted_stream
    FixedV7IntervalCache._load_to = timed_interval("load_to", original_interval_load)
    FixedV7IntervalCache.strategy_one_levels = timed_interval(
        "levels", original_interval_levels)
    FixedV7IntervalCache.advance_seconds = timed_interval(
        "advance_seconds", original_interval_advance)
    reaction_band.fit = counted_fit
    try:
        yield
    finally:
        FixedV7Stream.update_second = original
        FixedV7Cache._stream = original_stream
        FixedV7IntervalCache._load_to = original_interval_load
        FixedV7IntervalCache.strategy_one_levels = original_interval_levels
        FixedV7IntervalCache.advance_seconds = original_interval_advance
        reaction_band.fit = original_fit
        print(f"V7 cache stream calls={sum(stream_calls.values())} "
              f"tickers={len(stream_calls)} update_threads={len(profiles)}", flush=True)
        for label in ("load_to", "levels", "advance_seconds"):
            print(f"V7 interval {label}: calls={interval_calls[label]} "
                  f"wall_s={interval_seconds[label]:.3f}", flush=True)
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


@contextmanager
def _profile_v7_seeds(enabled: bool):
    """Time independent seed phases across worker lanes; retain no SQL or rows."""
    if not enabled:
        yield
        return
    from src.backend import fixed_v7_stream

    original_seed = fixed_v7_stream.load_seeds_batch
    original_splits = fixed_v7_stream.split_evidence_batch
    original_book = fixed_v7_stream.FixedV7Stream.__init__
    lock = Lock()
    totals: dict[str, tuple[int, float, float]] = {}

    def timed(label, function):
        def call(*args, **kwargs):
            started = perf_counter()
            try:
                return function(*args, **kwargs)
            finally:
                elapsed = perf_counter() - started
                with lock:
                    count, seconds, slowest = totals.get(label, (0, 0.0, 0.0))
                    totals[label] = (count + 1, seconds + elapsed,
                                     max(slowest, elapsed))
        return call

    fixed_v7_stream.load_seeds_batch = timed("seed_select_decode", original_seed)
    fixed_v7_stream.split_evidence_batch = timed("split_select", original_splits)
    fixed_v7_stream.FixedV7Stream.__init__ = timed("book_construct", original_book)
    try:
        yield
    finally:
        fixed_v7_stream.load_seeds_batch = original_seed
        fixed_v7_stream.split_evidence_batch = original_splits
        fixed_v7_stream.FixedV7Stream.__init__ = original_book
        for label, (count, seconds, slowest) in sorted(totals.items()):
            print(f"V7 seed {label}: calls={count} worker_s={seconds:.3f} "
                  f"max_call_s={slowest:.3f}", flush=True)


@contextmanager
def _profile_entry_path(enabled: bool):
    """Measure shared admission lanes without changing production control flow."""
    if not enabled:
        yield
        return
    from src.trading_runtime.runtime import TradingRuntime
    from src.trading_runtime.portfolio import PortfolioManagementEngine
    from src.trading_runtime.order_management import OrderManagementEngine
    from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter
    from src.backend.backtest_journal_memory import BacktestMemoryJournal

    targets = (
        (TradingRuntime, "_refresh_portfolio_from_broker", "portfolio_refresh"),
        (TradingRuntime, "_synchronize_portfolio_broker", "broker_snapshot_sync"),
        (PortfolioManagementEngine, "synchronize", "portfolio_sync"),
        (PortfolioManagementEngine, "_persist_state", "portfolio_state_append"),
        (PortfolioManagementEngine, "approve", "portfolio_approve"),
        (OrderManagementEngine, "submit_intent", "oms_submit"),
        (OrderManagementEngine, "expire_entry_deadlines", "oms_deadline_expiry"),
        (OrderManagementEngine, "advance_adaptive_execution", "oms_adaptive_advance"),
        (BacktestMemoryJournal, "append_strategy_one_intent", "intent_append"),
        (SimulatedBrokerAdapter, "live_orders", "broker_live_orders"),
        (SimulatedBrokerAdapter, "account_summary", "broker_account_summary"),
        (SimulatedBrokerAdapter, "account_ledger", "broker_account_ledger"),
        (SimulatedBrokerAdapter, "positions", "broker_positions"),
        (SimulatedBrokerAdapter, "_on_validated_liquidity_bar", "broker_bar_match"),
    )
    original = [(owner, name, getattr(owner, name)) for owner, name, _ in targets]
    counts: Counter[str] = Counter()
    seconds: Counter[str] = Counter()

    def timed(label, function):
        if asyncio.iscoroutinefunction(function):
            async def call(*args, **kwargs):
                started = perf_counter()
                try:
                    return await function(*args, **kwargs)
                finally:
                    counts[label] += 1
                    seconds[label] += perf_counter() - started
        else:
            def call(*args, **kwargs):
                started = perf_counter()
                try:
                    return function(*args, **kwargs)
                finally:
                    counts[label] += 1
                    seconds[label] += perf_counter() - started
        return call

    for owner, name, label in targets:
        setattr(owner, name, timed(label, getattr(owner, name)))
    try:
        yield
    finally:
        for owner, name, function in original:
            setattr(owner, name, function)
        for label in (row[2] for row in targets):
            print(f"Entry path {label}: calls={counts[label]} "
                  f"wall_s={seconds[label]:.3f}", flush=True)


async def _run(day: date, ticker: str, minutes: int, cash: float, apply: bool,
               profile_v7: bool = False, profile_preflight: bool = False,
               repeat_preflight: int = 1,
               profile_v7_seeds: bool = False,
               profile_entry: bool = False,
               start_time: time = time(4),
               configuration_revision_id: str = "",
               max_execution_s: float = 3600) -> None:
    from src.backend.app import (  # noqa: PLC0415
        BacktestRunCreateRequest, HistoricalPreflightRequest,
        _trading_historical_preflight_payload, backtest_run_service,
        trading_backtest_run_create,
    )

    selected = (ticker,) if ticker else ()
    end_at = datetime.combine(day, start_time) + timedelta(minutes=minutes)
    if (start_time.tzinfo is not None or not time(4) <= start_time < time(20)
            or minutes < 1 or end_at > datetime.combine(day, time(20))):
        raise ValueError("App probe must remain within 04:00-20:00 ET")
    end = end_at.time()
    anchor = day + timedelta(days=1)
    preflight_request = HistoricalPreflightRequest(
        mode="backtest", anchor_date=anchor, session_count=1,
        initial_cash=cash, start_time=start_time.isoformat(),
        end_time=end.isoformat(), tickers=list(selected),
        configuration_revision_id=configuration_revision_id,
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
    preflight = None
    for repetition in range(repeat_preflight):
        began = perf_counter()
        observed = await asyncio.to_thread(load_preflight)
        preflight = observed
        print(f"App preflight pass {repetition + 1}/{repeat_preflight}: "
              f"wall_s={perf_counter()-began:.3f}", flush=True)
    assert preflight is not None
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
        run_plan_id=preflight["run_plan_id"], start_time=start_time.isoformat(),
        end_time=end.isoformat(), tickers=list(selected),
        experimental_structure_book="level-book-v7",
    )
    sql_profile = _SqlCallProfile()
    with (_profile_v7_updates(profile_v7),
          _profile_v7_seeds(profile_v7_seeds),
          _profile_entry_path(profile_entry),
          _profile_sql_calls(sql_profile)):
        began = perf_counter()
        response = await trading_backtest_run_create(request)
        launch_s = perf_counter() - began
        controller = backtest_run_service.get(response["run_id"])
        print(f"App launch: run_id={controller.run_id} wall_s={launch_s:.3f} "
              f"status={controller.status}", flush=True)
        if controller._task is None:
            raise RuntimeError("App route did not schedule Backtest execution")
        began = perf_counter()
        task_error = None
        try:
            await _await_run_with_progress(controller, max_execution_s=max_execution_s)
        except Exception as exc:
            task_error = exc
        execution_s = perf_counter() - began
    print(f"App result: run_id={controller.run_id} status={controller.status} "
          f"execution_s={execution_s:.3f} processed_rows={controller.processed_events} "
          f"error={controller.error[:300]}", flush=True)
    _print_journal_writer_profile(controller)
    if controller.status != "completed" or controller.run_dir.exists():
        # A failed full-session probe is still performance evidence. Keep its
        # bounded stage/SQL breakdown visible before the fail-closed error.
        for name, row in sorted(controller._stage_timings.items()):
            print(f"Stage {name}: calls={row['calls']} "
                  f"wall_s={row['seconds']:.3f} "
                  f"max_call_s={row['maximum_seconds']:.3f}", flush=True)
        sql_profile.print_summary()
        if task_error is not None:
            raise task_error
        raise RuntimeError("App Backtest failed or created a run-local directory")
    if task_error is not None:
        raise task_error
    _print_completed_profile(controller)
    sql_profile.print_summary()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", type=date.fromisoformat,
                        default=date(2026, 8, 18))
    parser.add_argument("--ticker", default="",
                        help="optional single-symbol scope; omit for all tradable tickers")
    parser.add_argument("--minutes", type=int, default=10,
                        help="whole minutes from --start-time; must end by 20:00 ET")
    parser.add_argument("--start-time", type=time.fromisoformat, default=time(4),
                        help="flat account start in ET; default 04:00; after-hours 16:00")
    parser.add_argument("--configuration-revision-id", default="",
                        help="exact published numbered release; default retains Strategy 1")
    parser.add_argument("--cash", type=float, default=100_000.0,
                        help="initial simulated cash; default matches the app")
    parser.add_argument("--apply", action="store_true",
                        help="create and await one normalized ClickHouse Backtest run")
    parser.add_argument("--profile-v7", action="store_true",
                        help="profile completed-second V7 engine calls in memory")
    parser.add_argument("--profile-v7-seeds", action="store_true",
                        help="time V7 seed read, split read, and book construction lanes")
    parser.add_argument("--profile-entry", action="store_true",
                        help="measure shared portfolio and OMS entry admission lanes")
    parser.add_argument("--profile-preflight", action="store_true",
                        help="profile the read-only app preflight in memory")
    parser.add_argument("--repeat-preflight", type=int, default=1,
                        help="repeat identical read-only preflight in one process")
    parser.add_argument("--repeat-runs", type=int, default=1,
                        help="run one or two full app probes in this process to compare warm reuse; requires --apply")
    parser.add_argument("--max-execution-seconds", type=float, default=3600,
                        help="execution deadline (default 3600s); request graceful stop and drain cleanup; timed-out P&L is invalid")
    args = parser.parse_args()
    if (not 0 < args.max_execution_seconds <= 86400
            or not 1 <= args.minutes <= 960 or not 1 <= args.repeat_preflight <= 5
            or not 1 <= args.repeat_runs <= 2
            or not 1_000 <= args.cash <= 1_000_000_000
            or args.cash != args.cash or args.cash in (float("inf"), float("-inf"))
            or (args.ticker and (not args.ticker.isascii()
                                 or not args.ticker.isalnum()))):
        parser.error("require 0 < execution deadline <= 86400 seconds, 1..960 minutes, 1,000..1,000,000,000 finite cash, "
                     "and an optional ASCII ticker")
    if args.repeat_runs > 1 and not args.apply:
        parser.error("--repeat-runs requires --apply")
    _load_private_credentials()
    async def probes() -> None:
        for number in range(1, args.repeat_runs + 1):
            if args.repeat_runs > 1:
                phase = "cold" if number == 1 else "warm"
                print(f"App probe {number}/{args.repeat_runs} ({phase} process)",
                      flush=True)
            await _run(args.session, args.ticker, args.minutes, args.cash,
                       args.apply, args.profile_v7, args.profile_preflight,
                       args.repeat_preflight, args.profile_v7_seeds,
                       args.profile_entry, args.start_time,
                       args.configuration_revision_id, args.max_execution_seconds)
    asyncio.run(probes())


if __name__ == "__main__":
    main()
