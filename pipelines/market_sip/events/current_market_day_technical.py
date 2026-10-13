"""Current technical stage using immutable parents and shared CH checkpoints."""
from datetime import date
from types import MappingProxyType
from uuid import uuid4

from pipelines.market_sip.events import market_day_sql as sql
from pipelines.market_sip.events.market_day_checkpoint import MarketDayCheckpointWriter, UNIT, SEED
from pipelines.market_sip.events.market_day_predecessor import MarketDayPredecessorLedger


async def build_current_market_day_technical(client_factory, checkpoint_writer, registry, *, day, ticker):
    """Reuse the numerical stage and lineage checks; never publish a final fence."""
    from src.backend.qmd_columnar_history_transport import _owned_worker
    from scripts.build_market_day import (
        digest, completed, prior_indicator_state, resume_technical_hash,
        storage_preflight, validate_technical,
    )
    if (not callable(client_factory) or type(checkpoint_writer) is not MarketDayCheckpointWriter
            or type(registry) is not MarketDayPredecessorLedger or type(day) is not date
            or checkpoint_writer.claim.build_id != registry.current.build_id):
        raise ValueError('Current technical stage lacks native explicit producer authorities')
    predecessor = registry.predecessor_for(day, ticker)
    target = registry.for_target(day, ticker)
    build = registry.current.build_id
    calculation_source, rules_hash, version = registry.profile
    if version != sql.VERSION:
        raise ValueError('Current technical SQL differs from its immutable producer version')
    splits = tuple(dict(row) for row in registry.splits)
    def work():
        client = client_factory()
        try:
            checkpoint_writer._owned()
            storage_preflight(client, 'arte', require_tables=True)
            bar = registry.unit(build, day, ticker, 'bars')
            if bar is None or not completed(registry, client, 'arte', build, day, ticker, 'bars', bar['source_hash']):
                raise ValueError('Current technical stage lacks reverified completed bars')
            prior, prior_hash, prior_build = prior_indicator_state(target, client, 'arte', build,
                day, ticker, predecessor, calculation_source, rules_hash, splits)
            dependencies = [dict(session_date=day.isoformat(), **bar)]
            dependency_hash = digest([dependencies, prior_hash, sql.VERSION])
            seed = registry.seed(build, day, ticker)
            technical = registry.unit(build, day, ticker, 'technical')
            dependency_hash = resume_technical_hash(target, build, day, ticker, predecessor,
                'arte', calculation_source, rules_hash, dependencies, prior, prior_build,
                dependency_hash, technical, seed)
            technical = completed(registry, client, 'arte', build, day, ticker, 'technical', dependency_hash)
            if seed is not None and technical is None:
                raise ValueError('Current seed provenance lacks completed technical output')
            attempt = technical['attempt_id'] if technical is not None else str(uuid4())
            seed_row = dict(build_id=build, session_date=day.isoformat(), ticker=ticker,
                attempt_id=attempt, mode=int(bool(prior)), predecessor_date=predecessor or '',
                prior_build_id=prior_build, prior_state_hash=prior_hash or '')
            expected_seed = {name: seed_row[name] for name in (
                'attempt_id', 'mode', 'predecessor_date', 'prior_build_id', 'prior_state_hash')}
            if seed is not None:
                if seed != expected_seed:
                    raise ValueError('Current technical seed changed its immutable parent lineage')
                checkpoint_writer._owned()
                return (), True
            receipts = []
            if technical is None:
                existing = client.query(f"SELECT count() AS n FROM {sql.table('arte', 'technical')} "
                    f"WHERE build_id={sql.literal(build)} AND session_date=toDate({sql.literal(day)}) "
                    f"AND ticker={sql.literal(ticker)}", 'uncheckpointed_technical')[0]
                if int(existing['n']):
                    raise RuntimeError('Uncheckpointed technical output requires explicit recovery')
                checkpoint_writer._owned()
                client.query(sql.technical_sql('arte', build, day, ticker, attempt, bar['attempt_id'], prior),
                    'technical', False)
                result = validate_technical(registry, client, 'arte', build, day, ticker, attempt)
                if result['n'] != result['unique_keys']:
                    raise ValueError('Current technical output has duplicate keys')
                receipts.append((UNIT, dict(build_id=build, session_date=day.isoformat(), ticker=ticker,
                    stage='technical', attempt_id=attempt, source_hash=dependency_hash,
                    output_rows=int(result['n']), output_hash=str(result['hash']))))
            # Recheck the complete parent chain after calculation, before any
            # seed/technical receipt becomes durable producer progress.
            if prior_indicator_state(target, client, 'arte', build, day, ticker, predecessor,
                    calculation_source, rules_hash, splits) != (prior, prior_hash, prior_build):
                raise ValueError('Current technical parent state changed during calculation')
            checkpoint_writer._owned()
            receipts.append((SEED, seed_row))
            return tuple(receipts), False
        finally:
            client.close()
    receipts, skipped = await _owned_worker(work)
    outcomes = []
    for name, receipt in receipts:
        outcomes.append(await checkpoint_writer.commit(name, receipt))
    return MappingProxyType(dict(build_id=build, session_date=day.isoformat(), ticker=ticker,
        status='skipped_verified' if skipped else 'completed_technical_seed_stages',
        receipt_outcomes=tuple(outcomes), consumer_ready=False))
