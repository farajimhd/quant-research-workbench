"""Pure V4 compound preparation before any ClickHouse or Keeper mutation."""
from dataclasses import replace
from datetime import date, datetime, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest

from src.trading_runtime.arte_journal_compound_v4 import (
    coalesce_v4_units, prepare_compound_v4_families, publish_compound_v4,
)
from src.trading_runtime.arte_journal_commit_v4 import load_verified_v4_prefix
from src.backend.backtest_typed_publisher import (
    _coalesce_v4_units, _committed_intent_source,
)
from src.trading_runtime.arte_journal_writer import TypedJournalBatch, typed_row
from src.trading_runtime.arte_intent_projection import strategy_intent_batch
from src.trading_runtime.arte_oms_projection import (
    load_committed_oms_group_state_page, oms_group_state_batch,
)
from src.trading_runtime.arte_oms_tactic_projection import (
    V4OmsTacticBatch, tactic_rows,
)
from src.trading_runtime.arte_typed_insert_dispatch import TypedInsertDispatch
from src.trading_runtime.arte_portfolio_allocation_v4 import V4PortfolioAllocationBatch
from src.trading_runtime.arte_broker_acknowledgement_v4 import (
    ACKNOWLEDGEMENT, broker_acknowledgement_batch_v4,
)
from src.trading_runtime.journal_contract import JournalRecord
from src.trading_runtime.ibkr_schema import OrderRequest
from src.trading_runtime.order_management import (
    _ManagedOrderGroup, OrderManagementState, ExecutionQuote,
    ExecutionTactic, ExecutionUrgency, PriceStep,
)
from src.trading_runtime.strategy_orders import StrategyOrderPlan
from src.trading_runtime.arte_protection_reconciliation_v4 import (
    V4ProtectionReconciliationBatch,
)
from tests.test_arte_journal_writer import batch
from tests.test_arte_journal_commit_v4 import attached_v4_client
from tests.test_arte_intent_projection import intent


RUN = str(UUID(int=1))
ATTEMPT = str(UUID(int=2))
NIL = str(UUID(int=0))


def _base(sequence, batch_number, prior_number, *, kind=("risk", "continuous_risk_state")):
    batch_id = str(UUID(int=batch_number))
    return TypedJournalBatch(
        RUN, date(2026, 8, 1), ATTEMPT, batch_id,
        str(UUID(int=prior_number)), sequence, sequence, "start", "running",
        ({"record_id": str(UUID(int=100 + sequence)), "run_id": RUN,
          "batch_id": batch_id, "category": kind[0],
          "entity_type": kind[1], "sequence": sequence},),
    )


def test_compound_rekeys_mixed_scalar_children_without_mutating_sources():
    first = _base(1, 11, 0)
    second = _base(2, 12, 11,
                   kind=("portfolio_management", "portfolio_allocation"))
    allocation = {"record_id": second.events[0]["record_id"], "run_id": RUN,
                  "batch_id": second.batch_id, "content_hash": "old-seal"}
    result = coalesce_v4_units((first, V4PortfolioAllocationBatch(second, allocation)))

    assert result.base.batch_id == second.batch_id
    assert (result.base.first_sequence, result.base.last_sequence) == (1, 2)
    assert {row["batch_id"] for row in result.base.events} == {second.batch_id}
    assert result.children["allocations"] == ({
        "record_id": second.events[0]["record_id"], "run_id": RUN,
        "batch_id": second.batch_id},)
    assert first.events[0]["batch_id"] == first.batch_id
    assert allocation["content_hash"] == "old-seal"


def test_compound_retains_qualified_rule_rows_and_reseals_dependent_child_hashes(monkeypatch):
    from tests.test_rule_decision_basis_projection import values
    from src.trading_runtime.signals import StrategySignal
    from src.trading_runtime.arte_journal_projection import project_journal_record
    from src.trading_runtime.rule_decision_basis_projection import (
        project_rule_decision_basis, RuleDecisionBasisBatch, rule_decision_semantic_hash, rekey_rule_decision_basis)
    from src.trading_runtime.rule_decision_publication import QualifiedRuleDecisionBatch
    from src.backend.rule_decision_reselection import QualifiedRuleDecision, _NativeDecisionEvidence, _NATIVE_DECISION_PROOF
    scope = {**values(), "run_id": RUN, "basis_sequence": 1, "decision_sequence": 3,
        "batch_id": str(UUID(int=32))}
    at = scope["boundary_at"]
    signal = StrategySignal(scope["decision_id"], "program_boundary_completed", "ABC", at,
        "wait", "neutral", 0., 1., "program:selected", working_timeframe="1s",
        metadata=dict(assignment_id="assignment", reference_price=10., status="completed",
            reason_code="program:selected", reason_detail="selected",
            correlation_id=scope["decision_id"], causation_id=scope["source_commit_hash"]))
    record = JournalRecord(scope["record_id"], RUN, 3, at, at, "strategy_decision", "signal",
        scope["decision_id"], "account", {**signal.payload(), "strategy_id": "program", "strategy_revision": 1,
            "correlation_id": scope["decision_id"], "causation_id": scope["source_commit_hash"]})
    base = project_journal_record(record, run_month=scope["event_month"], attempt_id=ATTEMPT,
        batch_id=scope["batch_id"], prior_batch_id=str(UUID(int=31)), source_cursor="start")
    parent, children = project_rule_decision_basis(scope, [dict(stage="entry", strategy_intent_id="intent")])
    digest = rule_decision_semantic_hash(parent, children)
    # Transport fixture only: native policy qualification is tested separately.
    evidence = _NativeDecisionEvidence(RUN, 1, digest, _NATIVE_DECISION_PROOF)
    qualified = QualifiedRuleDecision(RUN, 1, digest, None, evidence)
    first = QualifiedRuleDecisionBatch(RuleDecisionBasisBatch(base, parent, children), qualified)
    second = _base(4, 33, 32)
    result = coalesce_v4_units((first, second), max_events=2)
    expected_parent, expected_children = rekey_rule_decision_basis(parent, children, batch_id=second.batch_id)
    assert result.children["rule_decision_bases"] == ({key: value for key, value in expected_parent.items() if key != "content_hash"},)
    assert result.children["rule_decision_intents"] == tuple({key: value for key, value in row.items() if key != "content_hash"}
        for row in expected_children)
    assert result.units[0] is first and first.unit.basis["batch_id"] == base.batch_id
    assert expected_parent["intent_hash"] != parent["intent_hash"]
    from dataclasses import replace
    from src.trading_runtime.rule_decision_compound import qualified_compound_rule_families
    from src.trading_runtime.rule_decision_basis_schema import BASIS, INTENT
    assert qualified_compound_rule_families(result) == (
        (BASIS.name, (expected_parent,)), (INTENT.name, expected_children))
    from src.trading_runtime.rule_decision_compound import verify_compound_rule_readback
    stored = qualified_compound_rule_families(result)
    verify_compound_rule_readback(result, tuple(reversed(stored)))
    with pytest.raises(ValueError, match="coverage"):
        verify_compound_rule_readback(result, stored[:1])
    with pytest.raises(ValueError, match="repeated"):
        verify_compound_rule_readback(result, (*stored, stored[0]))
    damaged = ({**expected_parent, "intent_hash": "0" * 64},)
    with pytest.raises(ValueError, match="scalar hash"):
        verify_compound_rule_readback(result, ((BASIS.name, damaged), stored[1]))
    from src.trading_runtime.rule_decision_basis_projection import _seal
    forged = _seal(BASIS, {**{key: value for key, value in expected_parent.items() if key != "content_hash"},
                           "intent_hash": "0" * 64})
    with pytest.raises(ValueError, match="qualified decision evidence"):
        verify_compound_rule_readback(result, ((BASIS.name, (forged,)), stored[1]))
    tampered = dict(result.children)
    tampered["rule_decision_bases"] = ({**tampered["rule_decision_bases"][0], "intent_hash": "0" * 64},)
    with pytest.raises(ValueError, match="qualified source units"):
        qualified_compound_rule_families(replace(result, children=tampered))
    from src.trading_runtime import arte_journal_compound_v4 as compound_module
    from src.trading_runtime.rule_decision_compound import prepare_qualified_compound_families
    calls = []
    def prepare_ordinary(client, ordinary, **context):
        assert not ordinary.children["rule_decision_bases"]
        assert not ordinary.children["rule_decision_intents"]
        assert ordinary.units == (first.base, second)
        assert ordinary.base is result.base
        assert context == dict(verified_prior_prefix="prefix", first_price_source="source")
        calls.append(True)
        return (("base", ()),), (("ordinary", ()),)
    monkeypatch.setattr(compound_module, "prepare_compound_v4_families", prepare_ordinary)
    assert prepare_qualified_compound_families(object(), result,
        verified_prior_prefix="prefix", first_price_source="source") == (
            (("base", ()),), (("ordinary", ()), (BASIS.name, (expected_parent,)),
                             (INTENT.name, expected_children)))
    assert calls == [True]
    from src.trading_runtime.arte_journal_writer import _sealed_families, _v4_family_table
    second_real = project_journal_record(replace(record, sequence=4, record_id=str(UUID(int=34))),
        run_month=scope["event_month"], attempt_id=ATTEMPT, batch_id=str(UUID(int=33)),
        prior_batch_id=base.batch_id, source_cursor="start")
    real_compound = coalesce_v4_units((first, second_real), max_events=2)
    ordinary_families = tuple((_v4_family_table(name), rows) for name, rows in _sealed_families(real_compound.base) if rows)
    monkeypatch.setattr(compound_module, "prepare_compound_v4_families",
                        lambda *args, **kwargs: (ordinary_families, ordinary_families))
    from src.trading_runtime.rule_decision_compound import prepare_qualified_compound_commit
    from src.trading_runtime.rule_decision_publication import verify_rule_decision_commit_readback
    committed = prepare_qualified_compound_commit(object(), real_compound, committed_at=at)
    assert committed.commit["first_sequence"] == first.base.first_sequence
    assert committed.commit["last_sequence"] == second.last_sequence
    assert {name for name, _ in committed.families} == {name for name, _ in ordinary_families} | {BASIS.name, INTENT.name}
    from src.trading_runtime.arte_journal_writer import _CONTRACTS, _datetime_wire
    persisted = []
    for name, rows in committed.families:
        converted = []
        for row in rows:
            stored_row = dict(row)
            for column, kind in _CONTRACTS[name].columns:
                if kind.startswith("DateTime64"):
                    precision = int(kind.split("(")[1].split(",")[0])
                    stored_row[column] = _datetime_wire(stored_row[column], precision)
            converted.append(stored_row)
        persisted.append((name, tuple(converted)))
    verify_rule_decision_commit_readback(committed, persisted)
    with pytest.raises(TypeError):
        committed.commit["batch_id"] = first.base.batch_id
    with pytest.raises(ValueError, match="Backtest owner"):
        prepare_qualified_compound_families(type("Live", (), {"live_v4_lease": object()})(), result)
    with pytest.raises(RuntimeError, match="integrated writer hook"):
        prepare_compound_v4_families(object(), result)


def test_compound_preserves_nested_normalized_parent_chain():
    first = _base(1, 11, 0)
    second = _base(2, 12, 11,
                   kind=("order_management", "protection_reconciliation"))
    parent = {"record_id": second.events[0]["record_id"], "run_id": RUN,
              "batch_id": second.batch_id}
    action = {"record_id": str(UUID(int=303)), "parent_record_id": parent["record_id"],
              "run_id": RUN, "batch_id": second.batch_id}
    reply = {"record_id": str(UUID(int=304)), "parent_record_id": action["record_id"],
             "run_id": RUN, "batch_id": second.batch_id}
    unit = V4ProtectionReconciliationBatch(second, parent, (action,), (reply,))

    result = coalesce_v4_units((first, unit))
    assert result.children["reconciliation_replies"][0]["parent_record_id"] == action["record_id"]
    assert all(row["batch_id"] == result.base.batch_id
               for rows in result.children.values() for row in rows)


def test_compound_rejects_gap_and_foreign_parent():
    first = _base(1, 11, 0)
    gap = _base(3, 12, 11)
    with pytest.raises(ValueError, match="adjacent"):
        coalesce_v4_units((first, gap))
    second = _base(2, 12, 11,
                   kind=("portfolio_management", "portfolio_allocation"))
    foreign = {"record_id": str(UUID(int=999)), "run_id": RUN,
               "batch_id": second.batch_id}
    with pytest.raises(ValueError, match="parent"):
        coalesce_v4_units((first, V4PortfolioAllocationBatch(second, foreign)))


def test_compound_accepts_4096_event_transport_bound_but_rejects_4097():
    units = tuple(_base(sequence, 10 + sequence,
                        0 if sequence == 1 else 9 + sequence)
                  for sequence in range(1, 4098))
    compound = coalesce_v4_units(units[:4096], max_events=4096)
    assert len(compound.base.events) == 4096
    with pytest.raises(ValueError, match="event bound"):
        coalesce_v4_units(units, max_events=4096)


def test_mixed_preparation_seals_combined_base_without_clickhouse_io():
    first = batch()
    second_id = str(UUID(int=405))
    second_event = typed_row("trading_event_v1", {
        **{key: value for key, value in first.events[0].items()
           if key != "content_hash"},
        "record_id": str(UUID(int=406)), "batch_id": second_id,
        "sequence": 2,
    })
    second = replace(first, batch_id=second_id, prior_batch_id=first.batch_id,
                     first_sequence=2, last_sequence=2, events=(second_event,))
    compound = coalesce_v4_units((first, second))

    def forbidden(*_args, **_kwargs):
        raise AssertionError("V4 compound preparation must not call ClickHouse")

    client = SimpleNamespace(
        typed_insert_strict=True,
        typed_insert_dispatch=TypedInsertDispatch(object()),
        execute=forbidden)
    base, families = prepare_compound_v4_families(client, compound)
    assert len(dict(base)["trading_event_v1"]) == 2
    assert dict(families)["trading_event_v1"] == dict(base)["trading_event_v1"]
    assert all(row["batch_id"] == second_id
               for _, rows in families for row in rows)


def test_mixed_preparation_retains_broker_acknowledgement_child():
    at = datetime(2026, 8, 18, 8, 0, 31, tzinfo=timezone.utc)
    source = JournalRecord(
        str(UUID(int=181)), "run-ack-compound", 1, at, at,
        "broker", "order_acknowledgement", "1001", "DU1",
        {"order_id": "1001", "order_status": "Submitted",
         "local_order_id": "coid-1", "order_group_id": "group-1",
         "decision_to_submit_ms": 1.25, "ticker": "AAA",
         "action": "enter_long", "intent_id": "intent-1",
         "correlation_id": "correlation-1", "causation_id": "causation-1",
         "strategy_id": "early-squeeze-strategy", "strategy_revision": 1})
    first = broker_acknowledgement_batch_v4(
        source, run_month=date(2026, 8, 1), attempt_id=ATTEMPT,
        batch_id=str(UUID(int=183)), prior_batch_id=NIL,
        source_cursor="2026-08-18:31000")
    second_id = str(UUID(int=184))
    second_event = typed_row("trading_event_v1", {
        **{key: value for key, value in first.base.events[0].items()
           if key != "content_hash"},
        "record_id": str(UUID(int=185)), "batch_id": second_id,
        "sequence": 2, "category": "run_state", "entity_type": "lifecycle",
    })
    second = replace(first.base, batch_id=second_id,
                     prior_batch_id=first.base.batch_id,
                     first_sequence=2, last_sequence=2, events=(second_event,))
    compound = coalesce_v4_units((first, second))
    client = SimpleNamespace(
        typed_insert_strict=True,
        typed_insert_dispatch=TypedInsertDispatch(object()),
        execute=lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("pure preparation queried ClickHouse")))
    _, families = prepare_compound_v4_families(client, compound)
    ack, = dict(families)[ACKNOWLEDGEMENT.name]
    assert ack["record_id"] == source.record_id
    assert ack["batch_id"] == second_id

    altered = {key: tuple(dict(row) for row in rows)
               for key, rows in compound.children.items()}
    altered["acknowledgements"][0]["broker_order_id"] = "foreign-order"
    with pytest.raises(ValueError, match="lost normalized"):
        prepare_compound_v4_families(client, replace(compound, children=altered))

    altered = {key: tuple(dict(row) for row in rows)
               for key, rows in compound.children.items()}
    altered["acknowledgements"][0]["content_hash"] = "forged"
    with pytest.raises(ValueError, match="lost normalized"):
        prepare_compound_v4_families(client, replace(compound, children=altered))


def test_compound_publishes_one_cold_verified_commit_for_two_events():
    first = batch()
    second_id = str(UUID(int=407))
    second_event = typed_row("trading_event_v1", {
        **{key: value for key, value in first.events[0].items()
           if key != "content_hash"},
        "record_id": str(UUID(int=408)), "batch_id": second_id,
        "sequence": 2,
    })
    second = replace(first, batch_id=second_id, prior_batch_id=first.batch_id,
                     first_sequence=2, last_sequence=2, events=(second_event,))
    client = attached_v4_client()
    timings_ns = {}
    assert publish_compound_v4(client, coalesce_v4_units((first, second)),
                               timings_ns=timings_ns) == second_id
    assert timings_ns["prepare"] >= 0
    assert timings_ns["publish"] >= 0
    assert all(timings_ns[f"stage_{stage}"] >= 0 for stage in (
        "prechecks", "detail_inventory", "detail_insert", "detail_readback",
        "family_set", "commit_fence"))
    prefix = load_verified_v4_prefix(client, first.run_id)
    assert prefix.last_batch_id == second_id
    assert prefix.last_sequence == 2


def test_direct_live_compound_rejects_backtest_rows_before_any_insert():
    first = _base(1, 11, 0)
    second = _base(2, 12, 11)
    compound = coalesce_v4_units((first, second))
    client = attached_v4_client()
    client.live_v4_lease = SimpleNamespace(assert_current=lambda: None,
                                           run_id=RUN)
    rejected = replace(compound.base, backtest_progress=(
        {"record_id": str(UUID(int=999))},))
    with pytest.raises(ValueError, match="Backtest-only"):
        publish_compound_v4(client, replace(compound, base=rejected))
    assert client.inserts == []


def test_compound_can_cold_verify_intent_and_its_causal_oms_consumer():
    at = datetime(2026, 8, 18, 8, 5, tzinfo=timezone.utc)
    source_intent = intent()
    first = strategy_intent_batch(
        source_intent, run_id=RUN, run_month=date(2026, 8, 1),
        account_id="DU1", attempt_id=ATTEMPT, batch_id=str(UUID(int=411)),
        prior_batch_id=NIL, sequence=1, source_cursor="start",
        run_status="running", recorded_at=at)
    order = OrderRequest(acctId="DU1", conid=123, cOID="entry-1",
                         ticker="test", orderType="LMT", side="BUY",
                         quantity=5, price=12.5)
    group = _ManagedOrderGroup(
        "group-1", source_intent, "DU1", StrategyOrderPlan((order,)),
        OrderManagementState.CREATED, at, at, [order], remaining_quantity=5.)
    group.tactic = ExecutionTactic(
        ExecutionUrgency.URGENT, "BUY", (PriceStep(0, 12.5), PriceStep(200, 12.6)),
        ExecutionQuote(12.4, 12.6, at, 0.01), 200)
    second = oms_group_state_batch(
        group, run_id=RUN, run_month=date(2026, 8, 1),
        attempt_id=ATTEMPT, batch_id=str(UUID(int=412)),
        prior_batch_id=first.batch_id, sequence=2, source_cursor="start",
        run_status="running", strategy_id="strategy-1", strategy_revision=1,
        recorded_at=at, published_intent_batch=first,
        committed_intent_batch_id=first.batch_id)
    tactic_state, tactic_steps = tactic_rows(
        group.tactic, group_record_id=second.events[0]["record_id"],
        run_id=RUN, event_month="2026-08-01", batch_id=second.batch_id,
        account_id="DU1")
    second_unit = V4OmsTacticBatch(second, tactic_state, tactic_steps)
    client = attached_v4_client()
    compound = coalesce_v4_units((first, second_unit))
    assert _coalesce_v4_units((first, second_unit)) == (compound,)
    assert publish_compound_v4(client, compound) == second.batch_id
    prefix = load_verified_v4_prefix(client, RUN)
    assert prefix.last_sequence == 2
    assert prefix.batch_ids == (second.batch_id,)
    assert len(client.tables["trading_oms_execution_tactic_v1"]) == 1
    assert len(client.tables["trading_oms_execution_step_v1"]) == 2
    recovered, = load_committed_oms_group_state_page(
        client, prefix, require_tactic=True)
    assert recovered.tactic == group.tactic and recovered.tactic_recorded
    committed_source = _committed_intent_source(
        compound.base, first.events[0]["record_id"])
    assert committed_source.batch_id == prefix.last_batch_id
    assert committed_source.events[0]["batch_id"] == prefix.last_batch_id
    later = oms_group_state_batch(
        group, run_id=RUN, run_month=date(2026, 8, 1),
        attempt_id=ATTEMPT, batch_id=str(UUID(int=413)),
        prior_batch_id=second.batch_id, sequence=3, source_cursor="start",
        run_status="running", strategy_id="strategy-1", strategy_revision=1,
        recorded_at=at, published_intent_batch=committed_source,
        committed_intent_batch_id=second.batch_id)
    assert later.intent_uses[0]["intent_record_id"] == first.events[0]["record_id"]
