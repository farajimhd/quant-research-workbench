"""Typed operator handoff into the normal app startup and UI launch path."""
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from fastapi import FastAPI
from .replay_run_service import ReplayRunDefinition, ReplayRunService
from .backtest_program_input_plan import ProgramPublishedInputRequest
from .program_managed_journal import ManagedProgramJournalRequest
from .signal_stream_typed_publication import TypedSignalPublicationQueue
from src.trading_runtime.strategy_engine import StrategyAssignment

HANDOFF_KEY = 'approved_program_app_preparation'


@dataclass(frozen=True)
class ProgramAppHandoff:
    definition: ReplayRunDefinition
    assignments: tuple
    listing_by_assignment: Mapping
    liquidity_resolution_ms: int
    published_inputs: ProgramPublishedInputRequest
    scopes: tuple
    journal: ManagedProgramJournalRequest
    signal_publisher: TypedSignalPublicationQueue
    signal_scope: Mapping
    checkpoint_connection_factory: object
    checkpoint_concurrency: int

    def __post_init__(self):
        if (type(self.definition) is not ReplayRunDefinition
                or type(self.published_inputs) is not ProgramPublishedInputRequest
                or type(self.journal) is not ManagedProgramJournalRequest
                or type(self.signal_publisher) is not TypedSignalPublicationQueue
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
