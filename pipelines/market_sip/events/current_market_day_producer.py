"""Bounded current producer scheduling; existing numeric and final gates own data."""
import asyncio
from copy import deepcopy
from types import MappingProxyType

from pipelines.market_sip.events.current_market_day_bars import build_current_market_day_bars
from pipelines.market_sip.events.current_market_day_technical import build_current_market_day_technical
from pipelines.market_sip.events.current_market_day_publication import publish_current_market_day_build
from pipelines.market_sip.events.market_day_checkpoint import MarketDayCheckpointWriter, load_completed_market_day_ledger
from pipelines.market_sip.events.market_day_predecessor import MarketDayPredecessorLedger


async def run_current_market_day_producer(definition, checkpoint_writer, *,
        sql_client_factory, checkpoint_reader_factory, source_client_factory,
        parents, workers, on_progress=None, empty_predecessors=None):
    """Operate only an explicitly approved build; this function grants no approval.

    Fixed workers finish active units on cancellation or peer failure. New
    units stop, verified checkpoints remain reusable, and no final fence is
    published for an interrupted build. Different dates run chronologically.
    """
    from scripts.build_market_day import digest
    from src.backend.backtest_program_journal_startup import _settle_owned_task
    from .market_day_predecessor_absence import reverify_empty_market_day_predecessors
    if (type(definition) is not dict or type(checkpoint_writer) is not MarketDayCheckpointWriter
            or any(not callable(factory) for factory in
                (sql_client_factory, checkpoint_reader_factory, source_client_factory))
            or type(parents) is not tuple or type(workers) is not int or not 1 <= workers <= 6
            or (on_progress is not None and not asyncio.iscoroutinefunction(on_progress))):
        raise ValueError('Current producer requires native explicit bounded authorities')
    definition = deepcopy(definition)
    build = checkpoint_writer.claim.build_id
    if digest(definition) != build.split('-', 1)[0]:
        raise ValueError('Current producer definition differs from its immutable build')
    units = tuple(dict(unit) for unit in definition['plan']['units'])
    scopes = tuple(sorted((unit['source_date'], unit['ticker']) for unit in units))
    if scopes != checkpoint_writer.scopes:
        raise ValueError('Current producer scope differs from its owned checkpoint plan')
    counts = dict(total=len(units) * 2, queued=len(units) * 2, active=0,
        completed=0, skipped=0, failed=0)
    stop = False
    async def report(phase):
        if on_progress is not None:
            await on_progress(MappingProxyType(dict(build_id=build, phase=phase,
                work_unit_kind='ticker_session_phase', source_units=len(units), **counts)))
    async def phase_pool(phase, selected, operation):
        nonlocal stop
        iterator = iter(selected)
        errors = []
        async def worker():
            nonlocal stop
            while not stop:
                unit = next(iterator, None)
                if unit is None:
                    return
                counts['queued'] -= 1
                counts['active'] += 1
                try:
                    await report(phase)
                    result = await operation(unit)
                    counts['skipped' if result['status'] == 'skipped_verified' else 'completed'] += 1
                except BaseException as error:
                    counts['failed'] += 1
                    errors.append(error)
                    stop = True
                finally:
                    counts['active'] -= 1
                try:
                    await report(phase)
                except BaseException as error:
                    errors.append(error)
                    stop = True
        tasks = [asyncio.create_task(worker()) for _ in range(min(workers, len(selected)))]
        joined = asyncio.gather(*tasks, return_exceptions=True)
        try:
            await asyncio.shield(joined)
        except asyncio.CancelledError:
            stop = True
            await _settle_owned_task(joined)
            raise
        errors.extend(result for result in joined.result() if isinstance(result, BaseException))
        if errors:
            raise BaseExceptionGroup('Current market-day producer phase failed', errors)
    await report('preflight')
    ledger = await load_completed_market_day_ledger(checkpoint_reader_factory, build, scopes)
    by_day = {}
    for unit in units:
        by_day.setdefault(unit['source_date'], []).append(unit)
    for day in sorted(by_day):
        selected = tuple(sorted(by_day[day], key=lambda unit: unit['ticker']))
        if empty_predecessors is not None:
            await reverify_empty_market_day_predecessors(empty_predecessors, checkpoint_reader_factory)
        registry = MarketDayPredecessorLedger(ledger, definition, parents, empty_predecessors=empty_predecessors)
        # Resolve declared lineage before spending time on new bar calculation.
        for unit in selected:
            previous = registry.predecessor_for(day, unit['ticker'])
            if previous is not None:
                registry.for_target(day, unit['ticker']).candidates(previous, unit['ticker'],
                    build, 'arte', *registry.profile)
        async def bars(unit):
            return await build_current_market_day_bars(sql_client_factory, checkpoint_writer, ledger,
                build_id=build, source_unit=unit, rules=definition['plan']['rules'])
        await phase_pool('bars:' + day, selected, bars)
        ledger = await load_completed_market_day_ledger(checkpoint_reader_factory, build, scopes)
        registry = MarketDayPredecessorLedger(ledger, definition, parents, empty_predecessors=empty_predecessors)
        async def technical(unit):
            from datetime import date
            return await build_current_market_day_technical(sql_client_factory, checkpoint_writer, registry,
                day=date.fromisoformat(day), ticker=unit['ticker'])
        await phase_pool('technical:' + day, selected, technical)
        ledger = await load_completed_market_day_ledger(checkpoint_reader_factory, build, scopes)
    if counts['active'] or counts['queued'] or counts['failed']:
        raise RuntimeError('Current producer cannot publish with unfinished or failed units')
    await report('publication')
    if empty_predecessors is not None:
        await reverify_empty_market_day_predecessors(empty_predecessors, checkpoint_reader_factory)
    proof = await publish_current_market_day_build(definition, checkpoint_writer,
        checkpoint_reader_factory=checkpoint_reader_factory, source_client_factory=source_client_factory)
    await report('complete')
    return proof
