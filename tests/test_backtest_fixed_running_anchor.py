from copy import deepcopy
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from src.backend import backtest_fixed_running_anchor as anchor_module
from src.backend.backtest_fixed_running_anchor import load_fixed_running_prefix_anchor
from src.backend.backtest_market_data import CertifiedMarketDayPlan, ExecutionInterval
from src.backend.replay_run_service import ReplayRunController, RunMode
from src.trading_runtime.arte_journal_writer import V2CommittedPrefix


RUN = "backtest:fixed-anchor"
BATCH = "00000000-0000-0000-0000-000000000a12"
PLAN_TOKEN = "a" * 64
CONFIG = "b" * 64
DAY = "2026-08-18"


def _plan():
    return CertifiedMarketDayPlan(
        ExecutionInterval.fixed(100), "build", "definition", (DAY,),
        ("AAPL",), (), (100,), PLAN_TOKEN)


def _fixture():
    context = dict(mode="backtest", configuration_hash=CONFIG,
                   market_plan_token=PLAN_TOKEN, account_ids=["DU1"])
    prefix = V2CommittedPrefix(RUN, 17, BATCH, f"{DAY}:100", "running", (BATCH,))
    cursor = dict(run_id=RUN, batch_id=BATCH, account_id="",
                  event_month="2026-08-01", session_date=DAY,
                  boundary_ms=100, market_sequence=8, event_sequence=17,
                  frame_as_of="2026-08-18 08:00:00.100000",
                  frame_ticker="AAPL", frame_timeframe="100ms",
                  frame_sequence=8)
    return context, prefix, cursor


def _install(monkeypatch, context, prefix, cursor):
    calls = []
    monkeypatch.setattr(anchor_module, "load_typed_run_context",
                        lambda client, run_id: calls.append("context") or context)
    monkeypatch.setattr(anchor_module, "load_committed_prefix",
                        lambda client, run_id, *, journal_profile: (
                            calls.append(journal_profile) or prefix))
    monkeypatch.setattr(anchor_module, "load_latest_backtest_cursor",
                        lambda client, verified: calls.append("cursor") or cursor)
    return calls


def _load():
    return load_fixed_running_prefix_anchor(
        object(), run_id=RUN, plan=_plan(), configuration_hash=CONFIG,
        account_ids=("DU1",))


def test_read_only_typed_running_prefix_and_completed_cursor(monkeypatch):
    context, prefix, cursor = _fixture()
    calls = _install(monkeypatch, context, prefix, cursor)
    anchor = _load()
    assert calls == ["context", "backtest_v2", "cursor"]
    assert anchor.source_cursor == f"{DAY}:100"
    assert anchor.journal_sequence == 17 and anchor.market_sequence == 8
    assert anchor.completed_at == datetime(2026, 8, 18, 8, 0, 0, 100000,
                                           tzinfo=timezone.utc)
    assert anchor.frame_cursor == (anchor.completed_at, "AAPL", "100ms", 8)


@pytest.mark.parametrize("target,key,value", [
    ("context", "market_plan_token", "c" * 64),
    ("context", "configuration_hash", "c" * 64),
    ("prefix", "status", "completed"),
    ("prefix", "source_cursor", f"{DAY}:200"),
    ("cursor", "batch_id", "00000000-0000-0000-0000-000000000a99"),
    ("cursor", "event_sequence", 16),
    ("cursor", "boundary_ms", 200),
    ("cursor", "session_date", "2026-08-19"),
    ("cursor", "frame_as_of", "2026-08-18 08:00:00.200000"),
    ("cursor", "frame_ticker", "MSFT"),
    ("cursor", "frame_sequence", 9),
])
def test_running_anchor_rejects_identity_and_future_cursor(
    monkeypatch, target, key, value,
):
    context, prefix, cursor = _fixture()
    context, cursor = deepcopy(context), deepcopy(cursor)
    if target == "prefix":
        prefix = V2CommittedPrefix(**{**{
            name: getattr(prefix, name) for name in (
                "run_id", "last_sequence", "last_batch_id", "source_cursor",
                "status", "batch_ids")}, key: value})
    else:
        {"context": context, "cursor": cursor}[target][key] = value
    _install(monkeypatch, context, prefix, cursor)
    with pytest.raises((ValueError, RuntimeError)):
        _load()


def test_controller_anchor_seam_refuses_opaque_disk_resume(monkeypatch):
    controller = object.__new__(ReplayRunController)
    controller.run_id = RUN
    controller.definition = SimpleNamespace(
        mode=RunMode.BACKTEST,
        configuration_revision={"content_hash": CONFIG})
    controller._resume_state = {"broker": {"opaque": "state"}}
    monkeypatch.setattr(anchor_module, "load_fixed_running_prefix_anchor",
                        lambda *_a, **_k: pytest.fail("cold read after disk checkpoint"))
    with pytest.raises(RuntimeError, match="cannot adopt a disk checkpoint"):
        controller._read_fixed_running_prefix_anchor(object(), _plan())


def test_controller_anchor_seam_passes_pinned_identity_without_restoring_state(monkeypatch):
    controller = object.__new__(ReplayRunController)
    controller.run_id = RUN
    controller.definition = SimpleNamespace(
        mode=RunMode.BACKTEST,
        configuration_revision={"content_hash": CONFIG})
    controller._account_map = {"key": "DU1"}
    controller._resume_state = None
    controller._source_cursor = {}
    expected = object()
    def read(_client, **kwargs):
        assert kwargs == dict(run_id=RUN, plan=plan,
                              configuration_hash=CONFIG,
                              account_ids=("DU1",))
        return expected
    plan = _plan()
    monkeypatch.setattr(anchor_module, "load_fixed_running_prefix_anchor", read)
    assert controller._read_fixed_running_prefix_anchor(object(), plan) is expected
    assert controller._source_cursor == {}
    assert controller._resume_state is None
