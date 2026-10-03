from datetime import date
from dataclasses import replace
from uuid import uuid4

import pytest

from src.backend.backtest_strategy_forty_three_journal import StrategyFortyThreeJournal
from src.backend.backtest_strategy_forty_three_projection import project_prefix
from src.backend.backtest_typed_projection import NIL_BATCH_ID
from src.trading_runtime.domain import InstrumentContract
from src.trading_runtime.strategy_forty_three_coordinator import BatchState, LegState
from src.trading_runtime.strategy_forty_three_rules import entry_intents, propose_batch
from src.trading_runtime.strategy_orders import IbkrStrategyOrderPlanner, canonical_runtime_order_raw
from tests.test_strategy_forty_three_rules import facts


def fixture():
    day = date(2026, 9, 3)
    batch = propose_batch(facts(), account_id="A", assignment_id="X",
                          free_cash_after_reservations=10_000., already_submitted=False)
    intents = entry_intents(batch, session_date=day)
    state = BatchState(day, batch, tuple(LegState(i, intent.intent_id, "", "submission_pending",
        confirmed_stop=batch.legs[i - 1].stop_price) for i, intent in enumerate(intents, 1)))
    journal = StrategyFortyThreeJournal(run_id=str(uuid4()))
    source = str(uuid4())
    journal.append_batch(state, source_fact_id=source)
    return journal, state, intents, source


def project(journal):
    return project_prefix(journal, attempt_id=str(uuid4()), run_month=date(2026, 9, 1),
        prior_sequence=0, prior_batch_id=NIL_BATCH_ID, source_cursor="start",
        expected_config={"mode": "backtest", "strategy_id": "squeeze-grid-strategy",
                         "strategy_revision": 43}, through_sequence=journal.latest_sequence(journal.run_id))


def test_batch_lock_and_fifteen_sources_project_without_strategy_one_evidence():
    journal, state, intents, source = fixture()
    units = project(journal)
    assert len(units) == 16
    assert len(units[0].signals) == 1
    assert len(units[0].signal_sources) == 2
    assert units[0].signal_sources[0]["source_signal_id"] == source
    assert [unit.intents[0]["intent_id"] for unit in units[1:]] == [row.intent_id for row in intents]
    assert all(unit.intents[0]["reason"] == "strategy_forty_three_entry" for unit in units[1:])
    assert all(len(unit.intent_slices) == 1 for unit in units[1:])
    assert all(type(unit).__name__ == "TypedJournalBatch" for unit in units)
    with pytest.raises(ValueError, match="already submitted"):
        journal.append_batch(state, source_fact_id=source)
    journal.mark_fenced(16)
    assert journal.pending_record_count == 0 and journal._forty_three_sources == {}
    assert journal.assignment_for_intent(intents[0].intent_id) == "X"
    with pytest.raises(ValueError, match="already submitted"):
        journal.append_batch(state, source_fact_id=source)


def test_native_order_commands_have_mandatory_exact_source_lineage():
    journal, _, intents, _ = fixture()
    intent = intents[0]
    plan = IbkrStrategyOrderPlanner().plan(intent=intent,
        instrument=InstrumentContract("conid:123", 123, "TEST", "STK", "SMART", "USD"),
        account_id="A", strategy_id="squeeze-grid-strategy", strategy_revision=43)
    for order in plan.orders:
        order = replace(order, raw=canonical_runtime_order_raw(order, intent,
            run_id=journal.run_id, strategy_id="squeeze-grid-strategy", strategy_revision=43))
        journal.append_strategy_order_command(order_request=order, run_id=journal.run_id,
            category="command", entity_type="order", entity_id=order.cOID,
            account_id="A", event_time=intent.event_time,
            payload={**order.to_cpapi(), "strategy_id": "squeeze-grid-strategy",
                     "strategy_revision": 43, "strategy_intent_id": intent.intent_id,
                     "intent_id": intent.intent_id, "ticker": "TEST", "order_group_id": "g1",
                     "policy_version": 1})
    units = project(journal)
    assert len(units) == 19
    assert all(len(unit.v4_command_lineages) == 1 for unit in units[-3:])
    assert all(unit.intent_uses[0]["intent_record_id"] == units[1].intents[0]["record_id"]
               for unit in units[-3:])


def test_native_publisher_retains_fenced_sources_for_later_command_prefix():
    import asyncio
    from src.backend.backtest_strategy_forty_three_publisher import StrategyFortyThreePublisher
    from tests.test_backtest_typed_publisher import FakeWriter, ATTEMPT
    async def run():
        journal, _, intents, _ = fixture()
        writer = FakeWriter()
        writer.run_id = journal.run_id
        writer.journal_profile = "backtest_v4"
        # Transport is a fixture; real native projection/coalescing/source
        # retention and bounded fencing execute here. No financial run.
        def submit_compound(unit, **_kwargs):
            return writer.submit(unit.base)
        writer.submit_compound_v4 = submit_compound
        publisher = StrategyFortyThreePublisher(journal, writer, attempt_id=ATTEMPT,
            run_month=date(2026, 9, 1), expected_config={"mode": "backtest",
                "strategy_id": "squeeze-grid-strategy", "strategy_revision": 43})
        await publisher.await_fence()
        assert publisher.fenced_sequence == 16
        assert len(publisher._committed_strategy_intents) == 15
        assert set(publisher._committed_strategy_intents) == {row.intent_id for row in intents}
        assert journal.pending_record_count == 0
    asyncio.run(run())


def test_independent_cold_leg_rebuilds_native_admission_and_rejects_foreign_witness():
    from src.trading_runtime.arte_intent_projection import RecoveredIntent
    from src.trading_runtime.arte_journal_reader import CompleteProtectionHistory
    from src.trading_runtime.arte_oms_projection import RecoveredOmsGroupState, reconstruct_strategy_one_oms_lineage
    journal, _, intents, _ = fixture()
    intent = intents[0]
    source_batch, group_batch, source_record = (str(uuid4()) for _ in range(3))
    source = RecoveredIntent(1, "A", source_record, source_batch, intent)
    plan = IbkrStrategyOrderPlanner().plan(intent=intent,
        instrument=InstrumentContract("conid:123", 123, "TEST", "STK", "SMART", "USD"),
        account_id="A", strategy_id="squeeze-grid-strategy", strategy_revision=43)
    state = RecoveredOmsGroupState(3, source_record,
        dict(run_id=journal.run_id, batch_id=group_batch, account_id="A", group_id="g1",
             strategy_id="squeeze-grid-strategy", strategy_revision=43,
             strategy_intent_id=intent.intent_id), tuple(replace(row, raw={}) for row in plan.orders),
        (0, 0, 0), ("all", "all", "all"), (), (), ())
    history = CompleteProtectionHistory(journal.run_id, 3, (source_batch, group_batch), ())
    reservation = dict(account_id="A", intent_id=intent.intent_id, decision_id="decision",
        reservation_id="reservation", account_key="cash", assignment_id="X", quantity=intent.quantity)
    decision = dict(decision_id="decision", reservation_id="reservation", account_key="cash",
        status="approved", policy_id="cash", policy_revision=1, requested_quantity=intent.quantity)
    orders = reconstruct_strategy_one_oms_lineage(state, source, history,
        admission_reservation=reservation, admission_decision=decision)
    assert len(orders) == 3
    assert all(row.raw["canonical_strategy_id"] == "squeeze-grid-strategy" for row in orders)
    assert all(row.raw["canonical_strategy_revision"] == 43 for row in orders)
    assert all(row.raw["canonical_metadata"]["assignment_id"] == "X" for row in orders)
    with pytest.raises(ValueError, match="another strategy"):
        reconstruct_strategy_one_oms_lineage(state, source, history,
            admission_reservation=reservation, admission_decision=decision, profit_giveback_row={})
    with pytest.raises(ValueError, match="independent leg"):
        reconstruct_strategy_one_oms_lineage(state, source, history,
            admission_reservation={**reservation, "quantity": intent.quantity - 1}, admission_decision=decision)
