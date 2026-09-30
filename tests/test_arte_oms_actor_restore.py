"""Typed OMS actor images must be complete and side-effect free."""
from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from src.trading_runtime.arte_intent_projection import RecoveredIntent
from src.trading_runtime.arte_journal_reader import CompleteProtectionHistory
from src.trading_runtime import arte_oms_actor_restore as restore
from src.trading_runtime.arte_oms_actor_restore import (
    attach_typed_oms_observations, install_typed_oms_actor_image,
    reconstruct_typed_oms_actor_image,
)
from src.trading_runtime.arte_oms_projection import (
    RecoveredOmsGroupState, RecoveredStrategyOneOmsLineage,
)
from src.trading_runtime.ibkr_schema import OrderRequest
from src.trading_runtime.order_management import OrderManagementEngine
from src.trading_runtime.strategy_one_contract import STRATEGY_ID, STRATEGY_NUMBER
from src.trading_runtime.strategy_one_broker_match_snapshot import BrokerMatchSnapshotRows
from src.trading_runtime.strategy_one_oms_observation_snapshot import (
    project_oms_observation_snapshot,
)
from tests.test_arte_intent_projection import intent


RUN = "backtest:one"
AT = datetime(2026, 8, 18, 8, 5, tzinfo=timezone.utc)


def _source(*, malformed=None):
    source = intent(ticker="AAA", intent_id="intent-1")
    request = OrderRequest(acctId="DU1", conid=123, cOID="co-1",
                           ticker="AAA", orderType="LMT", side="BUY",
                           quantity=5, price=10)
    row = dict(run_id=RUN, group_id="group-1", account_id="DU1",
               strategy_id=STRATEGY_ID, strategy_revision=STRATEGY_NUMBER,
               strategy_intent_id=source.intent_id, state="working",
               created_at=AT.isoformat(), updated_at=AT.isoformat(),
               submitted_at=AT.isoformat(), rejection_reason="",
               decision_to_submit_ms=None, reprice_count=0,
               last_reprice_at=None, failed_reprice_at=None,
               internal_reaction_ms=None, deferred_reprice_from=None,
               deferred_reprice_to=None, high_water_price="0",
               low_water_price="0", cancel_strategy_protection=0,
               protection_reconciliation_required=0,
               filled_quantity="0", remaining_quantity="5",
               current_limit_price="10", protection_required_quantity="0",
               protection_coverage_quantity="0", protection_delegated=0,
               order_count=1, broker_binding_count=1, warning_count=0,
               cancel_oca_count=0)
    row.update(malformed or {})
    binding = dict(broker_order_id="broker-1", request_index=0,
                   has_role=1, role="entry", has_slice=0, slice_id="",
                   has_filled_quantity=1, filled_quantity="0", terminal=0)
    state = RecoveredOmsGroupState(
        7, "record-1", row, (request,), (0,), ("",), (binding,), (), (),
        None, True)
    recovered = RecoveredIntent(6, "DU1", "record-1", "batch-1", source)
    lineage = RecoveredStrategyOneOmsLineage(
        state, recovered, (request,), 7,
        replace(source, metadata={"assignment_id": "assignment-1"}))
    history = CompleteProtectionHistory(RUN, 7, ("batch-1",), ())
    return lineage, history


def test_typed_oms_actor_image_rebuilds_group_and_indexes():
    lineage, history = _source()
    approved = replace(lineage.source_intent.intent,
                       metadata={"assignment_id": "assignment-1"}, quantity=4)
    lineage = replace(lineage, approved_intent=approved)
    image = reconstruct_typed_oms_actor_image(
        (lineage,), history, run_id=RUN,
        strategy_id=STRATEGY_ID, strategy_revision=STRATEGY_NUMBER,
        through_sequence=7, cutoff_at=AT)
    group = image.groups["group-1"]
    assert group.intent is approved
    with pytest.raises(RuntimeError, match="verified approved intent"):
        reconstruct_typed_oms_actor_image(
            (replace(lineage, approved_intent=None),), history,
            run_id=RUN, strategy_id=STRATEGY_ID,
            strategy_revision=STRATEGY_NUMBER,
            through_sequence=7, cutoff_at=AT)
    assert group.plan.orders == lineage.orders
    assert group.plan.broker_batches == (lineage.orders,)
    assert group.broker_order_request_indexes == {"broker-1": 0}
    assert image.group_by_client_id == {"co-1": "group-1"}
    assert image.group_by_broker_id == {"broker-1": "group-1"}
    assert image.protection_versions == {}


def test_typed_oms_observation_attachment_preserves_last_observed_state():
    lineage, history = _source()
    lineage = replace(lineage, approved_intent=replace(
        lineage.source_intent.intent,
        metadata={"assignment_id": "assignment-1"}, quantity=4))
    image = reconstruct_typed_oms_actor_image(
        (lineage,), history, run_id=RUN,
        strategy_id=STRATEGY_ID, strategy_revision=STRATEGY_NUMBER,
        through_sequence=7, cutoff_at=AT)
    observed = ("working", "Submitted", 0.0, 5.0, 0.0, 10.0,
                0.0, "", "", "")
    rows = project_oms_observation_snapshot(
        run_id=RUN, session_date=AT.date(), checkpoint_sequence=7,
        boundary_ms=30_000,
        groups={"group-1": type("Observed", (), {
            "broker_order_ids": ["broker-1"],
            "broker_order_state_fingerprints": {"broker-1": observed},
        })()})
    restored = attach_typed_oms_observations(image, rows, through_sequence=7)
    assert restored.groups["group-1"].broker_order_state_fingerprints == {
        "broker-1": observed}
    assert image.groups["group-1"].broker_order_state_fingerprints == {}
    live = ("Submitted", 0.0, 5.0, 0.0, 10.0, 0.0, "working")
    live_rows = project_oms_observation_snapshot(
        run_id=RUN, session_date=AT.date(), checkpoint_sequence=7,
        boundary_ms=30_000,
        groups={"group-1": type("Observed", (), {
            "broker_order_ids": ["broker-1"],
            "broker_order_state_fingerprints": {"broker-1": live},
        })()})
    live_restored = attach_typed_oms_observations(
        image, live_rows, through_sequence=7)
    assert live_restored.groups["group-1"].broker_order_state_fingerprints == {
        "broker-1": live}
    with pytest.raises(RuntimeError, match="checkpoint"):
        attach_typed_oms_observations(image, rows, through_sequence=8)
    stored, history = _source(malformed={
        "created_at": "2026-08-18 08:05:00.000000000",
        "updated_at": "2026-08-18 08:05:00.000000000",
        "submitted_at": "2026-08-18 08:05:00.000000"})
    recovered = reconstruct_typed_oms_actor_image(
        (stored,), history, run_id=RUN,
        strategy_id=STRATEGY_ID, strategy_revision=STRATEGY_NUMBER,
        through_sequence=7, cutoff_at=AT)
    assert recovered.groups["group-1"].created_at == AT


def test_typed_oms_image_installs_only_into_matching_fresh_actor():
    lineage, history = _source()
    image = reconstruct_typed_oms_actor_image(
        (lineage,), history, run_id=RUN,
        strategy_id=STRATEGY_ID, strategy_revision=STRATEGY_NUMBER,
        through_sequence=7, cutoff_at=AT)
    def actor(run_id=RUN):
        return OrderManagementEngine(
            broker=MagicMock(), planner=MagicMock(), risk=MagicMock(),
            journal=MagicMock(), run_id=run_id, strategy_id=STRATEGY_ID,
            strategy_revision=STRATEGY_NUMBER)
    restored = actor()
    install_typed_oms_actor_image(restored, image)
    assert set(restored._groups) == set(image.groups)
    assert restored._groups["group-1"].snapshot(restored.policy.version) == (
        image.groups["group-1"].snapshot(restored.policy.version))
    assert restored._groups["group-1"] is not image.groups["group-1"]
    restored._groups["group-1"].broker_order_ids.append("new-reply")
    assert image.groups["group-1"].broker_order_ids == ["broker-1"]
    with pytest.raises(RuntimeError, match="fresh"):
        install_typed_oms_actor_image(restored, image)
    with pytest.raises(RuntimeError, match="identity"):
        install_typed_oms_actor_image(actor("other-run"), image)


def test_typed_oms_actor_image_rejects_incomplete_contract():
    lineage, history = _source(malformed={"order_count": 2})
    with pytest.raises(RuntimeError, match="committed lineage"):
        reconstruct_typed_oms_actor_image(
            (lineage,), history, run_id=RUN,
            strategy_id=STRATEGY_ID, strategy_revision=STRATEGY_NUMBER,
            through_sequence=7, cutoff_at=AT)
    lineage, history = _source(malformed={"updated_at": "2026-08-18T08:06:00+00:00"})
    with pytest.raises(RuntimeError, match="recovery boundary"):
        reconstruct_typed_oms_actor_image(
            (lineage,), history, run_id=RUN,
            strategy_id=STRATEGY_ID, strategy_revision=STRATEGY_NUMBER,
            through_sequence=7, cutoff_at=AT)
    lineage, history = _source(malformed={"created_at": "2026-08-18 08:05:00"})
    with pytest.raises(RuntimeError, match="lacks UTC authority"):
        reconstruct_typed_oms_actor_image(
            (lineage,), history, run_id=RUN,
            strategy_id=STRATEGY_ID, strategy_revision=STRATEGY_NUMBER,
            through_sequence=7, cutoff_at=AT)


def test_typed_oms_image_cross_checks_broker_open_set(monkeypatch):
    lineage, history = _source()
    image = reconstruct_typed_oms_actor_image(
        (lineage,), history, run_id=RUN,
        strategy_id=STRATEGY_ID, strategy_revision=STRATEGY_NUMBER,
        through_sequence=7, cutoff_at=AT)
    monkeypatch.setattr(restore, "verify_broker_match_snapshot", lambda row: row)
    from src.trading_runtime.strategy_one_broker_match_snapshot import float64_bits

    order = dict(broker_order_id="broker-1", client_order_id="co-1",
                 account_id="DU1", conid=123, ticker="AAA",
                 filled_f64_bits=float64_bits(0, "filled"))
    broker = BrokerMatchSnapshotRows({}, (), (), (order,), (), ())
    assert restore.verify_typed_oms_broker_open_orders(image, broker) == 1
    with pytest.raises(RuntimeError, match="open order identities"):
        restore.verify_typed_oms_broker_open_orders(
            image, BrokerMatchSnapshotRows({}, (), (), (), (), ()))
    with pytest.raises(RuntimeError, match="fill quantity"):
        restore.verify_typed_oms_broker_open_orders(
            image, BrokerMatchSnapshotRows({}, (), (),
                                            ({**order, "filled_f64_bits":
                                              float64_bits(1, "filled")},), (), ()))
