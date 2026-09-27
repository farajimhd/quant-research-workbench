"""V4 terminal publication must fence the final completed market boundary."""
from __future__ import annotations

import asyncio
from datetime import date
from types import SimpleNamespace

from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.backend.backtest_market_data import market_day_boundary
from src.backend.replay_run_service import ReplayRunController
from src.trading_runtime.runtime import RunMode


RUN = "00000000-0000-0000-0000-000000000001"


def test_v4_terminal_fences_cursor_before_lifecycle(monkeypatch):
    day = date(2026, 8, 18)
    controller = object.__new__(ReplayRunController)
    controller.run_id = RUN
    controller.definition = SimpleNamespace(mode=RunMode.BACKTEST,
                                            session_date=day)
    controller._account_map = {"A": "SIM-01-A"}
    controller._journal = BacktestMemoryJournal(run_id=RUN)
    controller._runtime_finished = False
    controller._source_cursor = {"session_date": day, "boundary_ms": 100,
                                 "sequence": 1}
    controller._frame_cursor = {}
    controller.current_time = market_day_boundary(day, 100)
    calls = []

    async def fence(at, *, checkpoint_status):
        assert checkpoint_status == "running"
        calls.append(("cursor", at))
        publisher._source_cursor = "2026-08-18:100"

    async def finish(*, status):
        calls.append(("lifecycle", status))
        controller._journal.append(
            run_id=RUN, category="lifecycle", entity_type="run",
            entity_id=RUN, event_time=controller.current_time,
            payload={"status": status})

    def enqueue(_captures):
        calls.append(("publish_terminal", None))
        return asyncio.create_task(asyncio.sleep(0))

    publisher = SimpleNamespace(
        writer=SimpleNamespace(journal_profile="backtest_v4"),
        _source_cursor="start", enqueue_terminal=enqueue,
    )
    controller._journal_publisher = publisher
    controller._runtime = SimpleNamespace(
        finish=finish,
        portfolio=SimpleNamespace(capture_recovery_snapshot=lambda *_a, **_k:
                                  object()),
    )
    monkeypatch.setattr(controller, "_save_restart_checkpoint_responsive", fence)
    asyncio.run(controller._finish_fixed_v4("completed"))
    assert [name for name, _ in calls] == [
        "cursor", "lifecycle", "publish_terminal"]
    assert controller._runtime_finished is True
