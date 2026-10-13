"""Finalize the current producer through the existing native certificate gate."""
from copy import deepcopy

from pipelines.market_sip.events.market_day_checkpoint import MarketDayCheckpointWriter, load_completed_market_day_ledger


async def publish_current_market_day_build(definition, checkpoint_writer, *,
        checkpoint_reader_factory, source_client_factory):
    """Require every completed CH receipt before opening final publication.

    This is an operational writer entry point: explicit user authorization is
    required before calling it. It creates no schemas, starts no app services,
    and never invokes the legacy producer's disk ledger or controller.
    """
    from src.backend.qmd_columnar_history_transport import _owned_worker
    from src.trading_runtime.arte_market_day_certification import prepare_market_day_certificate
    from src.trading_runtime.arte_market_day_keeper import require_attested_inventory
    from src.trading_runtime.arte_market_day_publisher import publish_market_day_certificate
    from scripts.build_market_day import digest
    if (type(definition) is not dict or type(checkpoint_writer) is not MarketDayCheckpointWriter
            or not callable(checkpoint_reader_factory) or not callable(source_client_factory)):
        raise ValueError('Current publication lacks native explicit producer authorities')
    definition = deepcopy(definition)
    build = checkpoint_writer.claim.build_id
    if digest(definition) != build.split('-', 1)[0]:
        raise ValueError('Current publication definition differs from its immutable build identity')
    scopes = tuple(sorted((unit['source_date'], unit['ticker']) for unit in definition['plan']['units']))
    if scopes != checkpoint_writer.scopes:
        raise ValueError('Current publication changed its completed checkpoint population')
    checkpoint_writer._owned()
    ledger = await load_completed_market_day_ledger(checkpoint_reader_factory, build, scopes)
    prepared = await _owned_worker(prepare_market_day_certificate, definition, build, ledger)
    # Missing stages/seeds have already failed before a source publisher client
    # can be opened. Keep the complete native source replay, named-row readback,
    # part-placement verification and final Keeper CAS in one owned worker.
    def publish():
        checkpoint_writer._owned()
        source = source_client_factory()
        try:
            proof = publish_market_day_certificate(checkpoint_writer.writer, source,
                checkpoint_writer.keeper, checkpoint_writer.claim, prepared,
                sessions=tuple(definition['plan']['requested']))
            require_attested_inventory(proof, prepared['market_day_build_fence_v1'][0])
            return proof
        finally:
            source.close()
    return await _owned_worker(publish)
