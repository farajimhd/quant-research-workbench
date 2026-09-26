"""Bounded workstation integration probe for the still-gated fixed Backtest.

This deliberately invokes the real private controller path after full market
preflight, without changing the public launch blocker. --apply persists a new
normalized ClickHouse test journal, never a run-local file or market product.
It is for integration validation only, not a user-facing launch workaround.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import date, time, timedelta
import os
from pathlib import Path
import platform
import sys
from time import perf_counter

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


async def _run(day: date, ticker: str, *, apply: bool) -> None:
    from src.backend.replay_run_service import (
        ReplayRunController, ReplayRunDefinition, backtest_preflight,
    )
    from src.backend.trading_configuration_service import backtest_configuration_snapshot
    from src.trading_runtime.runtime import RunMode

    revision = backtest_configuration_snapshot()
    began = perf_counter()
    preflight = await asyncio.to_thread(
        backtest_preflight, anchor_date=day + timedelta(days=1),
        session_count=1, start_time=time(4), end_time=time(9, 30),
        tickers=(ticker,), configuration_revision=revision)
    window = tuple(preflight["window"]["sessions"])
    if window != (day.isoformat(),):
        raise RuntimeError("Strategy 1 integration selected a different exchange day")
    allowed_blockers = {"fixed_execution_contract", "runtime_storage"}
    blocked = {row["id"]: row["summary"] for row in preflight["checks"]
               if row.get("required", True) and row["status"] != "ready"
               and row["id"] not in allowed_blockers}
    print(f"Preflight {day} {ticker}: {perf_counter()-began:.3f}s; "
          f"unresolved={tuple(blocked)}", flush=True)
    if blocked:
        raise RuntimeError("Strategy 1 integration lacks a required input: "
                           + "; ".join(f"{key}: {value}" for key, value in blocked.items()))
    if not apply:
        print("Plan only: no typed journal or run was created", flush=True)
        return
    definition = ReplayRunDefinition(
        session_date=day, final_session_date=day,
        start_time=time(4), end_time=time(9, 30), initial_cash=100_000,
        configuration_revision=revision, execution_interval="100ms",
        market_data_plan=dict(preflight["market_data_plan"]),
        causal_v7_plan=dict(preflight["causal_v7_plan"]),
        mode=RunMode.BACKTEST, simulation_profile="baseline",
        new_order_activation_delay_ms=0.0,
        experimental_structure_book="level-book-v7", tickers=(ticker,))
    controller = ReplayRunController(definition, runtime_root=RUNTIME_ROOT)
    began = perf_counter()
    await controller._run()
    elapsed = perf_counter() - began
    print(f"Strategy 1 probe run_id={controller.run_id} "
          f"status={controller.status} elapsed_s={elapsed:.3f} "
          f"processed_rows={controller.processed_events} "
          f"error={controller.error[:300]}", flush=True)
    if controller.status != "completed":
        raise RuntimeError("Strategy 1 integration run did not complete")
    if controller.run_dir.exists():
        raise RuntimeError("Strategy 1 integration wrote a run-local directory")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", type=date.fromisoformat,
                        default=date(2026, 8, 18))
    parser.add_argument("--ticker", default="IPST")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not args.ticker.isascii() or not args.ticker.isalnum():
        raise ValueError("Integration ticker must be an ASCII market symbol")
    _load_private_credentials()
    asyncio.run(_run(args.session, args.ticker, apply=args.apply))


if __name__ == "__main__":
    main()
