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
from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix, load_verified_v4_prefix


_RECOVERY_PROOF = object()


def project_pending_program_policy_events(records, *, executions, run_month,
        attempt_id, batch_id, prior_batch_id, source_cursor, exact_prices=False):
    """Normalize an owned pending suffix using the shared journal projector.

    These rows carry no durable receipt; caller custody and independent native
    financial comparison remain required before pending decision qualification.
    """
    from src.trading_runtime.arte_journal_projection import project_journal_record
    from src.trading_runtime.arte_execution_exact_prices import ExactPriceExecutionBatch
    if type(exact_prices) is not bool:
        raise ValueError("Pending policy price mode must be an explicit boolean")
    raw_keys = ("strategy_id", "canonical_strategy_revision", "canonical_run_id", "canonical_metadata")
    lineages = {execution.order_ref: ({key: execution.raw.get(key) for key in raw_keys},
        execution.account, execution.symbol, execution.conid) for execution in executions}
    result = []
    for record in records:
        detail, family = None, None
        event = dict(run_id=record.run_id, sequence=record.sequence,
            category=record.category, entity_type=record.entity_type, entity_id=record.entity_id,
            event_time=record.event_time, account_id=record.account_id,
            correlation_id=record.payload.get("correlation_id", ""),
            causation_id=record.payload.get("causation_id", ""))
        if record.category == "execution" or record.payload.get("signal_type") == "program_boundary_completed":
            unit = project_journal_record(record, run_month=run_month, attempt_id=attempt_id,
                batch_id=batch_id, prior_batch_id=prior_batch_id, source_cursor=source_cursor,
                committed_order_lineage=lineages,
                **({"expected_mode": "backtest", "exact_execution_prices": True} if exact_prices else {}))
            batch = unit.base if type(unit) is ExactPriceExecutionBatch else unit
            event = dict(batch.events[0])
            if record.entity_type == "fill":
                family = "trading_execution_v1"
                if type(unit) is ExactPriceExecutionBatch:
                    from src.trading_runtime.trading_price_contracts import transform_price_fields
                    from src.trading_runtime.arte_journal_writer import _CONTRACTS
                    detail = transform_price_fields(family,
                        {key: value for key, value in unit.execution.items() if key != "content_hash"},
                        _CONTRACTS, restore=True)
                else:
                    detail = dict(batch.executions[0])
            elif record.entity_type == "commission":
                family, detail = "trading_commission_v1", dict(batch.commissions[0])
            else:
                family, detail = "trading_strategy_signal_v2", dict(batch.signals[0])
        result.append(TypedJournalEvent(event, family, detail))
    return tuple(result)


async def rebuild_owned_pending_program_policy(controller, blocks, population, *,
        maximum_events, initial_packet=None):
    """Replay the bounded retained suffix and verify its owned pre-decision state.

    A cold restored prefix must be supplied when journal compaction has removed
    earlier records. This never substitutes memory for a durable cold receipt.
    """
    from .replay_run_service import ReplayRunController
    from .backtest_typed_publisher import BacktestTypedJournalPublisher
    from .signal_stream_runtime_service import SignalStreamRuntime
    from src.trading_runtime.runtime import TradingRuntime, RunMode
    from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter
    if (type(controller) is not ReplayRunController or type(maximum_events) is not int
            or not 1 <= maximum_events <= 1_000_000
            or type(population) is not CertifiedPopulationWindow):
        raise ValueError("Owned policy replay requires its bounded native scope")
    runtime, journal, actor, source, publisher = (controller._runtime, controller._journal,
        controller._program_strategy, controller._program_signal_runtime, controller._journal_publisher)
    if (type(runtime) is not TradingRuntime or runtime.config.mode is not RunMode.BACKTEST
            or type(runtime.broker) is not SimulatedBrokerAdapter or runtime.journal is not journal
            or type(actor) is not AssignedProgramStrategy or runtime.strategy is not actor
            or type(source) is not SignalStreamRuntime or source._typed_scope is None
            or type(publisher) is not BacktestTypedJournalPublisher or publisher.journal is not journal
            or population.source_revision != source._typed_scope[1]):
        raise ValueError("Owned policy replay lacks its controller actors")
    sequence = journal.latest_sequence(controller.run_id)
    at = runtime.last_event_time
    boundary = source.typed_source_boundary(at)
    source_hashes = source.typed_source_commit_hashes()[:boundary[0]]
    fence = (publisher.fenced_sequence, publisher._batch_id, publisher._source_cursor)
    state_hash = program_policy_fingerprint(actor)
    manager, broker, config = runtime.order_manager, runtime.broker, runtime.config
    if initial_packet is not None and type(initial_packet) is not RecoveredProgramPolicy:
        raise ValueError("Owned policy replay requires a sealed continuation packet")
    after = 0 if initial_packet is None else initial_packet.journal_sequence
    if not 0 <= after <= sequence or sequence - after > maximum_events:
        raise ValueError("Owned policy replay exceeds its declared journal bound")
    blocks = tuple(blocks)
    def require_scope():
        if (controller._runtime is not runtime or controller._journal is not journal
                or controller._program_strategy is not actor or controller._program_signal_runtime is not source
                or controller._journal_publisher is not publisher or runtime.order_manager is not manager
                or runtime.broker is not broker or runtime.config is not config or runtime.last_event_time != at
                or journal.latest_sequence(controller.run_id) != sequence
                or source.typed_source_boundary(at) != boundary
                or (publisher.fenced_sequence, publisher._batch_id, publisher._source_cursor) != fence
                or publisher._error is not None or publisher._terminal_task is not None
                or publisher._task is not None and not publisher._task.done()
                or program_policy_fingerprint(actor) != state_hash):
            raise ValueError("Owned policy replay changed during reconstruction")
    require_scope()
    executions = tuple(await broker.trades())
    require_scope()
    def rebuild():
        events = project_pending_program_policy_events(journal.records(controller.run_id, after_sequence=after),
            executions=executions, run_month=publisher.run_month, attempt_id=publisher.attempt_id,
            batch_id=publisher.attempt_id, prior_batch_id=fence[1], source_cursor=fence[2],
            exact_prices=publisher.exact_oms_prices)
        return rebuild_program_policy_events(actor, events, run_id=controller.run_id,
            source_hashes=source_hashes, blocks=blocks, population=population,
            executions=executions, order_manager=manager, maximum_events=maximum_events,
            last_sequence=sequence, initial_packet=initial_packet)
    packet = await _owned_worker(rebuild)
    require_scope()
    if packet.state_hash != state_hash:
        raise ValueError("Owned policy replay differs from the actual pre-decision actor")
    return packet


def _position_geometry(rows, account):
    from src.trading_runtime.ibkr_schema import PortfolioPosition
    from decimal import localcontext
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


def verify_program_filled_geometry(actor, broker_positions, portfolio):
    """Compare typed filled geometry; financial clocks/ownership remain separate proofs."""
    from decimal import localcontext
    if (type(actor) is not AssignedProgramStrategy
            or set(broker_positions) != set(portfolio.states)
            or set(broker_positions) != {row.account_id for row in actor.assignments()}):
        raise ValueError("Recovered program geometry account scope differs")
    expected = {}
    with localcontext() as context:
        context.prec = 80
        for assignment in actor.assignments():
            position = actor.position_for(assignment.assignment_id)
            if position.position_open:
                key = (assignment.account_id, assignment.conid, assignment.ticker)
                if key in expected:
                    raise ValueError("Recovered program geometry position ownership repeats")
                expected[key] = tuple(value.quantize(Decimal("0.0000000001"))
                    for value in (position.quantity, position.average_price))
    actual_broker, actual_portfolio = {}, {}
    for account, rows in broker_positions.items():
        actual_broker.update(_position_geometry(rows, account))
        actual_portfolio.update(_position_geometry(portfolio.states[account].positions.values(), account))
    if actual_broker != expected or actual_portfolio != expected:
        raise ValueError("Recovered program positions differ from broker or Portfolio")


def program_policy_fingerprint(actor):
    """Seal the complete derived ledger and policy caches in memory only."""
    return sha256(canonical_json(_program_policy_fingerprint_value(actor)).encode()).hexdigest()


def _program_policy_fingerprint_value(actor):
    from dataclasses import fields, is_dataclass
    def scalar(value):
        if isinstance(value, Decimal):
            if not value.is_finite():
                raise ValueError("Program policy contains a non-finite financial value")
            text = format(value, "f")
            text = text.rstrip("0").rstrip(".") if "." in text else text
            return "0" if value == 0 else text
        if is_dataclass(value):
            return {item.name: scalar(getattr(value, item.name)) for item in fields(value)}
        if isinstance(value, Mapping):
            return {key: scalar(item) for key, item in value.items()}
        if isinstance(value, (tuple, list)):
            return [scalar(item) for item in value]
        return value
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
    return scalar(value)


@dataclass(frozen=True, slots=True)
class _ProgramPolicyContinuationSeal:
    run_id: str
    journal_sequence: int
    execution_count: int
    completion_count: int
    completed: tuple
    source_hashes: tuple
    state_hash: str
    token: object = field(repr=False)


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
    _continuation_seal: object = field(default=None, repr=False)


def _clock(value):
    at = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("Program policy recovery requires timezone-aware clocks")
    return at


def rebuild_program_policy_events(template, events, *, run_id, source_hashes,
        blocks, population, executions, order_manager, maximum_events, last_sequence,
        initial_packet=None):
    """Consume verified journal order, never guessed timestamp sorting or signals as fills."""
    if (type(template) is not AssignedProgramStrategy
            or type(population) is not CertifiedPopulationWindow
            or type(maximum_events) is not int or maximum_events < 1
            or type(last_sequence) is not int or not 0 <= last_sequence < 2**64
            or initial_packet is None and last_sequence > maximum_events):
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
    if initial_packet is not None:
        from copy import deepcopy
        seal = getattr(initial_packet, "_continuation_seal", None)
        if (type(initial_packet) is not RecoveredProgramPolicy or initial_packet._proof is not _RECOVERY_PROOF
                or type(seal) is not _ProgramPolicyContinuationSeal or seal.token is not _RECOVERY_PROOF
                or (initial_packet.run_id, initial_packet.journal_sequence, initial_packet.execution_count,
                    initial_packet.completion_count, tuple(sorted(initial_packet.completed_source_sequences.items())),
                    initial_packet.source_commit_hashes, initial_packet.state_hash)
                   != (seal.run_id, seal.journal_sequence, seal.execution_count, seal.completion_count,
                       seal.completed, seal.source_hashes, seal.state_hash)
                or initial_packet.run_id != run_id or not 0 <= initial_packet.journal_sequence <= last_sequence
                or source_hashes[:len(initial_packet.source_commit_hashes)] != initial_packet.source_commit_hashes
                or initial_packet.actor.program_release.header != template.program_release.header
                or initial_packet.actor.assignments() != template.assignments()
                or initial_packet.actor._listings != template._listings
                or program_policy_fingerprint(initial_packet.actor) != initial_packet.state_hash):
            raise ValueError("Program policy continuation differs from its verified packet")
        actor.execution_state = deepcopy(initial_packet.actor.execution_state)
        actor.target_state = deepcopy(initial_packet.actor.target_state)
        actor.trail_state = deepcopy(initial_packet.actor.trail_state)
        actor._selection_clocks = dict(initial_packet.actor._selection_clocks)
        seen, fees = set(actor.execution_state._fills), set(actor.execution_state._fees)
        completed = dict(initial_packet.completed_source_sequences)
        sequence, completions = initial_packet.journal_sequence, initial_packet.completion_count
        if (seen != fees or len(seen) != initial_packet.execution_count or not seen <= set(cached)
                or any(type(value) is not int or not 1 <= value <= len(initial_packet.source_commit_hashes)
                       for value in completed.values())):
            raise ValueError("Program policy continuation ledger coverage differs")
        before = program_policy_fingerprint(actor)
        for identity in sorted(seen):
            execution = cached[identity]
            if execution.commission is None:
                raise ValueError("Program policy continuation execution fee is missing")
            actor.on_broker_executions((execution,), order_manager)
        if program_policy_fingerprint(actor) != before:
            raise ValueError("Program policy continuation changed its prior ledger")
    if last_sequence - sequence > maximum_events:
        raise ValueError("Program policy continuation suffix exceeds its declared journal bound")
    first_sequence = sequence
    for item in events:
        sequence += 1
        if (sequence - first_sequence > maximum_events or sequence > last_sequence or type(item) is not TypedJournalEvent
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
    if initial_packet is not None and program_policy_fingerprint(initial_packet.actor) != initial_packet.state_hash:
        raise ValueError("Program policy packet changed during continuation")
    state_hash = program_policy_fingerprint(actor)
    seal = _ProgramPolicyContinuationSeal(run_id, sequence, len(seen), completions,
        tuple(sorted(completed.items())), source_hashes, state_hash, _RECOVERY_PROOF)
    return RecoveredProgramPolicy(actor, sequence, len(seen), completions, MappingProxyType(completed),
        run_id, source_hashes, state_hash, _RECOVERY_PROOF, seal)


def verify_recovered_program_positions(packet, template, *, run_id, source_hashes,
        at, broker_positions, broker_ledgers, broker_executions, broker_orders, order_manager,
        portfolio, portfolio_recovery, observations, population):
    """Verify common filled geometry without executing a strategy or OMS command."""
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
        actual_broker.update(_position_geometry(rows, account))
        actual_portfolio.update(_position_geometry(state.positions.values(), account))
    with localcontext() as context:
        context.prec = 80
        expected = {key: tuple(value.quantize(Decimal("0.0000000001")) for value in values)
                    for key, values in expected.items()}
    if actual_broker != expected or actual_portfolio != expected:
        raise ValueError("Recovered program positions differ from broker or Portfolio")


async def recover_program_policy_async(reader_factory, prefix, template, *, through_sequence=None,
        exact_prices=False, **scope):
    """Read exact committed pages on an owned worker and close its SELECT client."""
    if (type(exact_prices) is not bool or type(prefix) is not V4CommittedPrefix or not prefix.batch_ids
            or through_sequence is not None and (type(through_sequence) is not int
                or not 0 <= through_sequence <= prefix.last_sequence)):
        raise ValueError("Program policy recovery requires an exact bounded historical prefix")
    basis_sequence = prefix.last_sequence if through_sequence is None else through_sequence
    def run():
        client = reader_factory()
        try:
            if client.execute("SELECT getSetting('readonly')").strip() != "1":
                raise ValueError("Program policy recovery requires a SELECT-only reader")
            if load_verified_v4_prefix(client, prefix.run_id) != prefix:
                raise ValueError("Program policy prefix moved before readback")
            def events():
                packet = scope.get("initial_packet")
                # The replay validator proves the continuation seal before
                # this generator is consumed; never reread its prior prefix.
                after = 0 if packet is None else packet.journal_sequence
                while after < basis_sequence:
                    page = load_typed_event_page(client, prefix, after_sequence=after,
                        limit=min(1000, basis_sequence - after),
                        **({"exact_prices": True} if exact_prices else {}))
                    if not page:
                        raise ValueError("Program policy recovery page did not advance")
                    if int(page[-1].event["sequence"]) > basis_sequence:
                        raise ValueError("Program policy recovery page exceeds historical basis")
                    yield from page
                    after = int(page[-1].event["sequence"])
            result = rebuild_program_policy_events(template, events(), run_id=prefix.run_id,
                last_sequence=basis_sequence, **scope)
            if load_verified_v4_prefix(client, prefix.run_id) != prefix:
                raise ValueError("Program policy prefix moved during readback")
            return result
        finally:
            client.close()
    return await _owned_worker(run)
