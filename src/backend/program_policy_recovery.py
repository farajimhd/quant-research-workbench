"""Cold policy projection from the existing committed financial journal.

This stages an actor without submitting orders or installing runtime state.
Portfolio, OMS, broker and source cursor admission remain separate authorities.
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field, replace
from hashlib import sha256
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
from src.trading_runtime.journal_contract import canonical_json


_RECOVERY_PROOF = object()


def program_policy_fingerprint(actor):
    """Seal the complete derived ledger and policy caches in memory only."""
    def pairs(mapping, *, sets=False):
        return [(key, sorted(value) if sets else value) for key, value in sorted(mapping.items())]
    ledger = actor.execution_state
    value = dict(release_hash=actor.program_release.header["content_hash"],
        assignments=actor.assignments(), listings=pairs(actor._listings),
        fills=pairs(ledger._fills), fees=pairs(ledger._fees), positions=pairs(ledger._positions),
        last_at=ledger._last_at, maximum_executions=ledger.maximum_executions,
        target_fraction=actor.target_state.fraction, adds=pairs(ledger._add_intents, sets=True),
        episode_executions=pairs(ledger._episode_executions, sets=True),
        closed_executions=pairs(ledger._closed_episode_by_execution), closed_net=pairs(ledger._closed_net),
        targets=pairs(actor.target_state._targets), entry_intents=pairs(actor.target_state._entry_intents),
        advanced_intents=pairs(actor.target_state._advanced_intents, sets=True),
        trail=pairs(actor.trail_state._water), clocks=pairs(actor._selection_clocks))
    return sha256(canonical_json(value).encode()).hexdigest()


@dataclass(frozen=True)
class RecoveredProgramPolicy:
    actor: AssignedProgramStrategy
    journal_sequence: int
    execution_count: int
    completion_count: int
    completed_source_sequences: Mapping[str, int]
    run_id: str
    source_commit_hashes: tuple[str, ...]
    state_hash: str
    _proof: object = field(repr=False)


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
    return RecoveredProgramPolicy(actor, sequence, len(seen), completions, MappingProxyType(completed),
        run_id, source_hashes, program_policy_fingerprint(actor), _RECOVERY_PROOF)


def verify_recovered_program_positions(packet, template, *, run_id, source_hashes,
        at, broker_positions, broker_ledgers, broker_executions, broker_orders, order_manager,
        portfolio, portfolio_recovery, observations, population):
    """Verify common filled geometry without executing a strategy or OMS command."""
    from src.trading_runtime.ibkr_schema import PortfolioPosition
    from src.trading_runtime.domain import OrderState
    from src.trading_runtime.oms_assignment_observation import AssignmentOrderObservation
    from src.trading_runtime.arte_portfolio_recovery import PortfolioRecovery
    from src.trading_runtime.arte_portfolio_snapshot import capture_portfolio_snapshot
    from decimal import localcontext
    if (type(packet) is not RecoveredProgramPolicy or packet._proof is not _RECOVERY_PROOF
            or packet.run_id != run_id or packet.source_commit_hashes != tuple(source_hashes)
            or packet.actor.program_release.header != template.program_release.header
            or packet.actor.assignments() != template.assignments()
            or packet.actor._listings != template._listings
            or packet.actor.execution_state.pending_fee_count
            or type(portfolio_recovery) is not PortfolioRecovery
            or type(population) is not CertifiedPopulationWindow
            or portfolio_recovery.run_id != run_id
            or set(portfolio_recovery.states) != set(broker_positions)
            or set(portfolio.states) != set(broker_positions)
            or set(broker_ledgers) != set(broker_positions)
            or any(value != packet.journal_sequence for value in portfolio_recovery.revisions.values())
            or set(portfolio_recovery.revisions) != set(broker_positions)
            or portfolio._last_filled_by_reservation != portfolio_recovery.last_filled_by_reservation):
        raise ValueError("Recovered program installation scope or fee coverage differs")
    if program_policy_fingerprint(packet.actor) != packet.state_hash:
        raise ValueError("Recovered program policy changed after cold qualification")
    executions = tuple(broker_executions)
    if (len(executions) != packet.execution_count
            or any(type(execution) is not Execution for execution in executions)
            or {execution.execution_id for execution in executions} != set(packet.actor.execution_state._fills)):
        raise ValueError("Recovered program broker execution cache differs")
    for first in range(0, len(executions), 4096):
        # Every ID is already present. Exact dedup checks also verify actual OMS
        # role attribution and final fees; these calls cannot create a fill.
        packet.actor.on_broker_executions(executions[first:first + 4096], order_manager)
    at = _clock(at)
    expected = {}
    expected_orders = {}
    required = set()
    for assignment in packet.actor.assignments():
        position = packet.actor.position_for(assignment.assignment_id)
        observation = observations.get(assignment.assignment_id)
        if (type(observation) is not AssignmentOrderObservation
                or (observation.account_id, observation.assignment_id, observation.ticker, observation.observed_at)
                   != (assignment.account_id, assignment.assignment_id, assignment.ticker, at)
                or observation.unobserved_order_ids):
            raise ValueError("Recovered program installation lacks completed OMS observations")
        if position.position_open:
            expected[(assignment.account_id, assignment.conid, assignment.ticker)] = (
                position.quantity, position.average_price)
        for order in observation.working_orders:
            if order.broker_order_id in expected_orders:
                raise ValueError("Recovered program OMS order ownership is duplicate")
            expected_orders[order.broker_order_id] = (assignment.account_id, assignment.conid,
                assignment.ticker, "BUY" if order.role == "entry" else "SELL",
                Decimal(str(order.remaining_quantity)), Decimal(str(order.limit_price)), Decimal(str(order.stop_price)))
        if (position.position_open or observation.pending_acquisition or observation.pending_reduction
                or population.is_ranked(position.listing_id, at)):
            required.add(assignment.assignment_id)
    if any(packet.completed_source_sequences.get(identity) != len(source_hashes) for identity in required):
        raise ValueError("Recovered program installation has incomplete current source decisions")
    actual_orders = {}
    for order in broker_orders:
        if type(order) is not OrderState:
            raise ValueError("Recovered program broker order inventory is untyped")
        if order.terminal or order.remaining_quantity <= 0:
            continue
        if order.broker_order_id in actual_orders:
            raise ValueError("Recovered program broker order inventory is duplicate")
        actual_orders[order.broker_order_id] = (order.account_id, order.instrument.conid,
            order.instrument.symbol, order.side.upper(), order.remaining_quantity,
            order.limit_price or Decimal(0), order.stop_price or Decimal(0))
    if actual_orders != expected_orders:
        raise ValueError("Recovered program OMS orders differ from current broker inventory")

    def geometry(rows, account):
        result = {}
        with localcontext() as context:
            context.prec = 80
            for row in rows:
                if type(row) is not PortfolioPosition or row.acctId != account:
                    raise ValueError("Recovered program financial position identity differs")
                quantity = Decimal(str(row.position)).quantize(Decimal("0.0000000001"))
                average = Decimal(str(row.avgPrice)).quantize(Decimal("0.0000000001"))
                if not quantity.is_finite() or not average.is_finite() or quantity < 0:
                    raise ValueError("Recovered program financial position is invalid")
                if quantity:
                    key = (account, row.conid, row.contractDesc.upper())
                    if key in result:
                        raise ValueError("Recovered program financial position is duplicate")
                    result[key] = (quantity, average)
        return result
    actual_broker, actual_portfolio = {}, {}
    for account, rows in broker_positions.items():
        state = portfolio.states.get(account)
        # Historical execution/order update times can predate the common
        # snapshot; the current broker order inventory was verified above.
        if (state is None or state.observed_at is None or state.observed_at > at
                or any(state.component_watermarks.get(component) != at for component in ("summary", "ledger", "positions"))
                or state.component_watermarks.get("orders") is None or state.component_watermarks["orders"] > at):
            raise ValueError(f"Recovered program Portfolio snapshot differs from the source clock: "
                f"source={at.isoformat()} observed={getattr(state, 'observed_at', None)} "
                f"components={getattr(state, 'component_watermarks', {})}")
        expected_state = portfolio_recovery.states[account]
        if broker_ledgers[account] != expected_state.ledger or state.ledger != expected_state.ledger:
            raise ValueError("Recovered program broker or Portfolio cash ledger differs")
        actual_capture = portfolio.capture_recovery_snapshot(account,
            state_revision=packet.journal_sequence, snapshot_at=at)
        expected_capture = capture_portfolio_snapshot(run_id=run_id,
            state_revision=packet.journal_sequence, snapshot_at=at, state=expected_state,
            reservations=portfolio_recovery.reservations.values(),
            allocations=portfolio_recovery.allocations.values(),
            reconciliation=portfolio_recovery.differences.values())
        if actual_capture != expected_capture:
            raise ValueError("Recovered program full Portfolio image differs")
        actual_broker.update(geometry(rows, account))
        actual_portfolio.update(geometry(state.positions.values(), account))
    with localcontext() as context:
        context.prec = 80
        expected = {key: tuple(value.quantize(Decimal("0.0000000001")) for value in values)
                    for key, values in expected.items()}
    if actual_broker != expected or actual_portfolio != expected:
        raise ValueError("Recovered program positions differ from broker or Portfolio")


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
