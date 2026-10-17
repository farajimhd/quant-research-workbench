"""Session scheduling over the existing prepared native cohort/fence API.

Preparation remains in QMD/scanner/Signal Stream producers and the admitted
compute funnel. This scheduler neither reads research files nor builds features.
"""
from dataclasses import dataclass, replace
from types import MappingProxyType
from collections.abc import Mapping
from datetime import timedelta
import asyncio
from itertools import islice


def program_terminal_cutoff(session_end, simulation_config, evaluation_interval_ms):
    """Last closing window that leaves an activation-eligible final bar.

    OMS may first cancel a remaining acquisition, then request the reduction
    on the next programme clock. Reserve that clock as well as broker activation
    and the final bar; this provides an opportunity, never a guaranteed fill.
    """
    from datetime import datetime, timezone
    from decimal import Decimal, ROUND_CEILING
    from src.trading_runtime.simulated_broker import SimulationConfig
    if (not isinstance(session_end, datetime) or session_end.tzinfo is None
            or session_end.utcoffset() is None or type(simulation_config) is not SimulationConfig
            or type(evaluation_interval_ms) is not int or evaluation_interval_ms != 1000
            or simulation_config.liquidity_resolution_ms not in (100, 1000)):
        raise ValueError("Programme closing window requires its native clock and broker contract")
    resolution_us = simulation_config.liquidity_resolution_ms * 1000
    activation_us = int((Decimal(str(simulation_config.new_order_activation_delay_ms)) * 1000)
        .to_integral_value(rounding=ROUND_CEILING))
    # The 1s broker checks activation at bar start; the 100ms broker checks
    # activation at the completed boundary, while submission precedes bar start.
    lead_us = (resolution_us + activation_us if simulation_config.liquidity_resolution_ms == 1000
        else max(resolution_us, activation_us))
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    delta = session_end.astimezone(timezone.utc) - epoch
    end_us = (delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds
    step_us = evaluation_interval_ms * 1000
    cutoff_us = ((end_us - lead_us) // step_us - 1) * step_us
    return epoch + timedelta(microseconds=cutoff_us)


@dataclass(frozen=True)
class PreparedProgramCohortWork:
    first_bucket: int
    stop_bucket: int
    blocks: Mapping
    population: object
    projection: object
    scope: Mapping

    def __post_init__(self):
        if (type(self.first_bucket) is not int or type(self.stop_bucket) is not int
                or not 14400 <= self.first_bucket < self.stop_bucket <= 72000
                or self.stop_bucket - self.first_bucket > 256
                or not isinstance(self.blocks, Mapping) or not isinstance(self.scope, Mapping)
                or {"controller", "blocks", "population", "projection", "first_bucket", "stop_bucket", "initial_packet", "terminal"} & set(self.scope)):
            raise ValueError("Programme cohort work lacks bounded controller-owned scheduling")
        from inspect import signature, Parameter
        from .backtest_program_cohort import run_prepared_program_cohort
        parameters = signature(run_prepared_program_cohort).parameters
        allowed = {name for name, value in parameters.items() if value.kind is Parameter.KEYWORD_ONLY}
        required = {name for name in allowed if parameters[name].default is Parameter.empty}
        required -= {"first_bucket", "stop_bucket"}
        if set(self.scope) - allowed or required - set(self.scope):
            raise ValueError("Programme cohort work differs from its complete native scope")
        object.__setattr__(self, "blocks", MappingProxyType(dict(self.blocks)))
        object.__setattr__(self, "scope", MappingProxyType(dict(self.scope)))


@dataclass(frozen=True)
class ProgramSessionResult:
    status: str
    completed_cohorts: int
    planned_cohorts: int
    liquidity_boundaries: int
    source_clocks: int
    qualified_decisions: int
    policy_packet: object


def require_program_session_owner(controller):
    if (getattr(controller, "_program_checkpoint_active", False)
            and getattr(controller, "_program_checkpoint_task", None) is not asyncio.current_task()):
        raise RuntimeError("Programme checkpoint already owns controller dispatch")
    if getattr(controller, "_runtime_finished", False):
        raise RuntimeError("Programme runtime has already finished")
    if getattr(controller, "_program_terminal_active", False):
        raise RuntimeError("Programme terminal already owns controller dispatch")
    if (getattr(controller, "_program_session_active", False)
            and getattr(controller, "_program_session_task", None) is not asyncio.current_task()):
        raise RuntimeError("Programme session already owns controller dispatch")


async def run_prepared_program_session(controller, cohorts, *, initial_packet=None, terminal=True):
    """Evaluate contiguous prepared session coverage, fencing each bounded unit.

    `evaluated` means the requested clocks are fenced. Terminal lifecycle,
    financial captures and close publication still belong to the main run owner.
    """
    from .replay_run_service import ReplayRunController
    require_program_session_owner(controller)
    if (type(controller) is not ReplayRunController or type(terminal) is not bool
            or isinstance(cohorts, (str, bytes))
            or getattr(controller, "_program_session_active", False)):
        raise ValueError("Programme session lacks its exclusive native owner")
    work, definition, actor = validate_prepared_program_session(controller, cohorts)
    controller._program_session_active = True
    controller._program_session_task = asyncio.current_task()
    controller._program_evaluated_terminal = None
    packet, completed, liquidity, clocks, decisions = initial_packet, 0, 0, 0, 0
    try:
        if controller.current_time is not None:
            work = await _continue_fenced_session_work(controller, work, initial_packet)
        for index, unit in enumerate(work):
            if controller._stop_requested:
                return ProgramSessionResult("stopped", completed, len(work), liquidity, clocks, decisions, packet)
            if controller.definition is not definition or controller._program_strategy is not actor:
                raise RuntimeError("Programme session ownership changed before its next cohort")
            result = await controller.run_fenced_program_cohort(unit.blocks, unit.population, unit.projection,
                **unit.scope, first_bucket=unit.first_bucket, stop_bucket=unit.stop_bucket,
                initial_packet=packet, terminal=terminal)
            packet = result.policy_packet
            liquidity += result.liquidity_boundaries
            clocks += result.source_clocks
            decisions += result.qualified_decisions
            if result.status not in ("completed", "stopped"):
                raise RuntimeError("Programme cohort returned an unrecognized completion state")
            if result.status == "stopped":
                return ProgramSessionResult("stopped", completed, len(work), liquidity, clocks, decisions, packet)
            completed += 1
        if terminal:
            controller._program_evaluated_terminal = (definition, controller._runtime, actor,
                controller.current_time, controller._journal_publisher.fenced_sequence)
        return ProgramSessionResult("evaluated", completed, len(work), liquidity, clocks, decisions, packet)
    finally:
        controller._program_session_active = False
        controller._program_session_task = None


async def _continue_fenced_session_work(controller, work, packet):
    """Retain the original feature origin after an owned completed cursor.

    This is a continuation gate for an already adopted native runtime, never
    cold-owner admission. Unfenced or merely timestamped state cannot skip work.
    """
    from .program_policy_recovery import (RecoveredProgramPolicy, _RECOVERY_PROOF,
        _ProgramPolicyContinuationSeal, program_policy_fingerprint)
    from .backtest_typed_publisher import BacktestTypedJournalPublisher
    from .backtest_v4_keeper_lease import BacktestV4KeeperLease
    from .qmd_columnar_history_transport import _owned_worker
    from .backtest_market_data import market_day_boundary, SESSION_OPEN_OFFSET_MS
    from .signal_stream_runtime_service import SignalStreamRuntime
    seal = getattr(packet, "_continuation_seal", None)
    runtime, actor, source = controller._runtime, controller._program_strategy, controller._program_signal_runtime
    journal, publisher, lease = controller._journal, controller._journal_publisher, controller._fixed_v4_lease
    at = controller.current_time
    market_cursor = tuple(sorted(controller._source_cursor.items()))
    writer = getattr(publisher, "writer", None)
    if (type(packet) is not RecoveredProgramPolicy or packet._proof is not _RECOVERY_PROOF
            or type(seal) is not _ProgramPolicyContinuationSeal or seal.token is not _RECOVERY_PROOF
            or (packet.run_id, packet.journal_sequence, packet.execution_count, packet.completion_count,
                tuple(sorted(packet.completed_source_sequences.items())), packet.source_commit_hashes, packet.state_hash)
               != (seal.run_id, seal.journal_sequence, seal.execution_count, seal.completion_count,
                   seal.completed, seal.source_hashes, seal.state_hash)
            or runtime is None or runtime.strategy is not actor or runtime.journal is not journal
            or runtime.last_event_time != at or type(source) is not SignalStreamRuntime
            or type(publisher) is not BacktestTypedJournalPublisher or publisher.journal is not journal
            or publisher._error is not None or publisher._terminal_task is not None
            or publisher.exact_oms_prices is not True
            or publisher._task is not None and not publisher._task.done()
            or publisher.fenced_sequence != packet.journal_sequence
            or journal.latest_sequence(controller.run_id) != packet.journal_sequence or journal.pending_record_count
            or packet.run_id != controller.run_id
            or packet.state_hash != program_policy_fingerprint(actor)
            or packet.state_hash != program_policy_fingerprint(packet.actor)
            or type(lease) is not BacktestV4KeeperLease or lease.run_id != controller.run_id
            or getattr(getattr(publisher.writer, "_client", None), "backtest_v4_lease", None) is not lease):
        raise ValueError("Programme continuation lacks its owned completed policy fence")
    cursor = source.typed_source_cursor()
    boundary = source.typed_source_boundary(at)
    if cursor != boundary or packet.source_commit_hashes != source.typed_source_commit_hashes()[:boundary[0]]:
        raise ValueError("Programme continuation source prefix differs from its policy fence")
    elapsed = at - market_day_boundary(controller.definition.session_date, 0)
    micros = (elapsed.days * 86400 + elapsed.seconds) * 1000000 + elapsed.microseconds
    if micros % 1000000:
        raise ValueError("Programme continuation requires a completed whole-second cursor")
    bucket = micros // 1000000 + SESSION_OPEN_OFFSET_MS // 1000
    terminal_complete = bucket == work[-1].stop_bucket and at == controller.definition.session_end
    if not work[0].first_bucket <= bucket < work[-1].stop_bucket and not terminal_complete:
        raise ValueError("Programme continuation cursor is outside remaining session coverage")
    if (controller._source_cursor.get("session_date") != controller.definition.session_date.isoformat()
            or controller._source_cursor.get("boundary_ms") != micros // 1000):
        raise ValueError("Programme continuation lacks its completed market cursor")
    await _owned_worker(lease.assert_current)
    if (controller._runtime is not runtime or controller._program_strategy is not actor
            or controller._journal is not journal or controller._journal_publisher is not publisher
            or controller._fixed_v4_lease is not lease or controller._program_signal_runtime is not source
            or controller.current_time != at or runtime.last_event_time != at
            or tuple(sorted(controller._source_cursor.items())) != market_cursor
            or source.typed_source_cursor() != cursor or source.typed_source_boundary(at) != boundary
            or packet.source_commit_hashes != source.typed_source_commit_hashes()[:boundary[0]]
            or journal.latest_sequence(controller.run_id) != packet.journal_sequence or journal.pending_record_count
            or publisher.fenced_sequence != packet.journal_sequence
            or publisher.writer is not writer or publisher._error is not None
            or publisher._terminal_task is not None
            or publisher._task is not None and not publisher._task.done()
            or program_policy_fingerprint(actor) != packet.state_hash
            or program_policy_fingerprint(packet.actor) != packet.state_hash):
        raise RuntimeError("Programme continuation changed during owner verification")
    if terminal_complete:
        return ()
    remaining = tuple(unit for unit in work if unit.stop_bucket > bucket)
    return (replace(remaining[0], first_bucket=bucket),) + remaining[1:]


def validate_prepared_program_session(controller, cohorts):
    """Check the complete requested plan without advancing any runtime clock."""
    from .backtest_program_inputs import PreparedProgramBlock, PreparedProgramScannerProjection
    from .qmd_columnar_population import CertifiedPopulationWindow
    from .backtest_market_data import market_day_boundary, SESSION_OPEN_OFFSET_MS
    work = tuple(islice(cohorts, 57601))
    if not work or len(work) > 57600 or any(type(unit) is not PreparedProgramCohortWork for unit in work):
        raise ValueError("Programme session needs bounded prepared cohort coverage")
    definition, actor = controller.definition, controller._program_strategy
    if actor is None or controller._program_dispatch_failure is not None:
        raise RuntimeError("Programme session requires its available programme runtime")
    day = definition.session_date.isoformat()
    start = market_day_boundary(day, 0)
    first_ms = (definition.requested_start - start) / timedelta(milliseconds=1) + SESSION_OPEN_OFFSET_MS
    stop_ms = (definition.session_end - start) / timedelta(milliseconds=1) + SESSION_OPEN_OFFSET_MS
    if first_ms % 1000 or stop_ms % 1000:
        raise ValueError("Programme session scheduling requires explicit whole-second endpoints")
    first, stop = int(first_ms // 1000), int(stop_ms // 1000)
    if work[0].first_bucket != first or work[-1].stop_bucket != stop:
        raise ValueError("Programme session cohorts do not cover the requested endpoints")
    required = set(actor._by_assignment)
    prior = first
    revision = None
    for unit in work:
        if (unit.first_bucket != prior or set(unit.blocks) != required
                or type(unit.population) is not CertifiedPopulationWindow
                or type(unit.projection) is not PreparedProgramScannerProjection
                or unit.projection.release_hash != actor.program_release.header["content_hash"]
                or unit.projection.source_revision != unit.population.source_revision
                or any(type(block) is not PreparedProgramBlock
                    or block.release_hash != actor.program_release.header["content_hash"]
                    for block in unit.blocks.values())):
            raise ValueError("Programme session has a gap, overlap or changed prepared authority")
        if revision is None:
            revision = unit.population.source_revision
        if unit.population.source_revision != revision:
            raise ValueError("Programme session changed its scanner population authority")
        prior = unit.stop_bucket
    return work, definition, actor
