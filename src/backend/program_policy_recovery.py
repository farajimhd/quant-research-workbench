"""Cold policy projection from the existing committed financial journal.

This stages an actor without submitting orders or installing runtime state.
Portfolio, OMS, broker and source cursor admission remain separate authorities.
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from types import MappingProxyType
import re
from typing import Mapping

from .backtest_program_inputs import PreparedProgramBlock
from .qmd_columnar_population import CertifiedPopulationWindow
from .qmd_columnar_history_transport import _owned_worker
from src.trading_runtime.arte_journal_reader import TypedJournalEvent, load_typed_event_page
from src.trading_runtime.columnar_program_actor import AssignedProgramStrategy
from src.trading_runtime.ibkr_schema import Execution
from src.trading_runtime.filled_position_state import FeeFact


@dataclass(frozen=True)
class RecoveredProgramPolicy:
    actor: AssignedProgramStrategy
    journal_sequence: int
    execution_count: int
    completion_count: int
    completed_source_sequences: Mapping[str, int]


def _clock(value):
    at = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("Program policy recovery requires timezone-aware clocks")
    return at


def rebuild_program_policy_events(template, events, *, run_id, source_hashes,
        blocks, population, executions, order_manager, maximum_events, last_sequence):
    """Consume verified journal order, never guessed timestamp sorting or signals as fills."""
    if (type(template) is not AssignedProgramStrategy
            or type(population) is not CertifiedPopulationWindow
            or type(maximum_events) is not int or maximum_events < 1
            or type(last_sequence) is not int or not 0 <= last_sequence <= maximum_events):
        raise ValueError("Program policy recovery lacks its bounded exact scope")
    source_hashes = tuple(source_hashes)
    if (len(source_hashes) > maximum_events or len(set(source_hashes)) != len(source_hashes)
            or any(not isinstance(value, str) or re.fullmatch("[0-9a-f]{64}", value) is None
                   for value in source_hashes)):
        raise ValueError("Program policy recovery source prefix is invalid")
    source_sequences = {value: index + 1 for index, value in enumerate(source_hashes)}
    intervals = {}
    allowed_listings = set(template._listings.values())
    for block_count, block in enumerate(blocks, 1):
        if (block_count > maximum_events or type(block) is not PreparedProgramBlock
                or block.release_hash != template.program_release.header["content_hash"]
                or not set(block.history_hashes) <= allowed_listings):
            raise ValueError("Program policy recovery prepared release differs")
        for listing in block.history_hashes:
            first, _ = block.row_for(listing, block.first_ordinal)
            last, _ = block.row_for(listing, block.stop_ordinal - 1)
            intervals.setdefault(listing, []).append((first["boundary_at"], last["boundary_at"], block))
    indexes = {}
    for listing, rows in intervals.items():
        rows.sort(key=lambda row: row[0])
        if any(rows[index][0] <= rows[index - 1][1] for index in range(1, len(rows))):
            raise ValueError("Program policy recovery has ambiguous prepared clocks")
        indexes[listing] = ([row[0] for row in rows], rows)
    cached = {}
    for execution in executions:
        if (type(execution) is not Execution or execution.execution_id in cached
                or len(cached) >= min(maximum_events, 1_000_000)):
            raise ValueError("Program policy recovery execution cache is invalid or duplicate")
        cached[execution.execution_id] = execution
    actor = AssignedProgramStrategy(template.program_release, template.assignments(), template._listings)
    seen, fees, completed = set(), set(), {}
    sequence = completions = 0
    for item in events:
        sequence += 1
        if (sequence > maximum_events or type(item) is not TypedJournalEvent
                or item.event["run_id"] != run_id or item.event["sequence"] != sequence):
            raise ValueError("Program policy recovery journal is gapped, foreign or exceeds its bound")
        event, detail = item.event, item.detail
        if (event["category"], event["entity_type"]) == ("execution", "fill"):
            if item.detail_family != "trading_execution_v1" or detail is None:
                raise ValueError("Program policy recovery fill lacks normalized detail")
            identity = detail["execution_id"]
            execution = cached.get(identity)
            if (execution is None or identity in seen
                    or execution.account != detail["account_id"]
                    or event["account_id"] != detail["account_id"] or event["entity_id"] != identity
                    or execution.symbol != detail["ticker"] or execution.conid != detail["conid"]
                    or str(execution.order_id) != str(detail["broker_order_id"])
                    or execution.order_ref != detail["client_order_id"]
                    or execution.side.upper() != detail["side"].upper()
                    or Decimal(str(execution.size)) != Decimal(str(detail["quantity"]))
                    or Decimal(str(execution.price)) != Decimal(str(detail["price"]))
                    or execution.trade_time != _clock(detail["source_event_time"])
                    or _clock(event["event_time"]) != execution.trade_time
                    or execution.commission is None):
                raise ValueError("Program policy recovery fill differs from committed broker facts")
            actor.on_broker_executions((replace(execution, commission=None),), order_manager)
            seen.add(identity)
        elif (event["category"], event["entity_type"]) == ("execution", "commission"):
            if item.detail_family != "trading_commission_v1" or detail is None:
                raise ValueError("Program policy recovery commission lacks normalized detail")
            identity = detail["execution_id"]
            execution = cached.get(identity)
            if (execution is None or identity not in seen or identity in fees
                    or detail["status"] != "final" or detail["currency"] != execution.currency
                    or detail["account_id"] != execution.account or event["entity_id"] != identity
                    or event["account_id"] != execution.account
                    or detail["time_authority"] != "execution"
                    or _clock(detail["source_event_time"]) != execution.trade_time
                    or Decimal(str(detail["commission"])) != Decimal(str(execution.commission))):
                raise ValueError("Program policy recovery commission differs from committed broker facts")
            actor.execution_state.apply_fee(FeeFact(identity,
                Decimal(str(detail["commission"])), detail["currency"]))
            fees.add(identity)
        elif detail is not None and detail.get("signal_type") == "program_boundary_completed":
            if ((event["category"], event["entity_type"]) != ("strategy_decision", "signal")
                    or item.detail_family not in {"trading_strategy_signal_v1", "trading_strategy_signal_v2"}):
                raise ValueError("Program completion uses the wrong normalized family")
            assignment_id = detail["decision_assignment_id"]
            assignment = actor._by_assignment.get(assignment_id)
            source_hash = event["causation_id"]
            source_sequence = source_sequences.get(source_hash)
            at = _clock(detail["source_event_time"])
            status = detail["decision_reason_detail"]
            if (assignment is None or source_sequence is None
                    or source_sequence <= completed.get(assignment_id, 0)
                    or (detail["strategy_id"], detail["strategy_revision"], detail["account_id"], detail["ticker"])
                       != (actor.strategy_id, actor.revision, assignment.account_id, assignment.ticker)
                    or detail["decision_status"] != "completed" or detail["action"] != "wait"
                    or status not in {"selected", "wait", "pending_reduction", "pending_acquisition", "awaiting_protection_observation"}
                    or detail["reason"] != "program:" + status or detail["working_timeframe"] != "1s"
                    or event["entity_id"] != detail["signal_id"] or event["correlation_id"] != detail["signal_id"]
                    or _clock(event["event_time"]) != at):
                raise ValueError("Program completion differs from its assignment/source contract")
            listing = actor._listings[assignment_id]
            starts, rows = indexes.get(listing, ([], []))
            index = bisect_right(starts, at) - 1
            if index < 0 or at > rows[index][1]:
                raise ValueError("Program completion lacks its prepared clock")
            block = rows[index][2]
            ordinal = block.ordinal_for(listing, at)
            flags = [flag for flag in (False, True) if block.completion_identity(run_id=run_id,
                assignment_id=assignment_id, source_hash=source_hash, listing_id=listing,
                ordinal=ordinal, terminal=flag) == detail["signal_id"]]
            if len(flags) != 1:
                raise ValueError("Program completion prepared identity changed")
            boundary = block.boundary_for(actor.program_release, population, listing_id=listing,
                ticker=assignment.ticker, ordinal=ordinal)
            if Decimal(str(boundary.close)) != Decimal(str(detail["decision_reference_price"])):
                raise ValueError("Program completion reference mark changed")
            position = actor.position_for(assignment_id)
            if position.last_management_fill_at is not None and position.last_management_fill_at > at:
                raise ValueError("Program completion cannot consume a future fill")
            actor.trail_state.observe(boundary, position,
                minimum_age_seconds=actor.program_release.minimum_age_seconds["trail"])
            previous = actor._selection_clocks.get(assignment_id)
            if previous is not None and at <= previous[0]:
                raise ValueError("Program completed clocks are duplicate or out of order")
            actor._selection_clocks[assignment_id] = (at, (boundary, flags[0]))
            completed[assignment_id] = source_sequence
            completions += 1
    if sequence != last_sequence or seen != set(cached) or fees != seen:
        raise ValueError("Program policy recovery prefix/cache coverage is incomplete")
    return RecoveredProgramPolicy(actor, sequence, len(seen), completions, MappingProxyType(completed))


async def recover_program_policy_async(reader_factory, prefix, template, **scope):
    """Read exact committed pages on an owned worker and close its SELECT client."""
    def run():
        client = reader_factory()
        try:
            def events():
                after = 0
                while after < prefix.last_sequence:
                    page = load_typed_event_page(client, prefix, after_sequence=after, limit=1000)
                    if not page:
                        raise ValueError("Program policy recovery page did not advance")
                    yield from page
                    after = int(page[-1].event["sequence"])
            return rebuild_program_policy_events(template, events(), run_id=prefix.run_id,
                last_sequence=prefix.last_sequence, **scope)
        finally:
            client.close()
    return await _owned_worker(run)
