"""Current producer bar stages using canonical SQL and shared CH checkpoints.

No legacy controller, SQLite ledger, disk journal or source flatfile is invoked.
The technical/seed stages and complete final publication remain separate gates.
"""
from datetime import date
from types import MappingProxyType
from uuid import uuid4

from pipelines.market_sip.events import market_day_sql as sql
from pipelines.market_sip.events.market_day_checkpoint import (
    CompletedMarketDayLedger, MarketDayCheckpointWriter, UNIT,
)


async def build_current_market_day_bars(client_factory, checkpoint_writer, ledger, *,
        build_id, source_unit, rules):
    """Build one explicit source unit, never admit an unfinished build.

    The caller supplies its reviewed source plan and bounded SQL transport.
    Native canonical-source, numeric-output and unchanged-source checks precede
    completed receipt publication. Existing completed outputs are reverified.
    Uncheckpointed output is an explicit recovery blocker, never overwritten.
    """
    from src.backend.qmd_columnar_history_transport import _owned_worker
    # These are shared numeric/source verification functions only. The legacy
    # run/build_ticker/Ledger/file-report controller is deliberately not called.
    from scripts.build_market_day import (
        digest, completed, evidence, source_evidence, storage_preflight,
        validate_bars, validate_broker,
    )
    if (not callable(client_factory) or type(checkpoint_writer) is not MarketDayCheckpointWriter
            or type(ledger) is not CompletedMarketDayLedger or type(source_unit) is not dict
            or type(rules) not in (tuple, list) or not rules
            or checkpoint_writer.claim.build_id != build_id or ledger.build_id != build_id):
        raise ValueError('Current market-day bars lack native explicit producer authorities')
    day = date.fromisoformat(source_unit['source_date'])
    ticker = source_unit['ticker']
    if (day.isoformat(), ticker) not in ledger._scope_keys:
        raise ValueError('Current market-day bar unit is outside its planned scope')
    source_unit = dict(source_unit)
    rules = tuple(dict(rule) for rule in rules)
    def work():
        client = client_factory()
        try:
            checkpoint_writer._owned()
            storage_preflight(client, 'arte', require_tables=True)
            source = source_evidence(client, source_unit)
            source_hash = digest([source, rules, sql.VERSION])
            bars = completed(ledger, client, 'arte', build_id, day, ticker, 'bars', source_hash)
            broker = completed(ledger, client, 'arte', build_id, day, ticker, 'broker_100ms', source_hash)
            if bars is not None:
                if broker is None:
                    raise ValueError('Completed bars lack their completed broker parent receipt')
                return (), True
            # Producer receipts are the restart authority. Orphaned output
            # requires explicit source/output reconciliation before retry.
            for stage in ('bars', 'broker_100ms'):
                existing = client.query(f"SELECT count() AS n FROM {sql.table('arte', stage)} "
                    f"WHERE build_id={sql.literal(build_id)} AND session_date=toDate({sql.literal(day)}) "
                    f"AND ticker={sql.literal(ticker)}", 'uncheckpointed_' + stage)[0]
                if int(existing['n']) and (stage != 'broker_100ms' or broker is None):
                    raise RuntimeError('Uncheckpointed market-day output requires explicit recovery')
            attempt = broker['attempt_id'] if broker is not None else str(uuid4())
            queries = []
            if broker is None:
                queries.append((sql.broker_sql('arte', build_id, day, ticker, attempt, rules), 'broker_100ms'))
            queries.extend(((sql.base_sql('arte', build_id, day, ticker, attempt), '100ms'),
                (sql.rollup_sql('arte', build_id, day, ticker, attempt, 100, (1000, 5000, 10000, 30000)), 'fine_rollup'),
                (sql.rollup_sql('arte', build_id, day, ticker, attempt, 30000, (60000, 300000, 3600000)), 'higher_rollup')))
            for query, phase in queries:
                checkpoint_writer._owned()
                client.query(query, phase, False)
            validate_bars(client, 'arte', build_id, day, ticker, attempt)
            validate_broker(client, 'arte', build_id, day, ticker, attempt, source)
            if source_evidence(client, source_unit) != source:
                raise ValueError('Canonical source changed during current market-day bar build')
            receipts = []
            for stage in ('broker_100ms', 'bars'):
                result = evidence(client, 'arte', stage, build_id, day, ticker, attempt)
                if result['n'] != result['unique_keys']:
                    raise ValueError('Current market-day output has duplicate keys')
                receipts.append(dict(build_id=build_id, session_date=day.isoformat(), ticker=ticker,
                    stage=stage, attempt_id=attempt, source_hash=source_hash,
                    output_rows=int(result['n']), output_hash=str(result['hash'])))
            checkpoint_writer._owned()
            return tuple(receipts), False
        finally:
            client.close()
    receipts, skipped = await _owned_worker(work)
    outcomes = []
    for receipt in receipts:
        outcomes.append(await checkpoint_writer.commit(UNIT, receipt))
    return MappingProxyType(dict(build_id=build_id, session_date=day.isoformat(), ticker=ticker,
        status='skipped_verified' if skipped else 'completed_bar_stages',
        receipt_outcomes=tuple(outcomes), receipts=tuple(MappingProxyType(row) for row in receipts),
        consumer_ready=False))
