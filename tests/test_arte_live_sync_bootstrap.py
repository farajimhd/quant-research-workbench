from __future__ import annotations

import asyncio
import pytest
from datetime import datetime, timezone
from dataclasses import replace
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
from src.trading_runtime.strategy_one_contract import STRATEGY_ID
from src.backend.backtest_strategy_one_configuration import CertifiedStrategyOneConfiguration
from test_keeper_ownership import _Client, _Store


NAMESPACE = "11111111-1111-4111-8111-111111111111"
REQUEST = "22222222-2222-4222-8222-222222222222"
RUN = f"live:v2:{NAMESPACE}:{1:020d}"


def _release():
    return CertifiedStrategyOneConfiguration(
        REQUEST, "a" * 64, "b" * 64, "candidate-350", "c" * 64,
        "d" * 64, {"strategy": {
            "strategy_id": STRATEGY_ID, "strategy_number": 1,
            "revision": 1, "execution_interval": "100ms"},
            "run_plan": {"run_plan_id": "plan-1"}})


def _cold_admission_pair():
    transport = object()
    return (SimpleNamespace(keeper=transport),
            bootstrap.KeeperAdmissionEpochAuthority(
                SimpleNamespace(_client=transport)))


def _live_context():
    run = dict(run_id=RUN, run_month="2026-08-01", mode="live",
               evaluation_interval_ms=100, session_date="2026-08-18",
               configuration_hash="a" * 64, code_hash="b" * 64,
               market_plan_token="", started_at="2026-08-18T08:00:00+00:00")
    config = dict(strategy_id=STRATEGY_ID, strategy_revision=1,
                  anchor_date="2026-08-18", run_plan_id="plan-1",
                  safety_supervisor_enabled=True,
                  checkpoint_interval_events=100,
                  write_progress_checkpoints=True)
    return run, config


def test_live_context_is_typed_and_bound_to_numbered_release():
    run, config = _live_context()
    assert bootstrap.validate_new_strategy_one_live_context(
        run, config, ("DU1",), _release()) == RUN


@pytest.mark.parametrize("run_change,config_change,accounts", [
    ({"mode": "backtest"}, {}, ("DU1",)),
    ({"evaluation_interval_ms": 200}, {}, ("DU1",)),
    ({"configuration_hash": "f" * 64}, {}, ("DU1",)),
    ({"run_month": "2026-09-01"}, {}, ("DU1",)),
    ({}, {"strategy_revision": 2}, ("DU1",)),
    ({}, {"run_plan_id": "wrong"}, ("DU1",)),
    ({}, {"anchor_date": "2026-08-19"}, ("DU1",)),
    ({}, {"safety_supervisor_enabled": False}, ("DU1",)),
    ({}, {}, ("DU1", "DU1")),
    ({}, {}, ()),
])
def test_live_context_rejects_unapproved_identity_or_account_scope(
    run_change, config_change, accounts,
):
    run, config = _live_context()
    with pytest.raises(ValueError, match="Strategy 1 live"):
        bootstrap.validate_new_strategy_one_live_context(
            {**run, **run_change}, {**config, **config_change},
            accounts, _release())


def test_approved_live_context_publishes_after_approval_and_binds_keeper(
    monkeypatch,
):
    keeper, core, sync_dispatch, writer, allocator, allocation = _setup()
    run, config = _live_context()
    context = {"run_id": RUN, "mode": "live", "account_ids": ("DU1",)}
    calls = []
    monkeypatch.setattr(bootstrap, "_rows", _empty_facts)
    monkeypatch.setattr(bootstrap, "certify_strategy_one_configuration",
                        lambda _client: _release())
    def approval(*_args, **_kwargs):
        calls.append("approval")
        return {"content_hash": "e" * 64}
    def publish_run(_writer, row):
        assert core._read_gate(RUN)[0].mode == "open"
        assert sync_dispatch._read(RUN)[0].mode == "open"
        assert row == run
        calls.append("run")
    def publish_context(_writer, *, run_id, config, account_ids):
        assert (run_id, account_ids) == (RUN, ("DU1",))
        calls.append("context")
    def bind(_client, _dispatch, receipt):
        assert receipt.status == "gates_bound"
        calls.append("bind")
        return replace(receipt, status="context_bound", context_hash="f" * 64)
    monkeypatch.setattr(bootstrap, "verify_selected_approval", approval)
    monkeypatch.setattr(bootstrap, "publish_typed_run", publish_run)
    monkeypatch.setattr(bootstrap, "publish_typed_run_context", publish_context)
    monkeypatch.setattr(allocator, "bind_context", bind)
    monkeypatch.setattr(allocator, "verify_context_bound", lambda *_args: context)
    monkeypatch.setattr(bootstrap, "load_typed_run_context", lambda *_args: context)
    bound = bootstrap.publish_new_strategy_one_live_context(
        run=run, config=config, account_ids=("DU1",), release=_release(),
        approval_reader=object(), writer_client=writer, read_client=object(),
        terminal_client=object(), owner_id="live-run-controller",
        core_dispatch=core, sync_dispatch=sync_dispatch,
        allocator=allocator, allocation=allocation)
    assert bound.status == "context_bound"
    assert calls == ["approval", "run", "context", "bind", "approval"]


def test_live_publication_rejects_changed_terminal_context(monkeypatch):
    keeper, core, sync_dispatch, writer, allocator, allocation = _setup()
    run, config = _live_context()
    monkeypatch.setattr(bootstrap, "_rows", _empty_facts)
    monkeypatch.setattr(bootstrap, "certify_strategy_one_configuration",
                        lambda _client: _release())
    monkeypatch.setattr(bootstrap, "verify_selected_approval",
                        lambda *_args, **_kwargs: {"content_hash": "e" * 64})
    monkeypatch.setattr(bootstrap, "publish_typed_run", lambda *_args: None)
    monkeypatch.setattr(bootstrap, "publish_typed_run_context", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(allocator, "bind_context", lambda *_args: replace(
        allocation, status="context_bound", context_hash="f" * 64))
    monkeypatch.setattr(allocator, "verify_context_bound", lambda *_args: {
        "run_id": RUN, "mode": "live", "account_ids": ("DU1",)})
    monkeypatch.setattr(bootstrap, "load_typed_run_context", lambda *_args: {
        "run_id": RUN, "mode": "live", "account_ids": ("WRONG",)})
    with pytest.raises(KeeperUnavailable, match="changed after publication"):
        bootstrap.publish_new_strategy_one_live_context(
            run=run, config=config, account_ids=("DU1",), release=_release(),
            approval_reader=object(), writer_client=writer, read_client=object(),
            terminal_client=object(), owner_id="live-run-controller",
            core_dispatch=core, sync_dispatch=sync_dispatch,
            allocator=allocator, allocation=allocation)


def test_live_publication_recertifies_release_before_keeper_create(monkeypatch):
    keeper, core, sync_dispatch, writer, allocator, allocation = _setup()
    run, config = _live_context()
    monkeypatch.setattr(bootstrap, "certify_strategy_one_configuration",
                        lambda _client: replace(_release(), payload_hash="f" * 64))
    with pytest.raises(ValueError, match="certified ClickHouse rows"):
        bootstrap.publish_new_strategy_one_live_context(
            run=run, config=config, account_ids=("DU1",), release=_release(),
            approval_reader=object(), writer_client=writer, read_client=object(),
            terminal_client=object(), owner_id="live-run-controller",
            core_dispatch=core, sync_dispatch=sync_dispatch,
            allocator=allocator, allocation=allocation)
    with pytest.raises(KeeperUnavailable, match="absent"):
        core._read_gate(RUN)


def test_cold_preparation_composes_approval_recovery_and_broker_audit(monkeypatch):
    release = _release()
    calls = []
    core, admission = _cold_admission_pair()

    class Barrier:
        def assert_fenced(self, run_id):
            assert run_id == RUN
            calls.append("fenced")

    cold = bootstrap.LiveSyncColdResult(
        RUN, 1, {"mode": "live", "configuration_hash": release.payload_hash,
                 "strategy_id": STRATEGY_ID, "strategy_revision": 1,
                 "evaluation_interval_ms": 100, "code_hash": "b" * 64,
                 "run_plan_id": "plan-1",
                 "anchor_date": "2026-08-18", "session_date": "2026-08-18"},
        object(), Barrier())
    portfolio = object()
    audit = bootstrap.StrategyOneColdBrokerAudit(object(), object())
    monkeypatch.setattr(bootstrap, "certify_strategy_one_configuration",
                        lambda _client: calls.append("release") or release)
    monkeypatch.setattr(bootstrap, "verify_selected_approval",
                        lambda *_args, **_kwargs: calls.append("approval") or {
                            "approval_id": "selected"})
    monkeypatch.setattr(bootstrap, "verify_live_sync_cold_start",
                        lambda **_kwargs: calls.append("cold") or cold)
    def audit_admissions(_reader, authority, run_id, *, quiescence):
        assert authority is admission and run_id == RUN
        assert quiescence is cold.barrier
        calls.append("admission")
        return 2

    monkeypatch.setattr(bootstrap, "audit_attested_admission_revisions",
                        audit_admissions)
    monkeypatch.setattr(bootstrap, "recover_attested_live_portfolio",
                        lambda **_kwargs: calls.append("portfolio") or portfolio)
    monkeypatch.setattr(bootstrap, "recover_strategy_one_live_oms",
                        lambda **_kwargs: calls.append("oms") or ())
    async def broker_audit(**_kwargs):
        calls.append("broker")
        return audit
    monkeypatch.setattr(bootstrap, "audit_recovered_strategy_one_live_oms",
                        broker_audit)
    prepared = asyncio.run(bootstrap.prepare_strategy_one_live_cold_start(
        run_id=RUN, read_client=object(), core_dispatch=core,
        sync_dispatch=object(), keeper=object(), allocator=object(),
        allocation=object(), release=release,
        admission_authority=admission,
        approval_reader=SimpleNamespace(read_head=lambda _mode: None),
        profiles=(), cutoff_at=datetime.now(timezone.utc), broker=object(),
        expected_code_hash="b" * 64))
    assert prepared == bootstrap.StrategyOneLiveColdPreparation(
        cold, portfolio, (), audit, 2)
    assert calls == ["cold", "release", "approval", "admission", "portfolio", "oms",
                     "broker", "fenced", "release", "approval", "fenced"]


def test_cold_preparation_rejects_changed_approval_after_broker_audit(monkeypatch):
    release = _release()
    core, admission = _cold_admission_pair()
    selected = iter(({"approval_id": "selected"}, {"approval_id": "revoked"}))
    monkeypatch.setattr(bootstrap, "certify_strategy_one_configuration",
                        lambda _client: release)
    monkeypatch.setattr(bootstrap, "verify_selected_approval",
                        lambda *_args, **_kwargs: next(selected))
    cold = bootstrap.LiveSyncColdResult(
        RUN, 1, {"mode": "live", "configuration_hash": release.payload_hash,
                 "strategy_id": STRATEGY_ID, "strategy_revision": 1,
                 "evaluation_interval_ms": 100, "code_hash": "b" * 64,
                 "run_plan_id": "plan-1",
                 "anchor_date": "2026-08-18", "session_date": "2026-08-18"},
        object(),
        SimpleNamespace(assert_fenced=lambda _run_id: None))
    monkeypatch.setattr(bootstrap, "verify_live_sync_cold_start",
                        lambda **_kwargs: cold)
    monkeypatch.setattr(bootstrap, "audit_attested_admission_revisions",
                        lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(bootstrap, "recover_attested_live_portfolio",
                        lambda **_kwargs: object())
    monkeypatch.setattr(bootstrap, "recover_strategy_one_live_oms",
                        lambda **_kwargs: ())
    async def broker_audit(**_kwargs):
        return bootstrap.StrategyOneColdBrokerAudit(object(), object())
    monkeypatch.setattr(bootstrap, "audit_recovered_strategy_one_live_oms",
                        broker_audit)
    with pytest.raises(RuntimeError, match="approval changed"):
        asyncio.run(bootstrap.prepare_strategy_one_live_cold_start(
            run_id=RUN, read_client=object(), core_dispatch=core,
            sync_dispatch=object(), keeper=object(), allocator=object(),
            allocation=object(), release=release,
            admission_authority=admission,
            approval_reader=SimpleNamespace(read_head=lambda _mode: None),
            profiles=(), cutoff_at=datetime.now(timezone.utc), broker=object(),
            expected_code_hash="b" * 64))


def test_cold_preparation_rejects_admission_proof_gap_before_recovery(monkeypatch):
    release = _release()
    core, admission = _cold_admission_pair()
    cold = bootstrap.LiveSyncColdResult(
        RUN, 0, {"mode": "live", "configuration_hash": release.payload_hash,
                 "strategy_id": STRATEGY_ID, "strategy_revision": 1,
                 "evaluation_interval_ms": 100, "code_hash": "b" * 64,
                 "run_plan_id": "plan-1", "anchor_date": "2026-08-18",
                 "session_date": "2026-08-18"}, object(),
        SimpleNamespace(assert_fenced=lambda _run_id: None))
    monkeypatch.setattr(bootstrap, "verify_live_sync_cold_start",
                        lambda **_kwargs: cold)
    monkeypatch.setattr(bootstrap, "certify_strategy_one_configuration",
                        lambda _client: release)
    monkeypatch.setattr(bootstrap, "verify_selected_approval",
                        lambda *_args, **_kwargs: {"approval_id": "selected"})
    monkeypatch.setattr(bootstrap, "audit_attested_admission_revisions",
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(
                            RuntimeError("admission proof gap")))
    monkeypatch.setattr(bootstrap, "recover_attested_live_portfolio",
                        lambda **_kwargs: pytest.fail(
                            "admission gap reached portfolio recovery"))
    with pytest.raises(RuntimeError, match="admission proof gap"):
        asyncio.run(bootstrap.prepare_strategy_one_live_cold_start(
            run_id=RUN, read_client=object(), core_dispatch=core,
            sync_dispatch=object(), keeper=object(), allocator=object(),
            allocation=object(), admission_authority=admission,
            release=release,
            approval_reader=SimpleNamespace(read_head=lambda _mode: None),
            profiles=(), cutoff_at=datetime.now(timezone.utc), broker=object(),
            expected_code_hash="b" * 64))


def test_cold_preparation_rejects_untyped_core_dispatch_before_recovery(monkeypatch):
    monkeypatch.setattr(bootstrap, "verify_live_sync_cold_start",
                        lambda **_kwargs: pytest.fail("cold recovery reached"))
    with pytest.raises(ValueError, match="typed approved release"):
        asyncio.run(bootstrap.prepare_strategy_one_live_cold_start(
            run_id=RUN, read_client=object(), core_dispatch=object(),
            sync_dispatch=object(), keeper=object(), allocator=object(),
            allocation=object(), release=_release(),
            admission_authority=_cold_admission_pair()[1],
            approval_reader=SimpleNamespace(read_head=lambda _mode: None),
            profiles=(), cutoff_at=datetime.now(timezone.utc), broker=object(),
            expected_code_hash="b" * 64))


def test_cold_preparation_rejects_code_drift_before_state_recovery(monkeypatch):
    release = _release()
    core, admission = _cold_admission_pair()
    calls = []
    cold = bootstrap.LiveSyncColdResult(
        RUN, 1, {"mode": "live", "configuration_hash": release.payload_hash,
                 "strategy_id": STRATEGY_ID, "strategy_revision": 1,
                 "evaluation_interval_ms": 100, "code_hash": "f" * 64,
                 "run_plan_id": "plan-1", "anchor_date": "2026-08-18",
                 "session_date": "2026-08-18"}, object(),
        SimpleNamespace(assert_fenced=lambda _run_id: None))
    monkeypatch.setattr(bootstrap, "verify_live_sync_cold_start",
                        lambda **_kwargs: calls.append("cold") or cold)
    monkeypatch.setattr(bootstrap, "certify_strategy_one_configuration",
                        lambda _client: release)
    monkeypatch.setattr(bootstrap, "verify_selected_approval",
                        lambda *_args, **_kwargs: {"approval_id": "selected"})
    monkeypatch.setattr(bootstrap, "recover_attested_live_portfolio",
                        lambda **_kwargs: pytest.fail("portfolio recovery reached"))
    with pytest.raises(RuntimeError, match="differs from its approved release"):
        asyncio.run(bootstrap.prepare_strategy_one_live_cold_start(
            run_id=RUN, read_client=object(), core_dispatch=core,
            sync_dispatch=object(), keeper=object(), allocator=object(),
            allocation=object(), release=release,
            admission_authority=admission,
            approval_reader=SimpleNamespace(read_head=lambda _mode: None),
            profiles=(), cutoff_at=datetime.now(timezone.utc), broker=object(),
            expected_code_hash="b" * 64))
    assert calls == ["cold"]


def _empty_facts(_client, sql):
    if "FROM system.columns" in sql:
        return [{"table": name} for name in sorted(bootstrap._EXISTING_TABLES)]
    return []


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
    monkeypatch.setattr(bootstrap, "_rows", lambda client, sql: reads.append(sql) or _empty_facts(client, sql))
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
    assert len(reads) == len(bootstrap._EXISTING_TABLES) + 1
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
    monkeypatch.setattr(bootstrap, "_rows", lambda client, sql: (
        _empty_facts(client, sql) if "FROM system.columns" in sql else [{"run_id": RUN}]))
    with pytest.raises(KeeperUnavailable, match="already has ClickHouse facts"):
        bootstrap.initialize_new_live_sync_run(
            run_id=RUN, writer_client=writer, read_client=object(),
            owner_id="live-run-controller",
            core_dispatch=core, sync_dispatch=sync_dispatch,
            allocator=allocator, allocation=allocation)
    assert writer.__dict__.get("typed_sync_insert_dispatch") is None
    core.initialize_new_run(RUN)  # Simulates an older run lacking the sync gate.
    monkeypatch.setattr(bootstrap, "_rows", _empty_facts)
    with pytest.raises(KeeperUnavailable, match="already exist"):
        bootstrap.initialize_new_live_sync_run(
            run_id=RUN, writer_client=writer, read_client=object(),
            owner_id="live-run-controller",
            core_dispatch=core, sync_dispatch=sync_dispatch,
            allocator=allocator, allocation=allocation)
    with pytest.raises(KeeperUnavailable, match="absent"):
        sync_dispatch._read(RUN)


def test_fresh_live_run_rejects_orphans_in_new_typed_families(monkeypatch):
    keeper, core, sync_dispatch, writer, allocator, allocation = _setup()
    def rows(client, sql):
        if "FROM system.columns" in sql:
            return [*(_empty_facts(client, sql)),
                    {"table": "trading_strategy_signal_v1"}]
        return ([{"run_id": RUN}] if "arte.trading_strategy_signal_v1" in sql
                else [])
    monkeypatch.setattr(bootstrap, "_rows", rows)
    with pytest.raises(KeeperUnavailable, match="already has ClickHouse facts"):
        bootstrap.initialize_new_live_sync_run(
            run_id=RUN, writer_client=writer, read_client=object(),
            owner_id="live-run-controller", core_dispatch=core,
            sync_dispatch=sync_dispatch, allocator=allocator,
            allocation=allocation)
    assert writer.__dict__.get("typed_sync_insert_dispatch") is None
    with pytest.raises(KeeperUnavailable, match="absent"):
        core._read_gate(RUN)


def test_fresh_live_run_rejects_orphan_outside_trading_prefix(monkeypatch):
    keeper, core, sync_dispatch, writer, allocator, allocation = _setup()

    def rows(client, sql):
        if "FROM system.columns" in sql:
            assert "startsWith(table,'trading_')" not in sql
            return [{"table": name} for name in sorted({
                *(row["table"] for row in _empty_facts(client, sql)),
                "strategy_one_live_order_fact_v1",
            })]
        return ([{"run_id": RUN}]
                if "arte.strategy_one_live_order_fact_v1" in sql else [])

    monkeypatch.setattr(bootstrap, "_rows", rows)
    with pytest.raises(KeeperUnavailable, match="already has ClickHouse facts"):
        bootstrap.initialize_new_live_sync_run(
            run_id=RUN, writer_client=writer, read_client=object(),
            owner_id="live-run-controller", core_dispatch=core,
            sync_dispatch=sync_dispatch, allocator=allocator,
            allocation=allocation)
    assert writer.__dict__.get("typed_sync_insert_dispatch") is None
    with pytest.raises(KeeperUnavailable, match="absent"):
        core._read_gate(RUN)


@pytest.mark.parametrize("inventory", [
    [],
    [{"table": "trading_run_v1"}],
    [{"table": name} for name in sorted(bootstrap._EXISTING_TABLES)]
    + [{"table": "trading_run_v1"}],
    [{"table": name} for name in sorted(bootstrap._EXISTING_TABLES)]
    + [{"table": "trading_bad;DROP"}],
])
def test_fresh_live_run_requires_unambiguous_fact_inventory(monkeypatch, inventory):
    keeper, core, sync_dispatch, writer, allocator, allocation = _setup()
    monkeypatch.setattr(bootstrap, "_rows", lambda _client, _sql: inventory)
    with pytest.raises(KeeperUnavailable, match="inventory is incomplete or ambiguous"):
        bootstrap.initialize_new_live_sync_run(
            run_id=RUN, writer_client=writer, read_client=object(),
            owner_id="live-run-controller", core_dispatch=core,
            sync_dispatch=sync_dispatch, allocator=allocator,
            allocation=allocation)
    assert writer.__dict__.get("typed_sync_insert_dispatch") is None


def test_lost_gate_transaction_response_stays_bound_but_unattached(monkeypatch):
    keeper, core, sync_dispatch, writer, allocator, allocation = _setup()
    monkeypatch.setattr(bootstrap, "_rows", _empty_facts)
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
    monkeypatch.setattr(bootstrap, "_rows", _empty_facts)
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
    monkeypatch.setattr(bootstrap, "_rows", _empty_facts)
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
    from src.trading_runtime import arte_intent_projection as intents
    from src.trading_runtime.strategy_one_contract import STRATEGY_ID

    calls = []
    class Barrier:
        def assert_fenced(self, run_id):
            assert run_id == RUN
            calls.append("fence")

    cold = bootstrap.LiveSyncColdResult(
        RUN, 1, {"mode": "live", "account_ids": ("DU1",)},
        object(), Barrier())
    row = SimpleNamespace(tactic_recorded=True, sequence=2,
                          intent_record_id="intent-record", orders=(), group={
        "account_id": "DU1", "strategy_id": STRATEGY_ID,
        "strategy_revision": 1, "strategy_intent_id": "intent-1"})
    source = SimpleNamespace(record_id="intent-record", sequence=1,
                             account_id="DU1", intent=SimpleNamespace(
                                 intent_id="intent-1", ticker="TEST",
                                 action="enter_long", metadata={}))
    admission = {"account_id": "DU1", "intent_id": "intent-1",
                 "ticker": "TEST", "action": "enter_long"}
    decision = {"decision_id": "decision-1"}
    monkeypatch.setattr(oms, "load_latest_committed_oms_groups",
                        lambda _client, _prefix, **kwargs:
                        calls.append(("read", kwargs)) or (row,))
    monkeypatch.setattr(intents, "load_committed_strategy_intent_page",
                        lambda _client, _prefix, **kwargs:
                        calls.append(("intent", kwargs)) or (source,))
    monkeypatch.setattr(oms, "load_committed_oms_admission_page",
                        lambda _client, _prefix, groups:
                        calls.append(("admission", len(groups))) or {2: admission})
    monkeypatch.setattr(oms, "load_committed_oms_decision_page",
                        lambda _client, _prefix, groups, admissions:
                        calls.append(("decision", len(groups), len(admissions)))
                        or {2: decision})
    assert bootstrap.recover_strategy_one_live_oms(
        cold=cold, read_client=object()) == (
            bootstrap.VerifiedStrategyOneOmsHead(row, source, admission, decision),)
    assert calls == ["fence", ("read", {
        "allowed_accounts": frozenset({"DU1"}),
            "strategy_identity": (STRATEGY_ID, 1),
            "require_tactic": True}),
            ("intent", {"limit": 1, "record_ids": ("intent-record",)}),
            ("admission", 1), ("decision", 1, 1), "fence"]
    row.group["strategy_revision"] = 2
    with pytest.raises(RuntimeError, match="differs from Strategy 1"):
        bootstrap.recover_strategy_one_live_oms(
            cold=cold, read_client=object())


def test_strategy_one_live_oms_rejects_contradictory_source_intent(monkeypatch):
    from src.trading_runtime import arte_oms_projection as oms
    from src.trading_runtime import arte_intent_projection as intents
    from src.trading_runtime.strategy_one_contract import STRATEGY_ID

    class Barrier:
        def assert_fenced(self, _run_id):
            pass

    cold = bootstrap.LiveSyncColdResult(
        RUN, 1, {"mode": "live", "account_ids": ("DU1",)},
        object(), Barrier())
    group = SimpleNamespace(tactic_recorded=True, sequence=2,
                            intent_record_id="intent-record", orders=(), group={
                                "account_id": "DU1", "strategy_id": STRATEGY_ID,
                                "strategy_revision": 1,
                                "strategy_intent_id": "intent-1"})
    source = SimpleNamespace(record_id="intent-record", sequence=3,
                             account_id="DU1", intent=SimpleNamespace(
                                 intent_id="intent-1", ticker="TEST",
                                 action="enter_long", metadata={}))
    monkeypatch.setattr(oms, "load_latest_committed_oms_groups",
                        lambda *_args, **_kwargs: (group,))
    monkeypatch.setattr(intents, "load_committed_strategy_intent_page",
                        lambda *_args, **_kwargs: (source,))
    monkeypatch.setattr(oms, "load_committed_oms_admission_page",
                        lambda *_args, **_kwargs: {2: {
                            "account_id": "DU1", "intent_id": "intent-1",
                            "ticker": "TEST", "action": "enter_long"}})
    monkeypatch.setattr(oms, "load_committed_oms_decision_page",
                        lambda *_args, **_kwargs: {2: {}})
    with pytest.raises(RuntimeError, match="contradicts"):
        bootstrap.recover_strategy_one_live_oms(cold=cold, read_client=object())
    source.sequence = 1
    monkeypatch.setattr(oms, "load_committed_oms_admission_page",
                        lambda *_args, **_kwargs: {2: {
                            "account_id": "DU1", "intent_id": "intent-1",
                            "ticker": "OTHER", "action": "enter_long"}})
    with pytest.raises(RuntimeError, match="contradicts"):
        bootstrap.recover_strategy_one_live_oms(cold=cold, read_client=object())


def test_cold_oms_broker_audit_keeps_fence_across_snapshot(monkeypatch) -> None:
    from src.trading_runtime.arte_oms_broker_audit import OmsOpenBindingAudit
    from src.trading_runtime import arte_oms_fill_audit as fills

    calls = []
    class Barrier:
        def assert_fenced(self, _run_id):
            calls.append("fence")

    class Broker:
        async def live_orders(self):
            calls.append("broker")
            return []

    cold = bootstrap.LiveSyncColdResult(
        RUN, 1, {"mode": "live", "account_ids": ("DU1",)},
        object(), Barrier())
    group = SimpleNamespace(group={"account_id": "DU1"}, orders=(),
                            broker_bindings=())
    head = bootstrap.VerifiedStrategyOneOmsHead(group, object(), {}, {})
    async def recent(_client, _prefix, _heads, _broker):
        calls.append("fills")
        return fills.RecentFillAudit(0, 0, 0)
    monkeypatch.setattr(fills, "audit_strategy_one_recent_fills", recent)
    result = asyncio.run(bootstrap.audit_recovered_strategy_one_live_oms(
        cold=cold, heads=(head,), read_client=object(), broker=Broker()))
    assert result == bootstrap.StrategyOneColdBrokerAudit(
        OmsOpenBindingAudit(1, 0, 0, 0), fills.RecentFillAudit(0, 0, 0))
    assert calls == ["fence", "broker", "fills", "fence"]
