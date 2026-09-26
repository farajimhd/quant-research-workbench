"""The inactive V4 launch path owns four clients and one Keeper session."""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from src.backend import backtest_fixed_journal_bootstrap as bootstrap
from src.backend import backtest_journal_clickhouse, backtest_fixed_v4_certification
from src.backend.replay_run_service import ReplayRunController
from src.trading_runtime import arte_journal_writer, keeper_session
from src.trading_runtime.runtime import RunMode


def test_v4_handoff_pins_accounts_and_closes_control_clients(monkeypatch):
    calls = []
    account = {"account_key": "main", "modes": ["backtest"]}
    definition = SimpleNamespace(
        mode=RunMode.BACKTEST, execution_interval="100ms",
        session_date=date(2026, 8, 18),
        session_start=datetime(2026, 8, 18, 8, tzinfo=timezone.utc),
        configuration_revision={"content_hash": "a" * 64, "payload": {
            "strategy": {"strategy_number": 1,
                         "strategy_id": "early-squeeze-strategy", "revision": 1},
            "accounts": {"bindings": [account]},
            "run_plan": {"run_plan_id": "strategy-one"},
        }}, market_data_plan={"token": "b" * 64})
    controller = object.__new__(ReplayRunController)
    controller.definition = definition
    controller.run_id = str(uuid4())
    controller.created_at = datetime(2026, 9, 26, tzinfo=timezone.utc)
    controller._journal = None
    controller._resume_state = None
    controller._fixed_keeper_session = None
    controller._fixed_v4_account_ids = None

    async def plans():
        calls.append("sealed_plans")
        return SimpleNamespace(
            market=SimpleNamespace(token="b" * 64),
            execution_market=SimpleNamespace(token="c" * 64))

    controller._fixed_strategy_one_plans = plans
    monkeypatch.setattr(backtest_journal_clickhouse, "backtest_code_hash",
                        lambda _root: "d" * 64)
    monkeypatch.setattr(backtest_fixed_v4_certification,
                        "certify_strategy_one_v4_projection",
                        lambda: "e" * 64)

    class Resource:
        def __init__(self, name):
            self.name = name

        def close(self):
            calls.append(f"close:{self.name}")

    session = Resource("keeper")
    monkeypatch.setattr(keeper_session, "open_workstation_keeper_session",
                        lambda: session)
    monkeypatch.setattr(arte_journal_writer, "backtest_v4_context_client_from_env",
                        lambda *, keeper_session: Resource("context"))
    monkeypatch.setattr(arte_journal_writer, "journal_client_from_env",
                        lambda: Resource("reader"))
    monkeypatch.setattr(arte_journal_writer, "backtest_v4_journal_client_from_env",
                        lambda *, keeper_session: Resource("writer_client"))
    assembly = SimpleNamespace(writer=Resource("writer"), journal=Resource("journal"))

    def publish(context, reader, writer, terminal, **kwargs):
        calls.append("publish")
        assert len({id(context), id(reader), id(writer), id(terminal)}) == 4
        assert kwargs["account_ids"] == ("SIM-01-MAIN",)
        assert kwargs["run"]["run_month"] == "2026-09-01"
        assert kwargs["run"]["session_date"] == "2026-08-18"
        assert kwargs["config"]["strategy_revision"] == 1
        assert kwargs["projection_certifier"]() == "e" * 64
        return assembly

    monkeypatch.setattr(bootstrap, "publish_and_assemble_fixed_v4_journal", publish)
    def attach(found):
        assert found is assembly
        controller._journal_writer = assembly.writer
        controller._journal = assembly.journal
        controller._journal_publisher = None
        calls.append("attach")

    controller._attach_fixed_journal_assembly = attach
    asyncio.run(controller._open_fixed_journal())
    assert controller._fixed_keeper_session is session
    assert controller._fixed_v4_account_ids == ("SIM-01-MAIN",)
    assert calls[:2] == ["sealed_plans", "publish"]
    assert calls[-1] == "attach"
    assert calls.count("close:reader") == 2
    assert "close:context" in calls and "close:keeper" not in calls
    asyncio.run(controller._close_fixed_journal())
    assert calls[-1] == "close:keeper"
