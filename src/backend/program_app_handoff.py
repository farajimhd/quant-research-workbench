"""Typed operator handoff into the normal app startup and UI launch path."""
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from datetime import datetime

from fastapi import FastAPI
from .replay_run_service import ReplayRunDefinition, ReplayRunService
from .backtest_program_input_plan import ProgramPublishedInputRequest
from .program_managed_journal import ManagedProgramJournalRequest
from .signal_stream_typed_publication import TypedSignalPublicationQueue
from .program_signal_source import ProgramSignalPublisherFactory
from src.trading_runtime.strategy_engine import StrategyAssignment

HANDOFF_KEY = 'approved_program_app_preparation'


class _ProgramProtectionLayouts:
    """Constant layout lookup; final stop binding occurs after selection."""
    def __init__(self, layouts):
        self.layouts = MappingProxyType(dict(layouts))

    def __call__(self, at):
        if (not isinstance(at, datetime) or at.tzinfo is None or at.utcoffset() is None
                or at.microsecond):
            raise ValueError('Programme protection requires a completed native clock')
        return self.layouts


def build_bound_program_app_handoff(*, definition, assignments, listing_by_assignment,
        liquidity_resolution_ms, published_inputs, journal, signal_publisher, signal_scope,
        checkpoint_connection_factory, checkpoint_concurrency, execution_binding,
        protection_layouts, assignment_order, catalogs, reader_factory,
        outside_rth, maximum_events, market_plan=None, price_plan=None, liquidity_certificates=None):
    """Compose existing native contracts; no reads, publication or run start.

    Admission order, slice fractions, budgets and source authorities are
    supplied explicitly by the approved app compute funnel, never invented.
    """
    from src.trading_runtime.configuration_execution_binding import (
        ConfigurationExecutionBinding, restore_execution_binding)
    from src.trading_runtime.program_stop_protection import ProgramStopProtection
    if (type(definition) is not ReplayRunDefinition
            or type(published_inputs) is not ProgramPublishedInputRequest
            or type(execution_binding) is not ConfigurationExecutionBinding
            or type(assignments) is not tuple or not assignments
            or any(type(row) is not StrategyAssignment for row in assignments)
            or not isinstance(protection_layouts, Mapping)
            or any(type(layout) is not ProgramStopProtection for layout in protection_layouts.values())
            or type(assignment_order) is not tuple or not callable(reader_factory)
            or type(outside_rth) is not bool or type(maximum_events) is not int or maximum_events < 1):
        raise ValueError('Bound handoff requires explicit native execution, layouts and compute bounds')
    binding = restore_execution_binding(execution_binding.rows)
    revision = definition.configuration_revision
    identities = {row.assignment_id for row in assignments}
    if (revision.get('execution_binding_hash') != binding.header['content_hash']
            or revision.get('selection_hash') != binding.header['configuration_hash']
            or revision.get('revision_id') != 'strategy-configuration:' + binding.header['configuration_id']
            or set(assignment_order) != identities or len(assignment_order) != len(identities)
            or set(protection_layouts) != identities):
        raise ValueError('Bound handoff differs from approved configuration or assignment coverage')
    layouts = _ProgramProtectionLayouts(protection_layouts)
    scope = dict(source_publisher=signal_publisher, catalogs=catalogs, reader_factory=reader_factory,
        concurrency=published_inputs.concurrency, assignment_order=assignment_order,
        execution_policy=binding, protection_profiles_at=layouts,
        outside_rth=outside_rth, maximum_events=maximum_events)
    if market_plan is not None:
        scope['market_plan'] = market_plan
    if price_plan is not None:
        scope['price_plan'] = price_plan
    if liquidity_certificates is not None:
        scope['liquidity_certificates'] = liquidity_certificates
    return ProgramAppHandoff(definition, assignments, listing_by_assignment,
        liquidity_resolution_ms, published_inputs,
        tuple(dict(scope) for _ in published_inputs.cohort_windows), journal,
        signal_publisher, signal_scope, checkpoint_connection_factory, checkpoint_concurrency)


@dataclass(frozen=True)
class ProgramAppHandoff:
    definition: ReplayRunDefinition
    assignments: tuple
    listing_by_assignment: Mapping
    liquidity_resolution_ms: int
    published_inputs: ProgramPublishedInputRequest
    scopes: tuple
    journal: ManagedProgramJournalRequest
    signal_publisher: TypedSignalPublicationQueue | ProgramSignalPublisherFactory
    signal_scope: Mapping
    checkpoint_connection_factory: object
    checkpoint_concurrency: int

    def __post_init__(self):
        if (type(self.definition) is not ReplayRunDefinition
                or type(self.published_inputs) is not ProgramPublishedInputRequest
                or type(self.journal) is not ManagedProgramJournalRequest
                or type(self.signal_publisher) not in (TypedSignalPublicationQueue, ProgramSignalPublisherFactory)
                or type(self.assignments) is not tuple or not self.assignments
                or any(type(row) is not StrategyAssignment for row in self.assignments)
                or not isinstance(self.listing_by_assignment, Mapping)
                or set(self.listing_by_assignment) != {row.assignment_id for row in self.assignments}
                or type(self.liquidity_resolution_ms) is not int or self.liquidity_resolution_ms not in (100, 1000)
                or type(self.scopes) is not tuple or len(self.scopes) != len(self.published_inputs.cohort_windows)
                or any(not isinstance(scope, Mapping) or scope.get('source_publisher') is not self.signal_publisher
                    for scope in self.scopes)
                or not isinstance(self.signal_scope, Mapping)
                or not callable(self.checkpoint_connection_factory)
                or type(self.checkpoint_concurrency) is not int or not 1 <= self.checkpoint_concurrency <= 8):
            raise ValueError('App handoff requires complete native published inputs and managed owners')
        object.__setattr__(self, 'listing_by_assignment', MappingProxyType(dict(self.listing_by_assignment)))
        object.__setattr__(self, 'scopes', tuple(MappingProxyType(dict(scope)) for scope in self.scopes))
        object.__setattr__(self, 'signal_scope', MappingProxyType(dict(self.signal_scope)))

    def arguments(self):
        return dict(assignments=self.assignments, listing_by_assignment=self.listing_by_assignment,
            liquidity_resolution_ms=self.liquidity_resolution_ms, cohorts=self.published_inputs,
            scopes=self.scopes, journal_scope=self.journal, signal_publisher=self.signal_publisher,
            signal_scope=self.signal_scope, checkpoint_connection_factory=self.checkpoint_connection_factory,
            checkpoint_concurrency=self.checkpoint_concurrency)


def configure_program_app_handoff(application, request):
    """Explicit approved operator composition; performs no I/O or installation.

    Published records remain database-authoritative. The request carries native
    consumer objects, not a disk configuration or an alternative data source.
    Its Signal Stream publisher remains caller-owned through app shutdown.
    """
    if type(application) is not FastAPI or type(request) is not ProgramAppHandoff:
        raise TypeError('App handoff requires the native application and typed request')
    prior = getattr(application.state, HANDOFF_KEY, None)
    if prior is not None and prior is not request:
        raise ValueError('App handoff cannot replace another approved preparation')
    setattr(application.state, HANDOFF_KEY, request)


async def prepare_configured_program_app_handoff(application, selector, run_service):
    """Stage for normal UI preflight/launch; never starts the backtest task.

    This operational preparation may publish the existing source/journal facts,
    so installing a request and starting the app requires prior user approval.
    Missing products fail; no source producer or implicit rebuild is invoked.
    """
    request = getattr(application.state, HANDOFF_KEY, None)
    if request is None:
        return None
    if type(request) is not ProgramAppHandoff or type(run_service) is not ReplayRunService:
        raise ValueError('Configured handoff requires the normal app Backtest service')
    from .program_configuration_selection_service import program_configuration_selection_service
    if program_configuration_selection_service() is not selector:
        raise ValueError('Configured handoff requires its installed central selection authority')
    prepared = await selector.prepare_app_controller(run_service, request.definition, **request.arguments())
    if (getattr(application.state, HANDOFF_KEY, None) is not request
            or program_configuration_selection_service() is not selector):
        await selector.close_prepared_handoff()
        raise RuntimeError('App handoff approval or installed owner changed during preparation')
    return prepared
