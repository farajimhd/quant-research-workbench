from copy import deepcopy
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from src.backend import backtest_fixed_running_anchor as anchor_module
from src.backend.backtest_squeeze_episode_v3 import V3CommittedPrefix
from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix
from src.backend.backtest_fixed_running_anchor import load_fixed_running_prefix_anchor
from src.backend.backtest_market_data import CertifiedMarketDayPlan, ExecutionInterval
from src.backend.replay_run_service import ReplayRunController, RunMode
from src.trading_runtime.arte_journal_writer import V2CommittedPrefix
from src.trading_runtime.arte_journal_writer import _committed_batch_filter
from src.backend.backtest_v4_keeper_lease import BacktestV4KeeperLease
from src.trading_runtime.arte_typed_insert_dispatch import TypedInsertDispatch
from src.trading_runtime.keeper_session import ManagedKeeperSession
from tests.test_live_signal_completion_keeper import FakeKazoo


RUN = "backtest:fixed-anchor"
BATCH = "00000000-0000-0000-0000-000000000a12"
PLAN_TOKEN = "a" * 64
CONFIG = "b" * 64
CODE_HASH = "c" * 64
DAY = "2026-08-18"
V4_RUN = "62908518-9fd4-4a8e-8c90-2162ccb237e1"


def test_cold_v4_resume_rejects_old_run_without_prior_owner_epoch():
    keeper = FakeKazoo()
    keeper.add_listener = lambda _listener: None
    session = ManagedKeeperSession(keeper)
    session._on_state("CONNECTED")
    lease = BacktestV4KeeperLease.acquire(
        session, run_id=V4_RUN, owner_id="first-after-the-fact")
    try:
        with pytest.raises(ValueError, match="prior fenced run owner"):
            anchor_module.cold_verify_v4_resume_anchor(
                SimpleNamespace(execute=lambda _sql: "1"),
                dispatch=TypedInsertDispatch(keeper), lease=lease,
                run_id=V4_RUN, plan=_plan(), configuration_hash=CONFIG,
                account_ids=("DU1",), code_hash=CODE_HASH)
    finally:
        lease.release()


def test_cold_v4_resume_rejects_prior_epoch_without_genesis():
    keeper = FakeKazoo()
    keeper.add_listener = lambda _listener: None
    session = ManagedKeeperSession(keeper)
    session._on_state("CONNECTED")
    old = BacktestV4KeeperLease.acquire(
        session, run_id=V4_RUN, owner_id="unfenced-old-writer")
    assert old.release()
    lease = BacktestV4KeeperLease.acquire(
        session, run_id=V4_RUN, owner_id="candidate-reader")
    try:
        with pytest.raises(RuntimeError, match="genesis is absent"):
            anchor_module.cold_verify_v4_resume_anchor(
                SimpleNamespace(execute=lambda _sql: "1"),
                dispatch=TypedInsertDispatch(keeper), lease=lease,
                run_id=V4_RUN, plan=_plan(), configuration_hash=CONFIG,
                account_ids=("DU1",), code_hash=CODE_HASH)
    finally:
        lease.release()


@pytest.mark.parametrize("mismatch", [False, True])
def test_cold_v4_resume_joins_owner_dispatch_prefix_and_cursor(monkeypatch, mismatch):
    keeper = FakeKazoo()
    keeper.add_listener = lambda _listener: None
    session = ManagedKeeperSession(keeper)
    session._on_state("CONNECTED")
    original = BacktestV4KeeperLease.acquire(
        session, run_id=V4_RUN, owner_id="original-writer")
    original.attest_genesis(
        configuration_hash=CONFIG, market_plan_token=PLAN_TOKEN,
        code_hash=CODE_HASH)
    assert original.release()
    lease = BacktestV4KeeperLease.acquire(
        session, run_id=V4_RUN, owner_id="cold-reader")
    calls = []
    barrier = SimpleNamespace(
        verify_run_context_receipt=lambda _client: calls.append("context"),
        verify_committed_prefix=lambda _client, *, journal_profile: (
            calls.append(journal_profile) or V4CommittedPrefix(
                V4_RUN, 17, BATCH, f"{DAY}:{200 if mismatch else 100}",
                "running", (BATCH,))),
        assert_fenced=lambda _run: calls.append("fenced"),
        release=lambda: calls.append("released"))
    dispatch = TypedInsertDispatch(keeper)
    monkeypatch.setattr(dispatch, "acquire_cold_barrier",
                        lambda run_id: calls.append("barrier") or barrier)
    anchor = anchor_module.FixedRunningPrefixAnchor(
        V4_RUN, BATCH, 17, f"{DAY}:100", DAY, 100, 8,
        datetime(2026, 8, 18, 8, 0, 0, 100_000, tzinfo=timezone.utc), None)
    monkeypatch.setattr(anchor_module, "load_fixed_running_prefix_anchor",
                        lambda *_args, **_kwargs: calls.append("anchor") or anchor)
    client = SimpleNamespace(execute=lambda sql: "1" if sql ==
                             "SELECT getSetting('readonly')" else "")
    try:
        if mismatch:
            with pytest.raises(RuntimeError, match="differs from Keeper prefix"):
                anchor_module.cold_verify_v4_resume_anchor(
                    client, dispatch=dispatch, lease=lease, run_id=V4_RUN,
                    plan=_plan(), configuration_hash=CONFIG,
                    account_ids=("DU1",), code_hash=CODE_HASH)
            assert calls == ["barrier", "context", "backtest_v4", "anchor",
                             "released"]
        else:
            assert anchor_module.cold_verify_v4_resume_anchor(
                client, dispatch=dispatch, lease=lease, run_id=V4_RUN,
                plan=_plan(), configuration_hash=CONFIG,
                account_ids=("DU1",), code_hash=CODE_HASH) == anchor
            assert calls == ["barrier", "context", "backtest_v4", "anchor",
                             "fenced", "released"]
    finally:
        lease.release()


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


def test_v3_anchor_uses_attested_prefix_and_only_v3_commit_batches(monkeypatch):
    context, old_prefix, cursor = _fixture()
    prefix = V3CommittedPrefix(
        old_prefix.run_id, old_prefix.last_sequence, old_prefix.last_batch_id,
        old_prefix.source_cursor, old_prefix.status, old_prefix.batch_ids, ())
    monkeypatch.setattr(anchor_module, "load_typed_run_context",
                        lambda _client, _run_id: context)
    def verified(_client, _run_id, **kwargs):
        assert kwargs == dict(expected_market_plan_token=PLAN_TOKEN,
                              expected_query_sha256="c" * 64)
        return prefix
    monkeypatch.setattr(anchor_module, "load_verified_squeeze_v3_prefix", verified)
    monkeypatch.setattr(anchor_module, "load_committed_prefix",
                        lambda *_a, **_k: pytest.fail("V2 fence used for V3"))
    monkeypatch.setattr(anchor_module, "load_latest_backtest_cursor",
                        lambda _client, verified: cursor if verified is prefix else None)
    result = load_fixed_running_prefix_anchor(
        object(), run_id=RUN, plan=_plan(), configuration_hash=CONFIG,
        account_ids=("DU1",), journal_profile="backtest_v3",
        expected_query_sha256="c" * 64)
    assert result.batch_id == BATCH and result.journal_sequence == 17
    assert "arte.trading_commit_v3" in _committed_batch_filter(prefix)
    assert "arte.trading_commit_v2" not in _committed_batch_filter(prefix)


def test_v4_anchor_uses_verified_v4_batches_only(monkeypatch):
    context, old_prefix, cursor = _fixture()
    prefix = V4CommittedPrefix(
        old_prefix.run_id, old_prefix.last_sequence, old_prefix.last_batch_id,
        old_prefix.source_cursor, old_prefix.status, old_prefix.batch_ids)
    monkeypatch.setattr(anchor_module, "load_typed_run_context",
                        lambda _client, _run_id: context)
    monkeypatch.setattr(anchor_module, "load_verified_v4_prefix",
                        lambda _client, _run_id: prefix)
    monkeypatch.setattr(anchor_module, "load_committed_prefix",
                        lambda *_a, **_k: pytest.fail("legacy fence used for V4"))
    monkeypatch.setattr(anchor_module, "load_latest_backtest_cursor",
                        lambda _client, verified: cursor if verified is prefix else None)
    result = load_fixed_running_prefix_anchor(
        object(), run_id=RUN, plan=_plan(), configuration_hash=CONFIG,
        account_ids=("DU1",), journal_profile="backtest_v4")
    assert result.batch_id == BATCH and result.journal_sequence == 17
    assert "arte.trading_commit_v4" in _committed_batch_filter(prefix)
    assert "arte.trading_commit_v3" not in _committed_batch_filter(prefix)


def test_v3_anchor_requires_query_certificate_before_read(monkeypatch):
    monkeypatch.setattr(anchor_module, "load_verified_squeeze_v3_prefix",
                        lambda *_a, **_k: pytest.fail("uncertified V3 read"))
    with pytest.raises(ValueError, match="pinned squeeze query"):
        load_fixed_running_prefix_anchor(
            object(), run_id=RUN, plan=_plan(), configuration_hash=CONFIG,
            account_ids=("DU1",), journal_profile="backtest_v3")


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


def test_controller_strategy_one_anchor_uses_v4_commit_identity(monkeypatch):
    controller = object.__new__(ReplayRunController)
    controller.run_id = RUN
    controller.definition = SimpleNamespace(
        mode=RunMode.BACKTEST,
        configuration_revision={"content_hash": CONFIG,
                                "payload": {"strategy": {"strategy_number": 1}}})
    controller._account_map = {"key": "DU1"}
    controller._resume_state = None
    def read(_client, **kwargs):
        assert kwargs["journal_profile"] == "backtest_v4"
        assert "expected_query_sha256" not in kwargs
        return "verified"
    monkeypatch.setattr(anchor_module, "load_fixed_running_prefix_anchor", read)
    assert controller._read_fixed_running_prefix_anchor(object(), _plan()) == "verified"
