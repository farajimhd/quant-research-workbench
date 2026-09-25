"""Read-only, all-or-nothing projection of a fixed Backtest journal prefix.

This module prepares normalized arte batches. It does not submit or fence them;
the fixed Backtest launch remains blocked until typed recovery is complete.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID, NAMESPACE_URL, uuid5

from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.trading_runtime.arte_journal_projection import project_journal_record
from src.trading_runtime.arte_journal_writer import TypedJournalBatch
from src.trading_runtime.arte_journal_writer import V3SqueezeBatch
from src.trading_runtime.journal_contract import canonical_json


NIL_BATCH_ID = str(UUID(int=0))


@dataclass(frozen=True, slots=True)
class ProjectedBacktestPrefix:
    batches: tuple[TypedJournalBatch, ...]
    last_sequence: int
    last_batch_id: str
    source_cursor: str


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
    records = journal.unfenced_records(after_sequence=prior_sequence)
    if through_sequence is not None:
        if (type(through_sequence) is not int or through_sequence <= prior_sequence
                or through_sequence > journal.latest_sequence(journal.run_id)):
            raise ValueError("Backtest typed projection limit is outside pending records")
        records = [record for record in records if record.sequence <= through_sequence]
        if len(records) != through_sequence - prior_sequence:
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
    records = journal.unfenced_records(after_sequence=prior_sequence)
    if through_sequence is not None:
        if (through_sequence <= prior_sequence
                or through_sequence > journal.latest_sequence(journal.run_id)):
            raise ValueError("V3 projection limit is outside pending records")
        records = [row for row in records if row.sequence <= through_sequence]
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
