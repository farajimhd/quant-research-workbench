"""Bounded workstation integration probe for the public Strategy 1 Backtest.

This invokes the public controller start path after full market preflight.
--apply persists a new normalized ClickHouse test journal, never a run-local
file or market product.
It is for integration validation only, not a user-facing launch workaround.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
import cProfile
from datetime import date, datetime, time, timedelta
from io import StringIO
import os
from pathlib import Path
import platform
import pstats
import sys
from threading import Lock
from time import perf_counter
import traceback

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True


SECRET_ROOT = Path(r"D:\TradingML\secrets")
RUNTIME_ROOT = Path(r"D:\TradingML\runtimes")


def _load_private_credentials() -> None:
    if platform.node().upper() != "DESKTOP-SAAI85T":
        raise RuntimeError("Strategy 1 integration probe is workstation-only")
    if not RUNTIME_ROOT.is_dir() or not SECRET_ROOT.is_dir():
        raise RuntimeError("Managed workstation runtime or secrets root is unavailable")
    os.environ["BACKTEST_V3_READ_CREDENTIAL_FILE"] = str(
        SECRET_ROOT / "backtest_v3_read.env")
    for name in ("trading_journal.env", "backtest_v4_runner.env"):
        path = SECRET_ROOT / name
        if not path.is_file():
            raise RuntimeError(f"Managed credential file is absent: {name}")
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line or line.startswith("#"):
                continue
            key, separator, value = line.partition("=")
            if not separator or not key or not value or key in os.environ:
                raise RuntimeError(f"Managed credential file is invalid: {name}")
            os.environ[key] = value


def _print_completed_profile(controller) -> None:
    """Report bounded wall stages and writer work, without dumping journal data."""
    for name, row in sorted(controller._stage_timings.items()):
        print(f"Stage {name}: calls={row['calls']} "
              f"wall_s={row['seconds']:.3f} "
              f"max_call_s={row['maximum_seconds']:.3f}", flush=True)
    metrics = getattr(controller, "_journal_writer_final_metrics", None)
    if (not isinstance(metrics, dict) or metrics.get("committed_units", 0) < 1
            or metrics.get("failed_units") != 0 or metrics.get("failed")
            or metrics.get("queue_depth") != 0):
        raise RuntimeError("Completed Backtest lacks a drained typed journal profile")
    print("Journal writer: "
          f"committed_units={metrics['committed_units']} "
          f"failed_units={metrics['failed_units']} "
          f"worker_s={metrics['publish_ns_total'] / 1e9:.3f} "
          f"max_unit_s={metrics['publish_ns_max'] / 1e9:.3f} "
          f"queue_capacity={metrics['queue_capacity']}", flush=True)


def _profile_preflight_call(call, **kwargs):
    """Profile the worker-thread preflight itself, not its asyncio caller."""
    profile = cProfile.Profile()
    try:
        return profile.runcall(call, **kwargs)
    finally:
        output = StringIO()
        pstats.Stats(profile, stream=output).sort_stats("cumulative").print_stats(25)
        print("Preflight call profile (top 25 cumulative seconds):", flush=True)
        print(output.getvalue(), flush=True)


class _SqlCallProfile:
    """Per-process HTTP timing; SQL text and credentials are never retained."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._bins: dict[str, tuple[int, float]] = {}
        self._v7_stream_reads = 0

    @staticmethod
    def category(sql: str) -> str:
        if "FROM arte.bars_v1" in sql and "AND resolution_ms=1000" in sql:
            return "v7_completed_second_read"
        journal = "arte.trading_" in sql.lower()
        insert = sql.lstrip().upper().startswith("INSERT ")
        return ("journal" if journal else "market_or_control") + (
            "_insert" if insert else "_read")

    def record(self, sql: str, elapsed: float) -> None:
        category = self.category(sql)
        with self._lock:
            calls, seconds = self._bins.get(category, (0, 0.0))
            self._bins[category] = (calls + 1, seconds + elapsed)

    def record_stream(self, sql: str) -> None:
        if self.category(sql) == "v7_completed_second_read":
            with self._lock:
                self._v7_stream_reads += 1

    def print_summary(self) -> None:
        for category, (calls, seconds) in sorted(self._bins.items()):
            print(f"ClickHouse {category}: calls={calls} "
                  f"client_s={seconds:.3f}", flush=True)
        print(f"ClickHouse v7_completed_second_stream: "
              f"calls={self._v7_stream_reads}; timing=not_measured", flush=True)


@contextmanager
def _profile_sql_calls(profile: _SqlCallProfile):
    from research.mlops.clickhouse import ClickHouseHttpClient

    original = ClickHouseHttpClient.execute
    original_stream = ClickHouseHttpClient.iter_json_each_row

    def timed_execute(client, sql, *args, **kwargs):
        started = perf_counter()
        try:
            return original(client, sql, *args, **kwargs)
        finally:
            profile.record(sql, perf_counter() - started)

    def counted_stream(client, sql, *args, **kwargs):
        result = original_stream(client, sql, *args, **kwargs)
        profile.record_stream(sql)
        return result

    ClickHouseHttpClient.execute = timed_execute
    ClickHouseHttpClient.iter_json_each_row = counted_stream
    try:
        yield
    finally:
        ClickHouseHttpClient.execute = original
        ClickHouseHttpClient.iter_json_each_row = original_stream


async def _run(day: date, ticker: str, *, apply: bool, minutes: int,
               profile_preflight: bool = False,
               profile_execution: bool = False) -> None:
    from src.backend.replay_run_service import (
        ReplayRunController, ReplayRunDefinition, backtest_preflight,
    )
    from src.backend.trading_configuration_service import backtest_configuration_snapshot
    from src.trading_runtime.runtime import RunMode

    revision = backtest_configuration_snapshot()
    selected = (ticker,) if ticker else ()
    end_time = (datetime.combine(day, time(4))
                + timedelta(minutes=minutes)).time()
    began = perf_counter()
    preflight = await asyncio.to_thread(
        _profile_preflight_call if profile_preflight else backtest_preflight,
        **({"call": backtest_preflight} if profile_preflight else {}),
        anchor_date=day + timedelta(days=1),
        session_count=1, start_time=time(4), end_time=end_time,
        tickers=selected, configuration_revision=revision)
    window = tuple(preflight["window"]["sessions"])
    if window != (day.isoformat(),):
        raise RuntimeError("Strategy 1 integration selected a different exchange day")
    blocked = {row["id"]: row["summary"] for row in preflight["checks"]
               if row.get("required", True) and row["status"] != "ready"}
    print(f"Preflight {day} {ticker or 'full-market'}: {perf_counter()-began:.3f}s; "
          f"unresolved={tuple(blocked)}", flush=True)
    if blocked or not preflight["ready"]:
        raise RuntimeError("Strategy 1 integration lacks a required input: "
                           + "; ".join(f"{key}: {value}" for key, value in blocked.items()))
    if not apply:
        print("Plan only: no typed journal or run was created", flush=True)
        return
    definition = ReplayRunDefinition(
        session_date=day, final_session_date=day,
        start_time=time(4), end_time=end_time, initial_cash=100_000,
        configuration_revision=revision, execution_interval="100ms",
        market_data_plan=dict(preflight["market_data_plan"]),
        causal_v7_plan=dict(preflight["causal_v7_plan"]),
        mode=RunMode.BACKTEST, simulation_profile="baseline",
        new_order_activation_delay_ms=0.0,
        experimental_structure_book="level-book-v7", tickers=selected)
    controller = ReplayRunController(definition, runtime_root=RUNTIME_ROOT)
    open_journal = controller._open_fixed_journal

    async def traced_open_journal():
        try:
            return await open_journal()
        except Exception:
            # This probe runs only on the managed workstation. Emit a Python
            # stack, never SQL, request headers, credential values, or files.
            traceback.print_exc(limit=12)
            raise

    controller._open_fixed_journal = traced_open_journal
    began = perf_counter()
    sql_profile = _SqlCallProfile()
    execution_profile = cProfile.Profile() if profile_execution else None
    if execution_profile is not None:
        execution_profile.enable()
    try:
        with _profile_sql_calls(sql_profile):
            await controller.start()
            if controller._task is None:
                raise RuntimeError("Public Backtest start did not schedule execution")
            await controller._task
    finally:
        if execution_profile is not None:
            execution_profile.disable()
            output = StringIO()
            pstats.Stats(execution_profile, stream=output).sort_stats(
                "cumulative").print_stats(35)
            pstats.Stats(execution_profile, stream=output).sort_stats(
                "cumulative").print_stats("streaming_level_book", 30)
            pstats.Stats(execution_profile, stream=output).sort_stats(
                "tottime").print_stats("streaming_level_book", 30)
            print("Execution event-loop profile (top 35; worker threads excluded):",
                  flush=True)
            print(output.getvalue(), flush=True)
    elapsed = perf_counter() - began
    print(f"Strategy 1 probe run_id={controller.run_id} "
          f"status={controller.status} elapsed_s={elapsed:.3f} "
          f"processed_rows={controller.processed_events} "
          f"error={controller.error[:300]}", flush=True)
    if controller.status != "completed":
        raise RuntimeError("Strategy 1 integration run did not complete")
    if controller.run_dir.exists():
        raise RuntimeError("Strategy 1 integration wrote a run-local directory")
    _print_completed_profile(controller)
    sql_profile.print_summary()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", type=date.fromisoformat,
                        default=date(2026, 8, 18))
    parser.add_argument("--ticker", default="",
                        help="optional single-symbol probe; omit for the full market")
    parser.add_argument("--minutes", type=int, default=10,
                        help="whole minutes from 04:00 ET, at most 330")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--profile-preflight", action="store_true",
                        help="show the slowest preflight calls; does not create market data")
    parser.add_argument("--profile-execution", action="store_true",
                        help="show main event-loop calls; profile overhead affects wall time")
    args = parser.parse_args()
    if args.ticker and (not args.ticker.isascii() or not args.ticker.isalnum()):
        raise ValueError("Integration ticker must be an ASCII market symbol")
    if not 1 <= args.minutes <= 330:
        raise ValueError("Integration horizon must be one to 330 minutes")
    _load_private_credentials()
    asyncio.run(_run(args.session, args.ticker, apply=args.apply,
                     minutes=args.minutes, profile_preflight=args.profile_preflight,
                     profile_execution=args.profile_execution))


if __name__ == "__main__":
    main()
