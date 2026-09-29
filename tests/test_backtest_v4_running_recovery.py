import pytest
from dataclasses import replace
from datetime import date, datetime, timezone

from src.backend import backtest_v4_running_recovery as subject
from src.backend.backtest_strategy_one_management import StrategyOneManagementState
from src.backend.backtest_strategy_one_evidence import StrategyOneEvidenceState
from src.backend.backtest_fixed_running_anchor import FixedRunningPrefixAnchor
from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix
from src.trading_runtime.arte_oms_projection import (
    RecoveredOmsGroupState, RecoveredStrategyOneOmsLineage,
)
from src.trading_runtime.ibkr_schema import OrderRequest
from src.trading_runtime.strategy_one_broker_match_snapshot import BrokerMatchSnapshotRows
from src.trading_runtime.strategy_one_campaign_snapshot import project_campaign_snapshot


RUN = "00000000-0000-0000-0000-000000000a01"
BATCH = "00000000-0000-0000-0000-000000000a02"
PREFIX = V4CommittedPrefix(RUN, 7, BATCH, "2026-08-18:100",
                           "running", (BATCH,))


def _install(monkeypatch, *, open_orders=(), oms=(), moved=False):
    broker = BrokerMatchSnapshotRows(
        {"checkpoint_sequence": 7, "boundary_ms": 100,
         "session_date": "2026-08-18"},
        ({"account_id": "DU1"},), (), tuple(open_orders), (), ())
    monkeypatch.setattr(subject, "load_v4_running_portfolio_images",
                        lambda *_a, **_k: (PREFIX, {"DU1": {"state_hash": "a" * 64}}))
    monkeypatch.setattr(subject, "load_committed_backtest_progress",
                        lambda *_a, **_k: {"controller_time":
                                            "2026-08-18T00:00:00+00:00",
                                            "controller_processed_events": 1,
                                            "controller_warmup_events": 0,
                                            "controller_processed_frames": 3,
                                            "runtime_processed_events": 2,
                                            "runtime_last_event_time":
                                            "2026-08-18T00:00:00+00:00"})
    monkeypatch.setattr(subject, "load_attested_manager_snapshot",
                        lambda *_a, **_k: StrategyOneManagementState(100, (), (), ()))
    monkeypatch.setattr(subject, "load_attested_evidence_snapshot",
                        lambda *_a, **_k: StrategyOneEvidenceState(100, (), (), ()))
    monkeypatch.setattr(subject, "load_attested_broker_match_snapshot",
                        lambda *_a, **_k: broker)
    campaign = project_campaign_snapshot(
        run_id=RUN, session_date=date(2026, 8, 18),
        checkpoint_sequence=7, boundary_ms=100,
        journal_batch_id=BATCH, ownership=())
    monkeypatch.setattr(subject, "load_attested_campaign_snapshot",
                        lambda *_a, **_k: campaign)
    monkeypatch.setattr(subject, "load_recovered_strategy_one_oms_lineage",
                        lambda *_a, **_k: oms)
    monkeypatch.setattr(subject, "load_completed_broker_quotes",
                        lambda *_a, **_k: {})
    monkeypatch.setattr(subject, "load_verified_v4_prefix",
                        lambda *_a, **_k: None if moved else PREFIX)


def test_v4_running_recovery_joins_exact_empty_oms(monkeypatch):
    _install(monkeypatch)
    result = subject.load_v4_running_recovery_evidence(
        object(), run_id=RUN, account_ids=("DU1",),
        manager_keeper=object(), broker_keeper=object(), evidence_keeper=object(),
        market_client=object(), market_plan=object())
    assert result.prefix == PREFIX
    assert result.progress["controller_processed_events"] == 1
    assert result.manager.boundary_ms == 100
    assert result.oms == ()
    assert result.campaign.snapshot["owner_count"] == 0


def test_v4_running_recovery_rejects_missing_progress(monkeypatch):
    _install(monkeypatch)
    def missing(*_args, **kwargs):
        assert kwargs["required"] is True
        raise RuntimeError("Backtest recovery lacks one typed progress row")
    monkeypatch.setattr(subject, "load_committed_backtest_progress", missing)
    with pytest.raises(RuntimeError, match="lacks one typed progress row"):
        subject.load_v4_running_recovery_evidence(
            object(), run_id=RUN, account_ids=("DU1",),
            manager_keeper=object(), broker_keeper=object(),
            evidence_keeper=object(), market_client=object(),
            market_plan=object())


def test_v4_recovery_must_match_exact_journal_anchor(monkeypatch):
    _install(monkeypatch)
    recovery = subject.load_v4_running_recovery_evidence(
        object(), run_id=RUN, account_ids=("DU1",),
        manager_keeper=object(), broker_keeper=object(), evidence_keeper=object(),
        market_client=object(), market_plan=object())
    anchor = FixedRunningPrefixAnchor(
        RUN, BATCH, 7, PREFIX.source_cursor, "2026-08-18", 100, 1,
        datetime(2026, 8, 18, tzinfo=timezone.utc), None)
    subject.verify_v4_recovery_at_anchor(recovery, anchor)
    image = subject.reconstruct_v4_controller_image(recovery, anchor)
    assert image.source_cursor == {"session_date": "2026-08-18",
                                   "boundary_ms": 100, "sequence": 1}
    assert image.processed_frames == 3
    with pytest.raises(RuntimeError, match="runtime clock exceeds"):
        subject.reconstruct_v4_controller_image(
            replace(recovery, progress={**recovery.progress,
                                        "runtime_last_event_time":
                                        "2026-08-18T00:00:01+00:00"}), anchor)
    with pytest.raises(RuntimeError, match="differs from cold journal anchor"):
        subject.verify_v4_recovery_at_anchor(
            recovery, replace(anchor, boundary_ms=200))
    with pytest.raises(RuntimeError, match="differs from cold journal anchor"):
        subject.verify_v4_recovery_at_anchor(
            replace(recovery, progress={**recovery.progress,
                                        "controller_time": "2026-08-18T00:00:01+00:00"}),
            anchor)
    corrupted = replace(recovery.campaign, snapshot={
        **recovery.campaign.snapshot, "owner_hash": "0" * 64})
    with pytest.raises(RuntimeError, match="committed seal"):
        subject.verify_v4_recovery_at_anchor(
            replace(recovery, campaign=corrupted), anchor)


def test_v4_running_recovery_rejects_orphan_broker_order(monkeypatch):
    _install(monkeypatch, open_orders=({"broker_order_id": "missing",
                                        "client_order_id": "missing"},))
    with pytest.raises(RuntimeError, match="lacks exact OMS lineage"):
        subject.load_v4_running_recovery_evidence(
            object(), run_id=RUN, account_ids=("DU1",),
            manager_keeper=object(), broker_keeper=object(), evidence_keeper=object(),
            market_client=object(), market_plan=object())


def _open_order_lineage(*, terminal=0):
    request = OrderRequest(acctId="DU1", conid=123, orderType="LMT",
                           side="BUY", quantity=1, cOID="co-1", ticker="WFF",
                           price=10)
    group = RecoveredOmsGroupState(6, "intent-1", {"account_id": "DU1"},
                                   (request,), (), (), ({
                                       "broker_order_id": "broker-1",
                                       "request_index": 0,
                                       "terminal": terminal},), (), ())
    lineage = RecoveredStrategyOneOmsLineage(group, object(), (request,), 7)
    return lineage


def test_v4_running_recovery_joins_open_order_to_oms(monkeypatch):
    lineage = _open_order_lineage()
    _install(monkeypatch, open_orders=({"broker_order_id": "broker-1",
                                        "client_order_id": "co-1",
                                        "account_id": "DU1", "conid": 123,
                                        "ticker": "WFF"},), oms=(lineage,))
    result = subject.load_v4_running_recovery_evidence(
        object(), run_id=RUN, account_ids=("DU1",),
        manager_keeper=object(), broker_keeper=object(), evidence_keeper=object(),
        market_client=object(), market_plan=object())
    assert result.oms == (lineage,)


def test_v4_running_recovery_rejects_terminal_oms_binding(monkeypatch):
    lineage = _open_order_lineage(terminal=1)
    _install(monkeypatch, open_orders=({"broker_order_id": "broker-1",
                                        "client_order_id": "co-1",
                                        "account_id": "DU1", "conid": 123,
                                        "ticker": "WFF"},), oms=(lineage,))
    with pytest.raises(RuntimeError, match="lacks exact OMS lineage"):
        subject.load_v4_running_recovery_evidence(
            object(), run_id=RUN, account_ids=("DU1",),
            manager_keeper=object(), broker_keeper=object(), evidence_keeper=object(),
            market_client=object(), market_plan=object())


def test_v4_running_recovery_rejects_moved_prefix(monkeypatch):
    _install(monkeypatch, moved=True)
    with pytest.raises(RuntimeError, match="prefix moved"):
        subject.load_v4_running_recovery_evidence(
            object(), run_id=RUN, account_ids=("DU1",),
            manager_keeper=object(), broker_keeper=object(), evidence_keeper=object(),
            market_client=object(), market_plan=object())


def test_v4_running_recovery_rejects_mismatched_evidence_boundary(monkeypatch):
    _install(monkeypatch)
    monkeypatch.setattr(subject, "load_attested_evidence_snapshot",
                        lambda *_a, **_k: StrategyOneEvidenceState(200, (), (), ()))
    with pytest.raises(RuntimeError, match="differ from pinned accounts or cursor"):
        subject.load_v4_running_recovery_evidence(
            object(), run_id=RUN, account_ids=("DU1",),
            manager_keeper=object(), broker_keeper=object(), evidence_keeper=object(),
            market_client=object(), market_plan=object())


def test_v4_running_recovery_rejects_mismatched_campaign_boundary(monkeypatch):
    _install(monkeypatch)
    wrong = project_campaign_snapshot(
        run_id=RUN, session_date=date(2026, 8, 18),
        checkpoint_sequence=7, boundary_ms=200,
        journal_batch_id=BATCH, ownership=())
    monkeypatch.setattr(subject, "load_attested_campaign_snapshot",
                        lambda *_a, **_k: wrong)
    with pytest.raises(RuntimeError, match="differ from pinned accounts or cursor"):
        subject.load_v4_running_recovery_evidence(
            object(), run_id=RUN, account_ids=("DU1",),
            manager_keeper=object(), broker_keeper=object(), evidence_keeper=object(),
            market_client=object(), market_plan=object(), campaign_keeper=object())


def test_v4_broker_state_uses_exact_open_oms_request(monkeypatch):
    lineage = _open_order_lineage()
    _install(monkeypatch, open_orders=({"broker_order_id": "broker-1",
                                        "client_order_id": "co-1",
                                        "account_id": "DU1", "conid": 123,
                                        "ticker": "WFF"},),
             oms=(lineage,))
    evidence = subject.load_v4_running_recovery_evidence(
        object(), run_id=RUN, account_ids=("DU1",),
        manager_keeper=object(), broker_keeper=object(), evidence_keeper=object(),
        market_client=object(), market_plan=object())
    expected = {"schema_version": 4}
    def project(broker, *, requests_by_broker_id, quotes):
        assert broker is evidence.broker
        assert requests_by_broker_id == {"broker-1": lineage.orders[0]}
        assert quotes == evidence.quotes
        return expected
    monkeypatch.setattr(subject, "reconstruct_broker_match_state", project)
    assert subject.reconstruct_v4_broker_state(evidence) is expected


def test_v4_oms_image_joins_complete_protection_and_broker(monkeypatch):
    _install(monkeypatch)
    recovery = subject.load_v4_running_recovery_evidence(
        object(), run_id=RUN, account_ids=("DU1",),
        manager_keeper=object(), broker_keeper=object(), evidence_keeper=object(),
        market_client=object(), market_plan=object())
    calls = []
    history = object()
    image = object()
    monkeypatch.setattr(subject, "load_complete_typed_protection_history",
                        lambda client, prefix: calls.append((client, prefix)) or history)

    def reconstruct(oms, protection, **kwargs):
        assert oms == recovery.oms and protection is history
        assert kwargs["through_sequence"] == PREFIX.last_sequence
        assert kwargs["strategy_revision"] == 1
        return image

    monkeypatch.setattr(subject, "reconstruct_typed_oms_actor_image", reconstruct)
    monkeypatch.setattr(subject, "verify_typed_oms_broker_open_orders",
                        lambda found, broker: calls.append((found, broker)))
    assert subject.load_v4_running_oms_image(object(), recovery) is image
    assert calls[-1] == (image, recovery.broker)
    monkeypatch.setattr(subject, "load_verified_v4_prefix", lambda *_a: None)
    with pytest.raises(RuntimeError, match="prefix moved"):
        subject.load_v4_running_oms_image(object(), recovery)


def test_v4_broker_state_rejects_missing_open_oms_request(monkeypatch):
    _install(monkeypatch)
    evidence = subject.load_v4_running_recovery_evidence(
        object(), run_id=RUN, account_ids=("DU1",),
        manager_keeper=object(), broker_keeper=object(), evidence_keeper=object(),
        market_client=object(), market_plan=object())
    orphan = subject.V4RunningRecoveryEvidence(
        evidence.prefix, evidence.progress, evidence.portfolio_images,
        evidence.manager, evidence.evidence,
        BrokerMatchSnapshotRows(evidence.broker.snapshot, evidence.broker.accounts,
                                (), ({"broker_order_id": "orphan"},), (), ()),
        (), evidence.quotes, evidence.campaign)
    with pytest.raises(RuntimeError, match="lacks open OMS request"):
        subject.reconstruct_v4_broker_state(orphan)


def test_v4_broker_image_joins_committed_trades_and_rechecks_prefix(monkeypatch):
    lineage = _open_order_lineage()
    _install(monkeypatch, oms=(lineage,))
    evidence = subject.load_v4_running_recovery_evidence(
        object(), run_id=RUN, account_ids=("DU1",),
        manager_keeper=object(), broker_keeper=object(), evidence_keeper=object(),
        market_client=object(), market_plan=object())
    monkeypatch.setattr(subject, "reconstruct_v4_broker_state",
                        lambda _evidence: {"next_execution_id": 2})
    expected = [{"execution_id": "SIM-1",
                 "trade_time": "2026-08-18T08:00:00.100000+00:00"}]
    def load(client, prefix, *, requests_by_coid, coid_by_broker_id,
             next_execution_id):
        assert prefix == PREFIX
        assert requests_by_coid == {"co-1": lineage.orders[0]}
        assert coid_by_broker_id == {"broker-1": "co-1"}
        assert next_execution_id == 2
        return expected
    monkeypatch.setattr(subject, "load_v4_broker_executions", load)
    assert subject.load_v4_running_broker_image(object(), evidence) == {
        "next_execution_id": 2, "executions": expected}
    monkeypatch.setattr(subject, "load_verified_v4_prefix", lambda *_a: None)
    with pytest.raises(RuntimeError, match="prefix moved"):
        subject.load_v4_running_broker_image(object(), evidence)
