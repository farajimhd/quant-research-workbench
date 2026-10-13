"""Managed operator composition for the existing central selection publisher."""
import asyncio
from pathlib import Path

from .program_app_credentials import program_app_reader_factory, program_app_writer_factory
from .certified_program_configuration import ConfigurationReferenceReader
from .strategy_configuration_transport import publish_configuration_selection
from .backtest_program_journal_startup import _settle_owned_task
from .qmd_columnar_history_transport import _owned_worker
from src.trading_runtime.strategy_configuration_selection import restore_configuration_selection
from src.trading_runtime.strategy_registry import ProgramStrategyExecutorRegistration, register_program_strategy_executor
from src.trading_runtime.columnar_strategy_reader import load_program_release
from src.trading_runtime.arte_typed_insert_dispatch import TypedInsertDispatch
from src.trading_runtime.keeper_session import open_workstation_keeper_session


async def publish_managed_program_configuration(source, registration, *, query_limits, concurrency):
    """Publish an approved scalar selection; never install it in the app or run.

    Operational authorization is required. Immutable programme references are
    cold verified before temporary operator-process code registration. The
    existing publisher independently verifies all policy references, storage,
    exact child readback and persistent Keeper ownership before sealing.
    """
    selected = restore_configuration_selection(source)
    if (type(registration) is not ProgramStrategyExecutorRegistration
            or type(concurrency) is not int or not 1 <= concurrency <= 6):
        raise ValueError('Managed selection publication requires bounded approved executable pins')
    registration.verify()
    if ((selected.header['strategy_id'], selected.header['strategy_revision']) != registration.key
            or selected.header['program_release_hash'] != registration.expected_content_hash):
        raise ValueError('Managed selection differs from its approved programme release')
    from .backtest_journal_clickhouse import backtest_code_hash
    if await _owned_worker(backtest_code_hash, Path(__file__).resolve().parents[2]) != registration.expected_code_hash:
        raise ValueError('Managed configuration executable differs from the native source closure')
    reader_factory = program_app_reader_factory('selection_reader', query_limits=query_limits)
    writer_factory = program_app_writer_factory('selection_publisher', query_limits=query_limits)
    release_factory = program_app_reader_factory('release_reader', query_limits=query_limits)
    policy_factory = program_app_reader_factory('market_input_reader', query_limits=query_limits)
    release = await load_program_release(release_factory, strategy_number=registration.revision,
        expected_content_hash=registration.expected_content_hash,
        expected_code_hash=registration.expected_code_hash, expected_winner_hash=registration.expected_winner_hash)
    if (release.header['strategy_id'], release.header['release_id']) != (
            selected.header['strategy_id'], selected.header['program_release_id']):
        raise ValueError('Managed selection references a different central release identity')
    register_program_strategy_executor(registration)
    def endpoint():
        reader = reader_factory()
        try:
            return reader.base_url
        finally:
            reader.close()
    central_endpoint = await _owned_worker(endpoint)
    opening = asyncio.create_task(asyncio.to_thread(open_workstation_keeper_session))
    session = None
    try:
        session = await asyncio.shield(opening)
        references = ConfigurationReferenceReader(central_endpoint, release_factory, policy_factory,
            TypedInsertDispatch(session.client), concurrency)
        # A missing referenced policy must fail before the publisher creates
        # its persistent single-use claim. Publication rechecks these references.
        await _owned_worker(references.verify, selected)
        return await publish_configuration_selection(reader_factory=reader_factory,
            writer_factory=writer_factory, session=session, source=selected.rows,
            reference_reader=references, concurrency=concurrency)
    finally:
        if session is None:
            session = await _settle_owned_task(opening)
        await _settle_owned_task(asyncio.create_task(asyncio.to_thread(session.close)))
