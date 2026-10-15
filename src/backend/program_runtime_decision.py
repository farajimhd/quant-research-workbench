"""Current native program decisions qualified independently of Backtest replay.

Capture precedes selection and binds the actual committed QMD clock, ledger,
OMS observation, typed Portfolio head and installed writer. Qualification uses
the same pure selector on a detached actor; no fill or broker fact is invented.
"""
from copy import deepcopy
from dataclasses import dataclass, field, replace, asdict, fields
from collections.abc import Mapping
from hashlib import sha256

from .program_execution_journal import ProgramExecutionJournal
from .program_policy_recovery import program_policy_fingerprint
from .backtest_program_inputs import PreparedProgramBlock
from .qmd_columnar_population import CertifiedPopulationWindow
from .signal_stream_runtime_service import SignalStreamRuntime
from src.trading_runtime.columnar_program_actor import AssignedProgramStrategy
from src.trading_runtime.oms_assignment_observation import observe_assignment_orders
from src.trading_runtime.program_portfolio_transitions import (
    ProgramPortfolioTransitionAuthority, ProgramPortfolioTransitionReceipt,
)
from src.trading_runtime.rule_decision_observation import observation_digest
from src.trading_runtime.rule_decision_basis_projection import rule_decision_semantic_hash


_CAPTURE = object()
_QUALIFIED = object()


@dataclass(frozen=True, slots=True)
class _ProgramCaptureSeal:
    runtime: object
    journal: object
    actor: object
    source: object
    block: object
    population: object
    replay_actor: object
    digest: str
    token: object = field(repr=False)


def _capture_digest(capture):
    from src.trading_runtime.journal_contract import canonical_json
    boundary = {f.name: dict(value) if isinstance(value := getattr(capture.boundary, f.name), Mapping) else value
                for f in fields(capture.boundary)}
    value = dict(assignment=capture.assignment_id, ordinal=capture.ordinal, basis=capture.basis_sequence,
        source=capture.source_boundary, boundary=boundary, observation=capture.observation_hash,
        head=asdict(capture.portfolio_head), policy=capture.policy_hash,
        release=capture.actor.program_release.header['content_hash'],
        history=capture.block.history_hashes[capture.boundary.listing_id])
    return sha256(canonical_json(value).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class ProgramRuntimeDecisionCapture:
    runtime: object = field(repr=False)
    journal: object = field(repr=False)
    actor: object = field(repr=False)
    source: object = field(repr=False)
    block: object = field(repr=False)
    population: object = field(repr=False)
    assignment_id: str
    ordinal: int
    basis_sequence: int
    source_boundary: tuple
    boundary: object
    observation: object
    observation_hash: str
    portfolio_head: object
    policy_hash: str
    replay_actor: object = field(repr=False)
    token: object = field(repr=False)


@dataclass(frozen=True, slots=True)
class _ProgramRuntimeDecisionEvidence:
    capture: ProgramRuntimeDecisionCapture
    run_id: str
    basis_sequence: int
    semantic_hash: str
    observation: object
    observation_hash: str
    selected_policy_hash: str
    selection: object
    capture_digest: str
    token: object = field(repr=False)


@dataclass(frozen=True, slots=True)
class ProgramRuntimeDecisionQualification:
    run_id: str
    basis_sequence: int
    semantic_hash: str
    selection: object
    _proof: object = field(repr=False)


def _require_capture(capture):
    from src.trading_runtime.runtime import TradingRuntime
    if type(capture) is not ProgramRuntimeDecisionCapture or type(capture.token) is not _ProgramCaptureSeal:
        raise ValueError('Program decision requires a native preselection capture')
    seal = capture.token
    if (seal.token is not _CAPTURE or seal.digest != _capture_digest(capture)
            or any(getattr(seal, name) is not getattr(capture, name) for name in (
                'runtime', 'journal', 'actor', 'source', 'block', 'population', 'replay_actor'))):
        raise ValueError('Program decision capture changed or lost native custody')
    runtime, journal, actor = capture.runtime, capture.journal, capture.actor
    if (type(runtime) is not TradingRuntime or type(journal) is not ProgramExecutionJournal
            or runtime.config.mode.value == 'backtest'
            or runtime.strategy is not actor or runtime.journal is not journal
            or runtime.run_id != journal.run_id or runtime.last_event_time != capture.boundary.boundary_at
            or capture.source.typed_source_boundary(capture.boundary.boundary_at) != capture.source_boundary
            or capture.source._typed_scope is None
            or capture.population.source_revision != capture.source._typed_scope[1]
            or capture.block.boundary_for(actor.program_release, capture.population,
                listing_id=actor._listings[capture.assignment_id],
                ticker=actor._by_assignment[capture.assignment_id].ticker,
                ordinal=capture.ordinal) != capture.boundary
            or observation_digest(capture.observation) != capture.observation_hash
            or type(capture.portfolio_head) is not ProgramPortfolioTransitionReceipt):
        raise ValueError('Program decision capture lost native source/clock custody')
    TradingRuntime.require_program_dispatch_mode(runtime)
    journal.require_current()
    lease = journal.writer._live_v4_lease
    if (lease.registration.key != (actor.strategy_id, actor.revision)
            or lease.registration.expected_content_hash != actor.program_release.header['content_hash']):
        raise ValueError('Program decision differs from its installed writer release')


async def capture_program_runtime_decision(runtime, source, block, population, *,
        assignment_id, ordinal, portfolio_authority):
    """Call under the native assembly lock, after all prior facts are committed."""
    from src.trading_runtime.runtime import TradingRuntime
    if (type(runtime) is not TradingRuntime or type(runtime.journal) is not ProgramExecutionJournal
            or type(runtime.strategy) is not AssignedProgramStrategy
            or type(source) is not SignalStreamRuntime or source._typed_scope is None
            or type(block) is not PreparedProgramBlock or type(population) is not CertifiedPopulationWindow
            or type(portfolio_authority) is not ProgramPortfolioTransitionAuthority
            or portfolio_authority.writer is not runtime.journal.writer
            or runtime.config.mode.value == 'backtest'):
        raise ValueError('Program capture requires actual native execution and typed Portfolio authorities')
    TradingRuntime.require_program_dispatch_mode(runtime)
    assembly = runtime._program_execution_assembly
    if (assembly.signal_runtime is not source or assembly.portfolio_authority is not portfolio_authority
            or getattr(source, 'source_context', None) != assembly.source_context):
        raise ValueError('Program capture differs from its bound native source/Portfolio assembly')
    journal, actor = runtime.journal, runtime.strategy
    journal.require_current()
    assignment = actor._by_assignment.get(assignment_id)
    if (assignment is None or assignment.account_id not in runtime.config.account_ids
            or journal.pending_record_count or journal.latest_sequence(runtime.run_id) != journal.fenced_sequence):
        raise ValueError('Program capture requires an exact committed assignment basis')
    boundary = block.boundary_for(actor.program_release, population,
        listing_id=actor._listings[assignment_id], ticker=assignment.ticker, ordinal=ordinal)
    source_boundary = source.typed_source_boundary(boundary.boundary_at)
    if (runtime.last_event_time != boundary.boundary_at or source_boundary[0] < 1
            or source_boundary[2] != boundary.boundary_at or population.source_revision != source._typed_scope[1]):
        raise ValueError('Program capture differs from the committed market/broker boundary')
    policy_hash = program_policy_fingerprint(actor)
    basis = journal.fenced_sequence
    head = await portfolio_authority.verify_current_head(runtime.portfolio, assignment.account_id,
                                                         as_of=boundary.boundary_at)
    if (type(head) is not ProgramPortfolioTransitionReceipt or head.account_id != assignment.account_id
            or journal.pending_record_count or journal.fenced_sequence != basis
            or program_policy_fingerprint(actor) != policy_hash):
        raise ValueError('Program financial basis changed during native head qualification')
    replay = AssignedProgramStrategy(actor.program_release, deepcopy(actor.assignments()), dict(actor._listings),
                                     execution_risk=actor.execution_risk)
    replay.execution_state = deepcopy(actor.execution_state)
    replay.target_state = deepcopy(actor.target_state)
    replay.trail_state = deepcopy(actor.trail_state)
    replay._selection_clocks = dict(actor._selection_clocks)
    if program_policy_fingerprint(replay) != policy_hash:
        raise ValueError('Program capture failed to detach the actual filled policy state')
    observation = observe_assignment_orders(runtime.order_manager, account_id=assignment.account_id,
        assignment_id=assignment_id, ticker=assignment.ticker, at=boundary.boundary_at)
    capture = ProgramRuntimeDecisionCapture(runtime, journal, actor, source, block, population,
        assignment_id, ordinal, basis, source_boundary, boundary, observation,
        observation_digest(observation), head, policy_hash, replay, None)
    seal = _ProgramCaptureSeal(runtime, journal, actor, source, block, population, replay,
                              _capture_digest(capture), _CAPTURE)
    capture = replace(capture, token=seal)
    _require_capture(capture)
    return capture


def qualify_program_runtime_decision(capture, parent, children, records, *,
        execution_policy, protection_profile, outside_rth):
    """Reselect from captured actual state; never issue a historical receipt."""
    from .rule_decision_reselection import reselect_rule_decision
    _require_capture(capture)
    if (parent['run_id'] != capture.journal.run_id or parent['basis_sequence'] != capture.basis_sequence
            or parent['assignment_id'] != capture.assignment_id or parent['observed_ordinal'] != capture.ordinal
            or program_policy_fingerprint(capture.replay_actor) != capture.policy_hash):
        raise ValueError('Program decision differs from its unchanged native capture')
    selection = reselect_rule_decision(parent, children, records, actor=capture.replay_actor,
        basis_sequence=capture.basis_sequence, block=capture.block, population=capture.population,
        source=capture.source, observation=capture.observation, execution_policy=execution_policy,
        protection_profile=protection_profile, outside_rth=outside_rth)
    selected_hash = program_policy_fingerprint(capture.replay_actor)
    if program_policy_fingerprint(capture.actor) != selected_hash:
        raise ValueError('Program execution ledger/policy changed outside the captured native selection')
    digest = rule_decision_semantic_hash(parent, children)
    evidence = _ProgramRuntimeDecisionEvidence(capture, capture.journal.run_id, capture.basis_sequence,
        digest, capture.observation, capture.observation_hash, selected_hash, selection,
        capture.token.digest, _QUALIFIED)
    return ProgramRuntimeDecisionQualification(evidence.run_id, evidence.basis_sequence, digest, selection, evidence)


def require_program_runtime_qualification(unit, qualified):
    """Writer boundary validates only the new concrete current-runtime proof."""
    evidence = getattr(qualified, '_proof', None)
    if (type(qualified) is not ProgramRuntimeDecisionQualification
            or type(evidence) is not _ProgramRuntimeDecisionEvidence or evidence.token is not _QUALIFIED
            or qualified.run_id != unit.base.run_id or qualified.run_id != evidence.run_id
            or qualified.basis_sequence != unit.basis['basis_sequence']
            or qualified.basis_sequence != evidence.basis_sequence
            or qualified.semantic_hash != evidence.semantic_hash
            or qualified.semantic_hash != rule_decision_semantic_hash(unit.basis, unit.intents)
            or qualified.selection != evidence.selection
            or evidence.capture_digest != _capture_digest(evidence.capture)
            or observation_digest(evidence.observation) != evidence.observation_hash):
        raise ValueError('Program decision lacks its exact native runtime qualification')
    _require_capture(evidence.capture)
    if (program_policy_fingerprint(evidence.capture.replay_actor) != evidence.selected_policy_hash
            or program_policy_fingerprint(evidence.capture.actor) != evidence.selected_policy_hash):
        raise ValueError('Qualified program replay policy changed before publication')
