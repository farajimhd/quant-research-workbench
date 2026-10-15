"""Deferred native-run binding for the current programme Signal Stream path."""
from dataclasses import dataclass
from inspect import iscoroutinefunction

from .signal_execution_context import SignalExecutionContext
from .signal_stream_typed_publication import TypedSignalPublicationQueue


def require_program_signal_publisher(context, publisher):
    from .signal_source_registered_storage import RegisteredSignalSourceStorage
    from .signal_stream_typed_attestation import SignalCursorAttestor
    from .signal_source_clickhouse_storage import ClickHouseSignalSourceReadStorage
    from .signal_context_transport import (
        ContextSignalSourceInsertDispatch, ContextSignalSessionHeadKeeper,
        require_context_components)
    if (type(context) is not SignalExecutionContext
            or type(publisher) is not TypedSignalPublicationQueue
            or type(publisher._storage) is not RegisteredSignalSourceStorage
            or type(publisher._storage._read_storage) is not ClickHouseSignalSourceReadStorage
            or type(publisher._attestor) is not SignalCursorAttestor
            or type(publisher._storage._dispatch) is not ContextSignalSourceInsertDispatch
            or type(publisher._attestor._keeper) is not ContextSignalSessionHeadKeeper):
        raise TypeError('Program source requires the concrete contextual native publication path')
    require_context_components(context, publisher, publisher._storage,
        publisher._storage._read_storage, publisher._storage._dispatch,
        publisher._attestor._keeper)
    return publisher


@dataclass(frozen=True)
class ProgramSignalPublisherFactory:
    """Caller-owned constructor, invoked only after the actual run exists.

    Construction opens no connection. The callback owns partial construction
    cleanup; its caller retains the returned publisher through run shutdown.
    """
    build: object

    def __post_init__(self):
        if not iscoroutinefunction(self.build):
            raise TypeError('Program source factory requires an explicit asynchronous builder')

    async def create(self, context):
        if type(context) is not SignalExecutionContext:
            raise TypeError('Program source factory requires its actual native run context')
        publisher = await self.build(context)
        try:
            return require_program_signal_publisher(context, publisher)
        except BaseException as exc:
            if type(publisher) is TypedSignalPublicationQueue:
                from .qmd_columnar_history_transport import _owned_worker
                try:
                    await _owned_worker(publisher.close)
                except BaseException as cleanup:
                    raise BaseExceptionGroup('Invalid programme source and cleanup failed', [exc, cleanup])
            raise
