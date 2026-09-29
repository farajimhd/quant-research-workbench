"""Typed OMS actor images must be complete and side-effect free."""
from datetime import datetime, timezone

import pytest

from src.trading_runtime.arte_intent_projection import RecoveredIntent
from src.trading_runtime.arte_journal_reader import CompleteProtectionHistory
from src.trading_runtime.arte_oms_actor_restore import reconstruct_typed_oms_actor_image
from src.trading_runtime.arte_oms_projection import (
    RecoveredOmsGroupState, RecoveredStrategyOneOmsLineage,
)
from src.trading_runtime.ibkr_schema import OrderRequest
from src.trading_runtime.strategy_one_contract import STRATEGY_ID, STRATEGY_NUMBER
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
    lineage = RecoveredStrategyOneOmsLineage(state, recovered, (request,), 7)
    history = CompleteProtectionHistory(RUN, 7, ("batch-1",), ())
    return lineage, history


def test_typed_oms_actor_image_rebuilds_group_and_indexes():
    lineage, history = _source()
    image = reconstruct_typed_oms_actor_image(
        (lineage,), history, run_id=RUN,
        strategy_id=STRATEGY_ID, strategy_revision=STRATEGY_NUMBER,
        through_sequence=7, cutoff_at=AT)
    group = image.groups["group-1"]
    assert group.plan.orders == lineage.orders
    assert group.plan.broker_batches == (lineage.orders,)
    assert group.broker_order_request_indexes == {"broker-1": 0}
    assert image.group_by_client_id == {"co-1": "group-1"}
    assert image.group_by_broker_id == {"broker-1": "group-1"}
    assert image.protection_versions == {}


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
