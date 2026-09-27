from __future__ import annotations

import pytest
from datetime import datetime, timezone
from types import SimpleNamespace

from src.trading_runtime import arte_live_sync_bootstrap as bootstrap
from src.trading_runtime import arte_portfolio_sync as sync
from src.trading_runtime.arte_portfolio_sync_dispatch import PortfolioSyncDispatch
from src.trading_runtime.arte_live_run_allocation import (
    LiveRunAllocator, LiveRunAllocatorHead, _HEAD,
)
from src.trading_runtime.arte_typed_insert_dispatch import TypedInsertDispatch
from src.trading_runtime.keeper_ownership import KeeperUnavailable, _ROOT
from src.trading_runtime.portfolio import PortfolioAccountProfile, PortfolioPolicy
from test_keeper_ownership import _Client, _Store


NAMESPACE = "11111111-1111-4111-8111-111111111111"
REQUEST = "22222222-2222-4222-8222-222222222222"
RUN = f"live:v2:{NAMESPACE}:{1:020d}"


class Writer:
    typed_insert_strict = True

    def __init__(self, dispatch):
        self.typed_insert_dispatch = dispatch


def _setup():
    keeper = _Client(_Store(), 11)
    keeper.ensure_path(f"{_ROOT}/live_run_allocator")
    keeper.ensure_path(f"{_ROOT}/live_run_allocation")
    keeper.create(_HEAD, LiveRunAllocatorHead(NAMESPACE, 0).wire())
    allocator = LiveRunAllocator(keeper)
    allocation = allocator.allocate(request_id=REQUEST, owner_id="live-run-controller")
    core = TypedInsertDispatch(keeper)
    sync_dispatch = PortfolioSyncDispatch(keeper)
    return keeper, core, sync_dispatch, Writer(core), allocator, allocation


def test_fresh_live_bootstrap_creates_both_gates_once(monkeypatch):
    keeper, core, sync_dispatch, writer, allocator, allocation = _setup()
    reads = []
    monkeypatch.setattr(bootstrap, "_rows", lambda _client, sql: reads.append(sql) or [])
    with pytest.raises(ValueError, match="distinct strict typed authorities"):
        bootstrap.initialize_new_live_sync_run(
            run_id=RUN, writer_client=writer, read_client=object(),
            owner_id="different-controller", core_dispatch=core,
            sync_dispatch=sync_dispatch, allocator=allocator,
            allocation=allocation)
    assert not reads
    bootstrap.initialize_new_live_sync_run(
        run_id=RUN, writer_client=writer, read_client=object(),
        owner_id="live-run-controller",
        core_dispatch=core, sync_dispatch=sync_dispatch,
        allocator=allocator, allocation=allocation)
    assert len(reads) == len(bootstrap._EXISTING_TABLES)
    assert writer.typed_sync_insert_dispatch is sync_dispatch
    assert core._read_gate(RUN)[0].mode == "open"
    assert sync_dispatch._read(RUN)[0].mode == "open"
    with pytest.raises((ValueError, KeeperUnavailable)):
        bootstrap.initialize_new_live_sync_run(
            run_id=RUN, writer_client=writer, read_client=object(),
            owner_id="live-run-controller",
            core_dispatch=core, sync_dispatch=sync_dispatch,
            allocator=allocator, allocation=allocation)


def test_existing_ch_run_and_partial_keeper_gate_fail_closed(monkeypatch):
    keeper, core, sync_dispatch, writer, allocator, allocation = _setup()
    monkeypatch.setattr(bootstrap, "_rows", lambda _client, _sql: [{"run_id": RUN}])
    with pytest.raises(KeeperUnavailable, match="already has ClickHouse facts"):
        bootstrap.initialize_new_live_sync_run(
            run_id=RUN, writer_client=writer, read_client=object(),
            owner_id="live-run-controller",
            core_dispatch=core, sync_dispatch=sync_dispatch,
            allocator=allocator, allocation=allocation)
    assert writer.__dict__.get("typed_sync_insert_dispatch") is None
    core.initialize_new_run(RUN)  # Simulates an older run lacking the sync gate.
    monkeypatch.setattr(bootstrap, "_rows", lambda _client, _sql: [])
    with pytest.raises(KeeperUnavailable, match="already exist"):
        bootstrap.initialize_new_live_sync_run(
            run_id=RUN, writer_client=writer, read_client=object(),
            owner_id="live-run-controller",
            core_dispatch=core, sync_dispatch=sync_dispatch,
            allocator=allocator, allocation=allocation)
    with pytest.raises(KeeperUnavailable, match="absent"):
        sync_dispatch._read(RUN)


def test_lost_gate_transaction_response_stays_bound_but_unattached(monkeypatch):
    keeper, core, sync_dispatch, writer, allocator, allocation = _setup()
    monkeypatch.setattr(bootstrap, "_rows", lambda _client, _sql: [])
    original = keeper.transaction
    def lost_response():
        txn = original()
        commit = txn.commit
        def lost():
            commit()
            raise TimeoutError("lost Keeper response")
        txn.commit = lost
        return txn
    monkeypatch.setattr(keeper, "transaction", lost_response)
    with pytest.raises(KeeperUnavailable, match="outcome is ambiguous"):
        bootstrap.initialize_new_live_sync_run(
            run_id=RUN, writer_client=writer, read_client=object(),
            owner_id="live-run-controller", core_dispatch=core,
            sync_dispatch=sync_dispatch, allocator=allocator,
            allocation=allocation)
    assert writer.__dict__.get("typed_sync_insert_dispatch") is None
    assert core._read_gate(RUN)[0].mode == "open"
    assert sync_dispatch._read(RUN)[0].mode == "open"
    assert allocator.load(REQUEST)[0].status == "gates_bound"


def test_cold_start_closes_sync_before_core_and_requires_live_context(monkeypatch):
    keeper, core, sync_dispatch, writer, allocator, allocation = _setup()
    monkeypatch.setattr(bootstrap, "_rows", lambda _client, _sql: [])
    bootstrap.initialize_new_live_sync_run(
        run_id=RUN, writer_client=writer, read_client=object(),
        owner_id="live-run-controller",
        core_dispatch=core, sync_dispatch=sync_dispatch,
        allocator=allocator, allocation=allocation)
    from dataclasses import replace
    bound = replace(allocation, status="context_bound", context_hash="a" * 64)
    from src.trading_runtime.arte_live_run_allocation import _receipt_path
    keeper.set(_receipt_path(REQUEST), bound.wire(), version=1)
    monkeypatch.setattr(allocator, "verify_context_bound",
                        lambda *_args: {"mode": "live"})
    keeper.load_portfolio_sync_transition_head = lambda *_args: None
    order = []
    class Base:
        def assert_fenced(self, run_id):
            assert run_id == RUN
        def verify_run_context_receipt(self, _client):
            order.append("context")
            return {"mode": "live"}
        def verify_committed_prefix(self, _client, *, journal_profile):
            assert journal_profile == "v1"
            order.append("prefix")
            return None
    def acquire(_run_id):
        assert sync_dispatch._read(RUN)[0].mode == "closed"
        order.append("core")
        return Base()
    monkeypatch.setattr(core, "acquire_cold_barrier", acquire)
    monkeypatch.setattr(sync, "_rows", lambda *_args: [])
    result = bootstrap.verify_live_sync_cold_start(
        run_id=RUN, read_client=object(), core_dispatch=core,
        sync_dispatch=sync_dispatch, keeper=keeper,
        allocator=allocator, allocation=bound)
    assert result.transition_count == 0 and order == ["core", "context", "prefix"]
    result.barrier.assert_fenced(RUN)
    with pytest.raises(KeeperUnavailable, match="cold-fenced"):
        sync_dispatch.reserve(RUN, "DU1", 1)


def test_cold_start_missing_sync_gate_never_claims_recovery():
    keeper, core, sync_dispatch, _writer, allocator, allocation = _setup()
    core.initialize_new_run(RUN)
    from dataclasses import replace
    from src.trading_runtime.arte_live_run_allocation import _receipt_path
    bound = replace(allocation, status="context_bound", context_hash="a" * 64)
    keeper.set(_receipt_path(REQUEST), bound.wire(), version=0)
    with pytest.raises(KeeperUnavailable, match="absent"):
        bootstrap.verify_live_sync_cold_start(
            run_id=RUN, read_client=object(), core_dispatch=core,
            sync_dispatch=sync_dispatch, keeper=keeper,
            allocator=allocator, allocation=bound)


def test_cold_start_nonlive_context_stays_closed(monkeypatch):
    keeper, core, sync_dispatch, writer, allocator, allocation = _setup()
    monkeypatch.setattr(bootstrap, "_rows", lambda _client, _sql: [])
    bootstrap.initialize_new_live_sync_run(
        run_id=RUN, writer_client=writer, read_client=object(),
        owner_id="live-run-controller",
        core_dispatch=core, sync_dispatch=sync_dispatch,
        allocator=allocator, allocation=allocation)
    from dataclasses import replace
    from src.trading_runtime.arte_live_run_allocation import _receipt_path
    bound = replace(allocation, status="context_bound", context_hash="a" * 64)
    keeper.set(_receipt_path(REQUEST), bound.wire(), version=1)
    monkeypatch.setattr(allocator, "verify_context_bound",
                        lambda *_args: {"mode": "backtest"})
    class Base:
        def verify_run_context_receipt(self, _client):
            return {"mode": "backtest"}
    monkeypatch.setattr(core, "acquire_cold_barrier", lambda _run_id: Base())
    with pytest.raises(KeeperUnavailable, match="not live"):
        bootstrap.verify_live_sync_cold_start(
            run_id=RUN, read_client=object(), core_dispatch=core,
            sync_dispatch=sync_dispatch, keeper=keeper,
            allocator=allocator, allocation=bound)
    assert sync_dispatch._read(RUN)[0].mode == "closed"


def test_live_portfolio_cold_handoff_uses_latest_attested_account_heads(monkeypatch):
    from src.trading_runtime import arte_portfolio_recovery as recovery_module
    from src.trading_runtime.arte_portfolio_sync import KeeperSyncTransitionHead

    checked = []
    class Barrier:
        def assert_fenced(self, run_id):
            assert run_id == RUN
            checked.append("fence")

    head = (KeeperSyncTransitionHead(RUN, "DU1", 2, "a" * 64, 3), 4)
    keeper = SimpleNamespace(load_portfolio_sync_transition_head=lambda *_: head)
    cold = bootstrap.LiveSyncColdResult(
        RUN, 2, {"mode": "live", "account_ids": ("DU1",)}, None, Barrier())
    profile = PortfolioAccountProfile("paper-key", "DU1", "live", "cash",
                                      PortfolioPolicy())
    monkeypatch.setattr(bootstrap, "load_attested_portfolio_sync_transition",
                        lambda *_args, **kwargs: checked.append(
                            (kwargs["account_id"], kwargs["state_revision"])))
    marker = object()
    def recover(_client, **kwargs):
        assert kwargs["state_revisions"] == {"DU1": 3}
        assert kwargs["profiles"] == (profile,)
        return marker
    monkeypatch.setattr(recovery_module, "recover_portfolio_engine_state", recover)
    assert bootstrap.recover_attested_live_portfolio(
        cold=cold, read_client=object(), keeper=keeper, profiles=(profile,),
        cutoff_at=datetime(2026, 8, 18, tzinfo=timezone.utc)) is marker
    assert checked == ["fence", ("DU1", 3), "fence"]


def test_live_portfolio_cold_handoff_rejects_missing_or_changed_head(monkeypatch):
    from src.trading_runtime import arte_portfolio_recovery as recovery_module
    from src.trading_runtime.arte_portfolio_sync import KeeperSyncTransitionHead

    class Barrier:
        def assert_fenced(self, _run_id):
            return None

    cold = bootstrap.LiveSyncColdResult(
        RUN, 1, {"mode": "live", "account_ids": ("DU1",)}, None, Barrier())
    profile = PortfolioAccountProfile("paper-key", "DU1", "live", "cash",
                                      PortfolioPolicy())
    heads = iter((None,))
    keeper = SimpleNamespace(load_portfolio_sync_transition_head=lambda *_: next(heads))
    cutoff = datetime(2026, 8, 18, tzinfo=timezone.utc)
    with pytest.raises(RuntimeError, match="lacks an attested"):
        bootstrap.recover_attested_live_portfolio(
            cold=cold, read_client=object(), keeper=keeper,
            profiles=(profile,), cutoff_at=cutoff)

    old = (KeeperSyncTransitionHead(RUN, "DU1", 1, "a" * 64, 1), 3)
    new = (KeeperSyncTransitionHead(RUN, "DU1", 2, "b" * 64, 2), 4)
    heads = iter((old, new))
    monkeypatch.setattr(bootstrap, "load_attested_portfolio_sync_transition",
                        lambda *_args, **_kwargs: None)
    monkeypatch.setattr(recovery_module, "recover_portfolio_engine_state",
                        lambda *_args, **_kwargs: object())
    with pytest.raises(RuntimeError, match="changed during recovery"):
        bootstrap.recover_attested_live_portfolio(
            cold=cold, read_client=object(), keeper=keeper,
            profiles=(profile,), cutoff_at=cutoff)


def test_strategy_one_live_oms_heads_are_fenced_and_strategy_pinned(monkeypatch):
    from src.trading_runtime import arte_oms_projection as oms
    from src.trading_runtime.strategy_one_contract import STRATEGY_ID

    calls = []
    class Barrier:
        def assert_fenced(self, run_id):
            assert run_id == RUN
            calls.append("fence")

    cold = bootstrap.LiveSyncColdResult(
        RUN, 1, {"mode": "live", "account_ids": ("DU1",)},
        object(), Barrier())
    row = SimpleNamespace(group={
        "account_id": "DU1", "strategy_id": STRATEGY_ID,
        "strategy_revision": 1})
    monkeypatch.setattr(oms, "load_latest_committed_oms_groups",
                        lambda _client, _prefix: calls.append("read") or (row,))
    assert bootstrap.recover_strategy_one_live_oms(
        cold=cold, read_client=object()) == (row,)
    assert calls == ["fence", "read", "fence"]
    row.group["strategy_revision"] = 2
    with pytest.raises(RuntimeError, match="differs from Strategy 1"):
        bootstrap.recover_strategy_one_live_oms(
            cold=cold, read_client=object())
