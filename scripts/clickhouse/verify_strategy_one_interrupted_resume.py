"""Crash and cold-resume one Strategy 1 Backtest from a typed V4 checkpoint.

This deliberately terminates only its own child process after that child
reports a committed running checkpoint. It never stops a managed service,
opens SQLite, or writes a run-local file. The resulting ClickHouse run is an
acceptance diagnostic, not authority to enable public resume by itself.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import date, timedelta
from multiprocessing import get_context
import os
from pathlib import Path
import sys
from time import monotonic
from uuid import UUID

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True

from scripts.clickhouse.smoke_strategy_one_backtest import (  # noqa: E402
    _load_private_credentials, _print_completed_profile,
)


async def _launch_and_pause(
    channel, session: date, cash: float, pause_boundary_ms: int,
) -> None:
    from src.backend.app import (
        BacktestRunCreateRequest, HistoricalPreflightRequest,
        _trading_historical_preflight_payload, backtest_run_service,
        trading_backtest_run_create,
    )

    preflight = await asyncio.to_thread(
        _trading_historical_preflight_payload,
        HistoricalPreflightRequest(
            mode="backtest", anchor_date=session + timedelta(days=1),
            session_count=1, initial_cash=cash, start_time="04:00:00",
            end_time="09:30:00", tickers=[],
        ),
    )
    blockers = tuple(row["id"] for row in preflight["checks"]
                     if row.get("required", True) and row["status"] != "ready")
    if blockers or not preflight["strategy_run_ready"] or preflight["execution_interval"] != "100ms":
        raise RuntimeError(f"Strategy 1 preflight blocked: {blockers}")
    response = await trading_backtest_run_create(BacktestRunCreateRequest(
        anchor_date=session + timedelta(days=1), session_count=1,
        initial_cash=cash, configuration_revision_id=preflight["configuration_revision_id"],
        run_plan_id=preflight["run_plan_id"], start_time="04:00:00",
        end_time="09:30:00", tickers=[],
        experimental_structure_book="level-book-v7",
    ))
    controller = backtest_run_service.get(response["run_id"])
    channel.send(("created", controller.run_id))
    pause_requested = False
    checkpoint_before_pause = None
    while controller._task is not None and not controller._task.done():
        cursor = dict(getattr(controller, "_source_cursor", {}) or {})
        boundary = int(cursor.get("boundary_ms") or 0)
        if boundary >= pause_boundary_ms and not pause_requested:
            checkpoint_before_pause = dict(
                getattr(controller, "_checkpoint_projection_cache", {}) or {}
            ).get("cursor")
            await controller.command("pause")
            pause_requested = True
        checkpoint = dict(getattr(controller, "_checkpoint_projection_cache", {}) or {})
        if (pause_requested and controller.status == "paused"
                and checkpoint.get("status") == "cursor_fenced"
                and checkpoint.get("cursor") != checkpoint_before_pause
                and int(checkpoint.get("processed_events") or 0) > 0
                and getattr(controller, "_checkpoint_io_task", None) is None
                and getattr(controller, "_journal_publish_error", None) is None):
            channel.send(("fenced", controller.run_id, boundary,
                          int(checkpoint["processed_events"])))
            # The parent kills this process. Never call Stop: Stop publishes
            # a terminal journal and cannot exercise crash recovery.
            await asyncio.Event().wait()
        await asyncio.sleep(0.05)
    raise RuntimeError(f"Run became terminal before a pausable checkpoint: {controller.status}")


def _child(channel, session: date, cash: float, pause_boundary_ms: int) -> None:
    try:
        _load_private_credentials()
        asyncio.run(_launch_and_pause(channel, session, cash, pause_boundary_ms))
    except BaseException as exc:
        channel.send(("error", type(exc).__name__, str(exc)[:500]))
        raise
    finally:
        channel.close()


async def _resume(run_id: str) -> None:
    from src.backend.replay_run_service import ReplayRunService, backtest_runtime_root

    service = ReplayRunService(
        runtime_root=backtest_runtime_root(), allow_typed_backtest_resume=True)
    started = monotonic()
    controller = await service.resume(run_id)
    if controller._task is None:
        raise RuntimeError("Cold resume did not start execution")
    await controller._task
    elapsed = monotonic() - started
    print(f"Resume result: run_id={run_id} status={controller.status} "
          f"wall_s={elapsed:.3f} processed_rows={controller.processed_events} "
          f"error={controller.error[:300]}", flush=True)
    if controller.status != "completed" or controller.run_dir.exists():
        raise RuntimeError("Cold-resumed Backtest failed or wrote a run-local directory")
    _print_completed_profile(controller)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", type=date.fromisoformat,
                        default=date(2026, 8, 18))
    parser.add_argument("--cash", type=float, default=10_000)
    parser.add_argument("--pause-after-minutes", type=int, default=315,
                        help="completed market minutes after 04:00 ET; default 09:15")
    parser.add_argument("--timeout-seconds", type=int, default=300)
    parser.add_argument("--resume-run-id", type=lambda value: str(UUID(value)),
                        help="retry cold assembly of an already interrupted diagnostic run")
    args = parser.parse_args()
    if (not 1 <= args.pause_after_minutes < 330
            or not 1_000 <= args.cash <= 1_000_000_000
            or args.cash != args.cash
            or not 60 <= args.timeout_seconds <= 900):
        parser.error("invalid cash, interruption boundary, or timeout")
    _load_private_credentials()
    if args.resume_run_id:
        asyncio.run(_resume(args.resume_run_id))
        return
    context = get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(
        target=_child,
        args=(sender, args.session, args.cash, args.pause_after_minutes * 60_000),
        name="strategy-one-interruption-probe",
    )
    process.start()
    sender.close()
    run_id = ""
    fenced = False
    deadline = monotonic() + args.timeout_seconds
    try:
        while monotonic() < deadline:
            if receiver.poll(1):
                message = receiver.recv()
                if message[0] == "created":
                    run_id = message[1]
                    print(f"Interruption run created: {run_id}", flush=True)
                elif message[0] == "fenced":
                    if message[1] != run_id:
                        raise RuntimeError("Child changed its Backtest identity")
                    print(f"Durable running checkpoint: run_id={run_id} "
                          f"boundary_ms={message[2]} processed_rows={message[3]}", flush=True)
                    fenced = True
                    break
                elif message[0] == "error":
                    raise RuntimeError(f"Child failed: {message[1]}: {message[2]}")
            if not process.is_alive():
                raise RuntimeError(f"Child exited before checkpoint: {process.exitcode}")
        if not fenced:
            raise TimeoutError("No durable running checkpoint before deadline")
    finally:
        if process.is_alive():
            process.terminate()
        process.join(timeout=15)
        receiver.close()
    if process.is_alive():
        raise RuntimeError("Interruption child did not terminate")
    if not run_id or not fenced:
        raise RuntimeError("Cannot resume without the child's fenced run identity")
    print(f"Interrupted only diagnostic child pid={process.pid}; resuming cold", flush=True)
    asyncio.run(_resume(run_id))


if __name__ == "__main__":
    main()
