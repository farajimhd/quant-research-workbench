"""Own the complete current producer invocation and its control-plane lifetime.

This is the operational composition entry point, not an approval mechanism.
It uses explicit role factories and the existing native source/publication gates.
"""
import asyncio
from copy import deepcopy

from .current_market_day_producer import run_current_market_day_producer
from .current_market_day_transport import MarketDayQueryLimits, current_market_day_sql_factory
from .market_day_checkpoint import MarketDayCheckpointWriter
from src.trading_runtime.arte_market_day_keeper import MarketDayKeeperAuthority
from src.trading_runtime.arte_market_day_publisher import MarketDayCertificateClient, _placement


async def execute_current_market_day_build(definition, *, owner_id,
        keeper_session_factory, checkpoint_reader_factory, writer_http_factory,
        sql_http_factory, source_client_factory, limits, parents, workers,
        on_progress=None, empty_predecessors=None):
    """Run an approved immutable build, drain active work, then close all owners.

    Factories perform no I/O until called off-loop. Each HTTP factory must use
    its approved dedicated principal; this function never substitutes an admin
    account. Reader, writer and Keeper session are invocation-owned. Source and
    calculation connections are unit-owned by the existing producer stages.
    """
    from scripts.build_market_day import digest
    from src.backend.backtest_program_journal_startup import _settle_owned_task
    from src.trading_runtime.arte_market_day_source_plan import verify_canonical_source_plan_at_publication
    if (type(definition) is not dict or type(owner_id) is not str or not owner_id
            or type(limits) is not MarketDayQueryLimits or type(parents) is not tuple
            or type(workers) is not int or not 1 <= workers <= 6
            or any(not callable(factory) for factory in (keeper_session_factory,
                checkpoint_reader_factory, writer_http_factory, sql_http_factory, source_client_factory))
            or (on_progress is not None and not asyncio.iscoroutinefunction(on_progress))):
        raise ValueError('Current build requires explicit approved bounded authorities')
    definition = deepcopy(definition)
    build_id = digest(definition)
    scopes = tuple(sorted((unit['source_date'], unit['ticker']) for unit in definition['plan']['units']))
    if not scopes or len(set(scopes)) != len(scopes):
        raise ValueError('Current build requires unique complete source scopes')
    owned = []
    def close():
        errors = []
        while owned:
            try:
                owned.pop().close()
            except BaseException as error:
                errors.append(error)
        if errors:
            raise BaseExceptionGroup('Current producer owner cleanup failed', errors)
    def open_owned():
        try:
            reader = checkpoint_reader_factory()
            owned.append(reader)
            if reader.execute("SELECT getSetting('readonly')").strip() != '1':
                raise ValueError('Current producer checkpoint reader is not SELECT-only')
            _placement(reader)
            source = source_client_factory()
            try:
                # Recreate canonical inventory before acquiring a writer lane.
                verify_canonical_source_plan_at_publication(source, definition['plan'])
            finally:
                source.close()
            session = keeper_session_factory()
            owned.append(session)
            keeper = MarketDayKeeperAuthority(session.client)
            claim = keeper.acquire(build_id, owner_id)
            if claim is None:
                raise RuntimeError('Current market-day build already has a live owner')
            http = writer_http_factory(limits.settings())
            owned.append(http)
            return MarketDayCheckpointWriter(reader, MarketDayCertificateClient(http),
                keeper, claim, scopes)
        except BaseException as error:
            try:
                close()
            except BaseException as cleanup:
                raise BaseExceptionGroup('Producer opening and cleanup failed', [error, cleanup])
            raise
    opening = asyncio.create_task(asyncio.to_thread(open_owned))
    failure = None
    try:
        writer = await asyncio.shield(opening)
        return await run_current_market_day_producer(definition, writer,
            sql_client_factory=current_market_day_sql_factory(sql_http_factory, limits),
            checkpoint_reader_factory=checkpoint_reader_factory,
            source_client_factory=source_client_factory, parents=parents, workers=workers,
            on_progress=on_progress, empty_predecessors=empty_predecessors)
    except BaseException as error:
        failure = error
        if isinstance(error, asyncio.CancelledError):
            try:
                await _settle_owned_task(opening)
            except BaseException as opening_error:
                if opening_error is not error:
                    raise BaseExceptionGroup('Producer cancellation and opening failed', [error, opening_error])
        raise
    finally:
        try:
            await _settle_owned_task(asyncio.create_task(asyncio.to_thread(close)))
        except BaseException as cleanup:
            if failure is not None:
                raise BaseExceptionGroup('Producer execution and cleanup failed', [failure, cleanup])
            raise
