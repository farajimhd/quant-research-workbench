"""Deferred source assembly uses actual native context and concrete components."""
import asyncio
from dataclasses import replace

import pytest

from src.backend.program_signal_source import ProgramSignalPublisherFactory, require_program_signal_publisher
from src.backend.signal_execution_context import SignalExecutionContext
from src.backend.signal_context_transport import ContextSignalSourceInsertDispatch, ContextSignalSessionHeadKeeper
from src.backend.signal_source_clickhouse_storage import ClickHouseSignalSourceReadStorage
from src.backend.signal_source_registered_storage import RegisteredSignalSourceStorage
from src.backend.signal_stream_typed_attestation import SignalCursorAttestor
from src.backend.signal_stream_typed_publication import TypedSignalPublicationQueue


def contextual_publisher(context):
    # Constructors perform no I/O. Native read/insert/Keeper transports remain
    # separate; these source-guard tests never bootstrap or publish market data.
    keeper = ContextSignalSessionHeadKeeper(object(), endpoint='127.0.0.1:9181', source_context=context)
    dispatch = ContextSignalSourceInsertDispatch(object(), source_context=context)
    reader = ClickHouseSignalSourceReadStorage(object(), {}, source_context=context)
    storage = RegisteredSignalSourceStorage(reader, object(), dispatch, session_key=context.session_key,
        configuration_revision=context.configuration_revision, source_context=context)
    attestor = SignalCursorAttestor(keeper, owner_id='test-source-owner', epoch=1)
    return TypedSignalPublicationQueue(storage, capacity=1, attestor=attestor)


def source_context_for(controller, source_revision='source-origin'):
    return SignalExecutionContext(controller.run_id, controller.definition.mode.value,
        controller.definition.session_date.isoformat(), controller.definition.configuration_revision['revision_id'],
        source_revision)


def executable_contextual_publisher(context, catalogs):
    from tests.test_signal_context_transport import ContextMemoryClient
    from tests.test_live_signal_completion_keeper import FakeKazoo
    from tests.test_arte_typed_insert_dispatch import Keeper
    client = ContextMemoryClient([context])
    keeper = ContextSignalSessionHeadKeeper(FakeKazoo(), endpoint='127.0.0.1:9181', source_context=context)
    epoch = keeper.acquire(context.session_key, owner_id='native-test-owner')
    dispatch = ContextSignalSourceInsertDispatch(Keeper(), source_context=context)
    reader = ClickHouseSignalSourceReadStorage(client, catalogs, source_context=context)
    storage = RegisteredSignalSourceStorage(reader, client, dispatch, session_key=context.session_key,
        configuration_revision=context.configuration_revision, source_context=context)
    storage.initialize_new_session()
    return TypedSignalPublicationQueue(storage, capacity=4,
        attestor=SignalCursorAttestor(keeper, owner_id='native-test-owner', epoch=epoch))


def test_factory_deferred_until_actual_context_and_exact_native_components():
    context = SignalExecutionContext('actual-run-1', 'backtest', '2026-10-15', 'config', 'source')
    calls = []
    async def build(actual):
        calls.append(actual)
        return contextual_publisher(actual)
    factory = ProgramSignalPublisherFactory(build)
    assert calls == []
    publisher = asyncio.run(factory.create(context))
    try:
        assert calls == [context]
        assert require_program_signal_publisher(context, publisher) is publisher
        with pytest.raises(ValueError, match='context'):
            require_program_signal_publisher(replace(context, run_id='actual-run-2'), publisher)
        with pytest.raises(TypeError, match='actual native run context'):
            asyncio.run(factory.create(object()))
    finally:
        publisher.close()


def test_factory_rejects_callback_labels_and_closes_foreign_context_queue():
    with pytest.raises(TypeError, match='asynchronous builder'):
        ProgramSignalPublisherFactory(lambda _: object())
    context = SignalExecutionContext('actual-run-1', 'backtest', '2026-10-15', 'config', 'source')
    publisher = contextual_publisher(replace(context, run_id='other-run'))
    async def build(_): return publisher
    with pytest.raises(ValueError, match='context'):
        asyncio.run(ProgramSignalPublisherFactory(build).create(context))
    assert publisher._closing
    with pytest.raises(TypeError, match='concrete contextual'):
        require_program_signal_publisher(context, object())
