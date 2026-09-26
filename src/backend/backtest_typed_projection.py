"""Read-only, all-or-nothing projection of a fixed Backtest journal prefix.

This module prepares normalized arte batches. It does not submit or fence them;
the fixed Backtest launch remains blocked until typed recovery is complete.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Mapping
from uuid import UUID, NAMESPACE_URL, uuid5

from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.trading_runtime.arte_journal_projection import project_journal_record
from src.trading_runtime.arte_journal_writer import TypedJournalBatch
from src.trading_runtime.arte_journal_writer import V3SqueezeBatch
from src.trading_runtime.arte_journal_writer import (
    V4BrokerAcknowledgementBatch, V4OrderCancelBatch, V4OrderRepriceBatch,
    V4ProtectionChangeBatch,
    V4StrategyOneEntryBatch, _coalesce_unpublished,
)
from src.trading_runtime.arte_protection_reconciliation_v4 import (
    V4ProtectionReconciliationBatch,
)
from src.trading_runtime.journal_contract import canonical_json


NIL_BATCH_ID = str(UUID(int=0))


def committed_oms_order_lineage(group: object, *, run_id: str,
                                strategy_id: str, strategy_revision: int) -> dict[str, tuple]:
    """Index only lineage already validated against a typed OMS transition."""
    from src.trading_runtime.strategy_orders import canonical_runtime_metadata

    result = {}
    for order in group.orders:
        if not order.cOID:
            raise ValueError("Typed OMS order lacks a client order ID")
        expected = {
            "strategy_id": strategy_id,
            "canonical_strategy_revision": strategy_revision,
            "canonical_run_id": run_id,
            "canonical_metadata": canonical_runtime_metadata(order, group.intent),
        }
        lineage = (expected, group.account_id, order.ticker.upper(), order.conid)
        prior = result.setdefault(order.cOID, lineage)
        if prior != lineage:
            raise ValueError("Client order ID has conflicting OMS lineage")
    return result


@dataclass(frozen=True, slots=True)
class ProjectedBacktestPrefix:
    batches: tuple[TypedJournalBatch, ...]
    last_sequence: int
    last_batch_id: str
    source_cursor: str


def project_pending_backtest_v4_prefix(
    journal: BacktestMemoryJournal, *, attempt_id: str, run_month: date,
    prior_sequence: int, prior_batch_id: str = NIL_BATCH_ID,
    source_cursor: str = "start", expected_config: dict | None = None,
    fixed_market_parent_plan: object | None = None,
    fixed_market_execution_plan: object | None = None,
    expected_market_start: datetime | None = None,
    published_sources: Mapping[str, tuple[TypedJournalBatch, object]] | None = None,
    committed_order_lineage: Mapping[str, tuple] | None = None,
    through_sequence: int,
) -> tuple[TypedJournalBatch | V4StrategyOneEntryBatch
           | V4BrokerAcknowledgementBatch | V4OrderCancelBatch
           | V4OrderRepriceBatch | V4ProtectionChangeBatch
           | V4ProtectionReconciliationBatch, ...]:
    """Project one bounded V4 prefix; special families never enter a base batch."""
    from src.trading_runtime.arte_broker_acknowledgement_v4 import (
        broker_acknowledgement_batch_v4,
    )
    from src.trading_runtime.arte_protection_change_v4 import (
        protection_change_batch_v4,
    )
    from src.trading_runtime.arte_protection_reconciliation_v4 import (
        protection_reconciliation_batch_v4,
    )
    from src.trading_runtime.arte_strategy_one_entry_journal import (
        project_strategy_one_entry_evidence,
    )
    from src.trading_runtime.strategy_one_intent import strategy_one_entry_intent
    from src.trading_runtime.arte_oms_projection import oms_group_state_batch

    attempt = str(UUID(attempt_id))
    previous = str(UUID(prior_batch_id))
    if (run_month.day != 1 or type(prior_sequence) is not int
            or prior_sequence < 0 or not source_cursor
            or (prior_sequence == 0) != (previous == NIL_BATCH_ID)
            or type(through_sequence) is not int
            or not prior_sequence < through_sequence
                   <= journal.latest_sequence(journal.run_id)):
        raise ValueError("V4 projection prefix identity is invalid")
    records = journal.unfenced_records(
        after_sequence=prior_sequence, through_sequence=through_sequence)
    if len(records) != through_sequence - prior_sequence:
        raise ValueError("V4 projection prefix is not contiguous")
    units = []
    ordinary: list[TypedJournalBatch] = []
    sources = dict(published_sources or {})
    order_lineage = dict(committed_order_lineage or {})
    cursor = source_cursor
    for sequence, record in enumerate(records, start=prior_sequence + 1):
        if record.run_id != journal.run_id or record.sequence != sequence:
            raise ValueError("V4 projection changed its run or event sequence")
        canonical_json(record.payload)
        batch_id = str(uuid5(NAMESPACE_URL,
            f"arte-backtest-v1:{record.run_id}:{attempt}:{sequence}:{record.record_id}"))
        kind = (record.category, record.entity_type)
        if kind == ("checkpoint", "market_boundary"):
            cursor = record.entity_id
        if kind in {("broker", "order_repriced"),
                    ("broker", "order_reprice_error")}:
            from src.trading_runtime.arte_order_reprice_v4 import order_reprice_batch_v4
            unit = order_reprice_batch_v4(
                record, run_month=run_month, attempt_id=attempt,
                batch_id=batch_id, prior_batch_id=previous,
                source_cursor=cursor)
        elif kind in {("command", "order_cancel"),
                    ("broker", "order_cancel_requested")}:
            from src.trading_runtime.arte_order_cancel_v4 import order_cancel_batch_v4
            unit = order_cancel_batch_v4(
                record, run_month=run_month, attempt_id=attempt,
                batch_id=batch_id, prior_batch_id=previous,
                source_cursor=cursor)
        elif kind == ("broker", "order_acknowledgement"):
            unit = broker_acknowledgement_batch_v4(
                record, run_month=run_month, attempt_id=attempt,
                batch_id=batch_id, prior_batch_id=previous,
                source_cursor=cursor)
        elif kind == ("order_management", "protection_replacement_deferred"):
            from src.trading_runtime.arte_protection_deferral_v4 import (
                protection_deferral_batch_v4,
            )
            source = sources.get(record.entity_id)
            if source is None:
                raise RuntimeError("Protection deferral lacks a journaled source intent")
            unit = protection_deferral_batch_v4(
                record, source_batch=source[0], source_intent=source[1],
                run_month=run_month, attempt_id=attempt, batch_id=batch_id,
                prior_batch_id=previous, source_cursor=cursor,
                strategy_id=(expected_config or {}).get("strategy_id"),
                strategy_revision=(expected_config or {}).get("strategy_revision"))
        elif kind == ("protection", "protection_change"):
            unit = protection_change_batch_v4(
                record, run_month=run_month, attempt_id=attempt,
                batch_id=batch_id, prior_batch_id=previous,
                source_cursor=cursor)
        elif kind == ("order_management", "protection_reconciliation"):
            unit = protection_reconciliation_batch_v4(
                record, run_month=run_month, attempt_id=attempt,
                batch_id=batch_id, prior_batch_id=previous,
                source_cursor=cursor)
        elif kind == ("order_management", "order_group_state"):
            group = journal.oms_group_for_record(record.record_id)
            admission = journal.oms_admission_for_record(record.record_id)
            if group is None or admission is None:
                raise RuntimeError("V4 OMS transition lacks its immutable admission")
            if (record.entity_id != group.group_id
                    or record.account_id != group.account_id
                    or record.payload.get("intent_id") != group.intent.intent_id
                    or record.payload.get("state") != group.state.value
                    or record.payload.get("action") != group.intent.action
                    or record.payload.get("ticker") != group.intent.ticker):
                raise RuntimeError("V4 OMS transition differs from its frozen group")
            source = sources.get(group.intent.intent_id)
            if source is None:
                raise RuntimeError("V4 OMS transition lacks its committed source intent")
            source_batch, source_intent = source
            unit = oms_group_state_batch(
                group, run_id=record.run_id, run_month=run_month,
                attempt_id=attempt, batch_id=batch_id,
                prior_batch_id=previous, sequence=sequence,
                source_cursor=cursor, run_status="running",
                strategy_id=record.payload["strategy_id"],
                strategy_revision=record.payload["strategy_revision"],
                recorded_at=record.recorded_at,
                published_intent_batch=source_batch,
                committed_intent_batch_id=source_batch.batch_id,
                admission_source_intent=source_intent,
                admission_reservation=admission,
                journal_record_id=record.record_id,
                correlation_id=record.payload.get("correlation_id", ""),
                causation_id=record.payload.get("causation_id", ""))
            if (unit.events[0]["event_time"] != record.event_time.isoformat()
                    or unit.events[0]["entity_id"] != record.entity_id
                    or unit.events[0]["account_id"] != record.account_id):
                raise RuntimeError("V4 OMS typed event differs from its journal source")
            for client_order_id, lineage in committed_oms_order_lineage(
                    group, run_id=record.run_id,
                    strategy_id=record.payload["strategy_id"],
                    strategy_revision=record.payload["strategy_revision"]).items():
                if client_order_id in order_lineage and order_lineage[client_order_id] != lineage:
                    raise RuntimeError("OMS changed committed order lineage")
                order_lineage[client_order_id] = lineage
        else:
            batch = project_journal_record(
                record, run_month=run_month, attempt_id=attempt,
                batch_id=batch_id, prior_batch_id=previous,
                source_cursor=cursor, expected_config=expected_config,
                expected_mode="backtest",
                fixed_market_parent_plan=fixed_market_parent_plan,
                fixed_market_execution_plan=fixed_market_execution_plan,
                expected_market_start=expected_market_start,
                committed_order_lineage=order_lineage)
            sidecar = journal.strategy_one_entry_for_record(record.record_id)
            protection_source = journal.strategy_one_protection_for_record(
                record.record_id)
            if sidecar is not None and protection_source is not None:
                raise RuntimeError("Strategy 1 intent has two source authorities")
            if sidecar is None:
                if (kind == ("strategy", "strategy_intent")
                        and record.payload.get("reason") == "strategy_one_entry"):
                    raise RuntimeError("Strategy 1 journal intent lacks normalized evidence")
                if (kind == ("strategy", "strategy_intent")
                        and record.payload.get("strategy_id") == "early-squeeze-strategy"
                        and record.payload.get("strategy_revision") == 1
                        and record.payload.get("action") in {
                            "replace_protective_stop", "replace_profit_target"}
                        and protection_source is None):
                    raise RuntimeError("Strategy 1 protection intent lacks typed source")
                unit = batch
                if protection_source is not None:
                    if (kind != ("strategy", "strategy_intent")
                            or record.entity_id != protection_source.intent_id
                            or record.account_id == ""
                            or protection_source.payload() != {
                                key: value for key, value in record.payload.items()
                                if key not in {"strategy_id", "strategy_revision",
                                               "correlation_id", "causation_id"}}):
                        raise RuntimeError("Strategy 1 protection source differs from typed intent")
                    prior_source = sources.get(protection_source.intent_id)
                    if prior_source is not None and prior_source != (
                            batch, protection_source):
                        raise RuntimeError("V4 Strategy 1 protection identity was reused")
                    sources[protection_source.intent_id] = (batch, protection_source)
            else:
                proposal, session_date = sidecar
                intent = strategy_one_entry_intent(
                    proposal, session_date=session_date)
                evidence = project_strategy_one_entry_evidence(
                    proposal, intent, session_date=session_date,
                    run_id=batch.run_id, batch_id=batch.batch_id,
                    parent_record_id=record.record_id)
                unit = V4StrategyOneEntryBatch(batch, (evidence,))
                prior_source = sources.get(intent.intent_id)
                if prior_source is not None and prior_source != (batch, intent):
                    raise RuntimeError("V4 Strategy 1 intent identity was reused")
                sources[intent.intent_id] = (batch, intent)
        base = unit.base if not isinstance(unit, TypedJournalBatch) else unit
        if (base.first_sequence != sequence or base.last_sequence != sequence
                or len(base.events) != 1 or base.batch_id != batch_id
                or base.prior_batch_id != previous):
            raise ValueError("V4 projector changed the exclusive batch identity")
        if isinstance(unit, TypedJournalBatch):
            if journal.strategy_one_protection_for_record(record.record_id) is not None:
                if ordinary:
                    units.append(_coalesce_unpublished(tuple(ordinary)))
                    ordinary.clear()
                units.append(unit)
            else:
                ordinary.append(unit)
        else:
            if ordinary:
                units.append(_coalesce_unpublished(tuple(ordinary)))
                ordinary.clear()
            units.append(unit)
        previous = batch_id
    if ordinary:
        units.append(_coalesce_unpublished(tuple(ordinary)))
    return tuple(units)


def project_pending_backtest_prefix(
    journal: BacktestMemoryJournal, *, attempt_id: str, run_month: date,
    prior_sequence: int, prior_batch_id: str = NIL_BATCH_ID,
    source_cursor: str = "start", expected_config: dict | None = None,
    fixed_market_parent_plan: object | None = None,
    fixed_market_execution_plan: object | None = None,
    expected_market_start: datetime | None = None,
    through_sequence: int | None = None,
) -> ProjectedBacktestPrefix:
    """Project every pending record or reject the entire prefix before writes.

    Each logical event receives one deterministic typed batch. A completed
    market-boundary event advances the source cursor only within its own batch.
    """
    attempt = str(UUID(attempt_id))
    previous = str(UUID(prior_batch_id))
    if (run_month.day != 1 or type(prior_sequence) is not int
            or prior_sequence < 0 or not source_cursor
            or (prior_sequence == 0 and previous != NIL_BATCH_ID)
            or (prior_sequence > 0 and previous == NIL_BATCH_ID)):
        raise ValueError("Backtest typed prefix identity is invalid")
    if through_sequence is not None:
        if (type(through_sequence) is not int or through_sequence <= prior_sequence
                or through_sequence > journal.latest_sequence(journal.run_id)):
            raise ValueError("Backtest typed projection limit is outside pending records")
    records = journal.unfenced_records(after_sequence=prior_sequence,
                                       through_sequence=through_sequence)
    if through_sequence is not None and len(records) != through_sequence - prior_sequence:
        raise ValueError("Backtest typed projection limit is not contiguous")
    batches: list[TypedJournalBatch] = []
    cursor = source_cursor
    for sequence, record in enumerate(records, start=prior_sequence + 1):
        if record.run_id != journal.run_id or record.sequence != sequence:
            raise ValueError("Backtest typed prefix is not one contiguous run")
        canonical_json(record.payload)
        batch_id = str(uuid5(NAMESPACE_URL,
            f"arte-backtest-v1:{record.run_id}:{attempt}:{record.sequence}:{record.record_id}"))
        if (record.category, record.entity_type) == ("checkpoint", "market_boundary"):
            cursor = record.entity_id
        batch = project_journal_record(
            record, run_month=run_month, attempt_id=attempt,
            batch_id=batch_id, prior_batch_id=previous, source_cursor=cursor,
            expected_config=expected_config, expected_mode="backtest",
            fixed_market_parent_plan=fixed_market_parent_plan,
            fixed_market_execution_plan=fixed_market_execution_plan,
            expected_market_start=expected_market_start,
        )
        if (batch.run_id != journal.run_id or batch.first_sequence != sequence
                or batch.last_sequence != sequence or len(batch.events) != 1):
            raise ValueError("Backtest typed projector changed event identity")
        batches.append(batch)
        previous = batch_id
    return ProjectedBacktestPrefix(tuple(batches),
                                   prior_sequence + len(batches), previous, cursor)


def project_pending_backtest_v3_prefix(
    journal: BacktestMemoryJournal, *, attempt_id: str, run_month: date,
    prior_sequence: int, prior_batch_id: str = NIL_BATCH_ID,
    source_cursor: str = "start", expected_config: dict | None = None,
    fixed_market_parent_plan: object | None = None,
    fixed_market_execution_plan: object | None = None,
    expected_market_start: datetime | None = None,
    expected_market_plan_token: str, expected_query_sha256: str,
    through_sequence: int | None = None,
) -> tuple[V3SqueezeBatch, ...]:
    """Project a contiguous V3 prefix; every batch names its closed child set."""
    import re
    from src.backend.backtest_squeeze_episode_v3 import project_squeeze_batch_v3

    if (re.fullmatch(r"[0-9a-f]{64}", expected_market_plan_token) is None
            or re.fullmatch(r"[0-9a-f]{64}", expected_query_sha256) is None
            or run_month.day != 1 or prior_sequence < 0):
        raise ValueError("V3 projection lacks pinned source authority")
    attempt = str(UUID(attempt_id))
    previous = str(UUID(prior_batch_id))
    if (prior_sequence == 0) != (previous == NIL_BATCH_ID):
        raise ValueError("V3 projection prior batch identity differs")
    if through_sequence is not None:
        if (through_sequence <= prior_sequence
                or through_sequence > journal.latest_sequence(journal.run_id)):
            raise ValueError("V3 projection limit is outside pending records")
    records = journal.unfenced_records(after_sequence=prior_sequence,
                                       through_sequence=through_sequence)
    result: list[V3SqueezeBatch] = []
    cursor = source_cursor
    for sequence, record in enumerate(records, start=prior_sequence + 1):
        if record.run_id != journal.run_id or record.sequence != sequence:
            raise ValueError("V3 projection is not one contiguous run")
        canonical_json(record.payload)
        batch_id = str(uuid5(NAMESPACE_URL,
            f"arte-backtest-v3:{record.run_id}:{attempt}:{record.sequence}:{record.record_id}"))
        if (record.category, record.entity_type) == ("checkpoint", "market_boundary"):
            cursor = record.entity_id
        if (record.category, record.entity_type) == (
                "market_discovery_signal", "signal_occurrence"):
            unit = project_squeeze_batch_v3(
                record, run_month=run_month, attempt_id=attempt,
                batch_id=batch_id, prior_batch_id=previous,
                source_cursor=cursor,
                expected_market_plan_token=expected_market_plan_token,
                expected_query_sha256=expected_query_sha256)
        elif (record.category, record.entity_type) == (
                "portfolio_management", "portfolio_reconciliation"):
            from src.backend.backtest_reconciliation_v3 import project_reconciliation_v3

            reconciliation = project_reconciliation_v3(
                record, attempt_id=attempt, batch_id=batch_id,
                account_key=record.entity_id)
            base = TypedJournalBatch(
                record.run_id, run_month, attempt, batch_id, previous,
                sequence, sequence, cursor, "running", (reconciliation.event,),
                portfolio_reconciliation_events=(reconciliation.parent,))
            unit = V3SqueezeBatch(
                base, (), (), reconciliation.differences)
        elif (record.category, record.entity_type) == (
                "portfolio_management", "portfolio_control"):
            from src.backend.backtest_portfolio_control_v3 import project_portfolio_control_v3

            strategy_id = record.payload.get("strategy_id")
            account_key = (record.entity_id[:-(len(strategy_id) + 1)]
                           if record.payload.get("event") ==
                           "strategy_allocation_control_changed"
                           and isinstance(strategy_id, str)
                           and record.entity_id.endswith(f":{strategy_id}")
                           else record.entity_id)
            control = project_portfolio_control_v3(
                record, attempt_id=attempt, batch_id=batch_id,
                account_key=account_key)
            selections = ()
            if record.payload.get("event") == "portfolio_policy_selected":
                from src.backend.backtest_policy_selection_v3 import project_policy_selection_v3

                selections = (project_policy_selection_v3(
                    record, account_key=account_key),)
            base = TypedJournalBatch(
                record.run_id, run_month, attempt, batch_id, previous,
                sequence, sequence, cursor, "running", (control.event,))
            unit = V3SqueezeBatch(base, (), portfolio_controls=(control.detail,),
                                  policy_selections=selections)
        elif (record.category == "trade_proposal" and record.entity_type in
              {"trade_proposal_confirmed", "trade_proposal_result"}):
            from src.backend.backtest_trade_proposal_v3 import project_trade_proposal_v3

            proposal = project_trade_proposal_v3(
                record, attempt_id=attempt, batch_id=batch_id)
            base = TypedJournalBatch(
                record.run_id, run_month, attempt, batch_id, previous,
                sequence, sequence, cursor, "running", (proposal.event,))
            unit = V3SqueezeBatch(
                base, (), trade_proposal_rows=proposal.rows)
        elif (record.category, record.entity_type) == (
                "broker_policy", "short_order_skipped"):
            from src.backend.backtest_broker_shortability_v3 import project_short_order_skip_v3

            projected = project_short_order_skip_v3(
                record, attempt_id=attempt, batch_id=batch_id)
            base = TypedJournalBatch(
                record.run_id, run_month, attempt, batch_id, previous,
                sequence, sequence, cursor, "running", (projected.event,))
            unit = V3SqueezeBatch(base, (), short_order_skips=(projected.detail,))
        elif record.category == "broker_policy" and record.entity_type in {
                "order_reply_suppression", "order_warning_decision"}:
            from src.backend.backtest_broker_policy_v3 import project_broker_reply_policy_v3

            projected = project_broker_reply_policy_v3(
                record, attempt_id=attempt, batch_id=batch_id)
            base = TypedJournalBatch(
                record.run_id, run_month, attempt, batch_id, previous,
                sequence, sequence, cursor, "running", (projected.event,))
            unit = V3SqueezeBatch(
                base, (), broker_reply_policy_events=(projected.detail,),
                broker_reply_policy_messages=projected.messages)
        elif (record.category, record.entity_type) == (
                "order_management", "entry_reprice_deferred"):
            from src.backend.backtest_entry_reprice_deferred_v3 import (
                project_entry_reprice_deferred_v3,
            )

            projected = project_entry_reprice_deferred_v3(
                record, attempt_id=attempt, batch_id=batch_id)
            base = TypedJournalBatch(
                record.run_id, run_month, attempt, batch_id, previous,
                sequence, sequence, cursor, "running", (projected.event,))
            unit = V3SqueezeBatch(
                base, (), entry_reprice_deferred=(projected.detail,))
        elif (record.category, record.entity_type) == (
                "portfolio_management", "entry_reprice_capacity"):
            from src.backend.backtest_entry_reprice_capacity_v3 import (
                project_entry_reprice_capacity_v3,
            )

            projected = project_entry_reprice_capacity_v3(
                record, attempt_id=attempt, batch_id=batch_id)
            base = TypedJournalBatch(
                record.run_id, run_month, attempt, batch_id, previous,
                sequence, sequence, cursor, "running", (projected.event,))
            unit = V3SqueezeBatch(
                base, (), entry_reprice_capacities=(projected.detail,),
                entry_reprice_capacity_reasons=projected.reasons)
        elif (record.category, record.entity_type) == (
                "portfolio_management", "entry_reprice_rejected"):
            from src.backend.backtest_entry_reprice_rejected_v3 import (
                project_entry_reprice_rejected_v3,
            )

            projected = project_entry_reprice_rejected_v3(
                record, attempt_id=attempt, batch_id=batch_id)
            base = TypedJournalBatch(
                record.run_id, run_month, attempt, batch_id, previous,
                sequence, sequence, cursor, "running", (projected.event,))
            unit = V3SqueezeBatch(
                base, (), entry_reprice_rejections=(projected.detail,))
        elif (record.category, record.entity_type) == (
                "order_management", "protected_exit_already_satisfied"):
            from src.backend.backtest_protected_exit_satisfied_v3 import (
                project_protected_exit_satisfied_v3,
            )

            projected = project_protected_exit_satisfied_v3(
                record, attempt_id=attempt, batch_id=batch_id)
            base = TypedJournalBatch(
                record.run_id, run_month, attempt, batch_id, previous,
                sequence, sequence, cursor, "running", (projected.event,))
            unit = V3SqueezeBatch(
                base, (), protected_exit_satisfied=(projected.detail,))
        elif (record.category, record.entity_type) == (
                "order_management", "protected_exit_snapshot_reconciled"):
            from src.backend.backtest_protected_exit_snapshot_v3 import (
                project_protected_exit_snapshot_v3,
            )

            projected = project_protected_exit_snapshot_v3(
                record, attempt_id=attempt, batch_id=batch_id)
            base = TypedJournalBatch(
                record.run_id, run_month, attempt, batch_id, previous,
                sequence, sequence, cursor, "running", (projected.event,))
            unit = V3SqueezeBatch(
                base, (), protected_exit_snapshots=(projected.detail,))
        elif (record.category, record.entity_type) == (
                "portfolio_management", "portfolio_allocation"):
            from src.backend.backtest_portfolio_allocation_v3 import (
                project_portfolio_allocation_v3,
            )

            projected = project_portfolio_allocation_v3(
                record, attempt_id=attempt, batch_id=batch_id)
            base = TypedJournalBatch(
                record.run_id, run_month, attempt, batch_id, previous,
                sequence, sequence, cursor, "running", (projected.event,))
            unit = V3SqueezeBatch(
                base, (), portfolio_allocation_fills=(projected.detail,))
        elif (record.category, record.entity_type) == (
                "protection", "protection_change"):
            from src.backend.backtest_protection_change_v3 import project_protection_change_v3

            projected = project_protection_change_v3(
                record, attempt_id=attempt, batch_id=batch_id)
            base = TypedJournalBatch(
                record.run_id, run_month, attempt, batch_id, previous,
                sequence, sequence, cursor, "running", (projected.event,))
            unit = V3SqueezeBatch(
                base, (), protection_changes=(projected.detail,),
                protection_entry_orders=projected.entry_orders)
        else:
            reservation_reasons = ()
            if (record.category, record.entity_type) == (
                    "portfolio_management", "portfolio_reservation"):
                from src.backend.backtest_reservation_reason_v3 import (
                    project_reservation_reasons_v3,
                )
                reservation_reasons = project_reservation_reasons_v3(
                    record, batch_id=batch_id)
            base = project_journal_record(
                record, run_month=run_month, attempt_id=attempt,
                batch_id=batch_id, prior_batch_id=previous,
                source_cursor=cursor, expected_config=expected_config,
                expected_mode="backtest",
                fixed_market_parent_plan=fixed_market_parent_plan,
                fixed_market_execution_plan=fixed_market_execution_plan,
                expected_market_start=expected_market_start,
                allow_v3_reservation_reasons=True)
            unit = V3SqueezeBatch(base, (), reservation_reasons)
        if (unit.base.run_id != journal.run_id
                or unit.base.first_sequence != sequence
                or unit.base.last_sequence != sequence
                or len(unit.base.events) != 1):
            raise ValueError("V3 projector changed event identity")
        result.append(unit)
        previous = batch_id
    return tuple(result)
