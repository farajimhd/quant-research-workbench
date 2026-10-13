"""Bounded scheduling, day ordering and graceful checkpoint retention."""
import asyncio
from copy import deepcopy

import pytest

from pipelines.market_sip.events import current_market_day_producer as producer
from pipelines.market_sip.events.market_day_checkpoint import CompletedMarketDayLedger, MarketDayCheckpointWriter, UNIT, SEED
from src.trading_runtime.arte_market_day_publisher import MarketDayCertificateClient
from src.trading_runtime.arte_market_day_keeper import MarketDayKeeperAuthority
from scripts.build_market_day import digest
from test_arte_market_day_keeper import FakeKeeper
from test_arte_market_day_publisher import FakeClickHouse
from test_arte_market_day_certification import DAY, PIN
from test_arte_market_day_source_plan import plan as source_plan_fixture


@pytest.mark.parametrize('mode', ['complete', 'failure', 'cancel'])
def test_fixed_workers_order_days_and_never_publish_interrupted_build(monkeypatch, mode):
    plan = source_plan_fixture()
    base = plan['units'][0]
    days = (DAY, '2026-08-19')
    plan['units'] = [{**base, 'source_date': day, 'ticker': f'T{index}'} for day in days for index in range(3)]
    plan['predecessors'] = {DAY: None, days[1]: DAY}  # Explicit synthetic first-source declaration.
    definition = dict(version='market-day-core-v5', calculation_source=PIN, rules_hash=PIN, plan=plan)
    build = digest(definition)
    scopes = tuple((row['source_date'], row['ticker']) for row in plan['units'])
    keeper = MarketDayKeeperAuthority(FakeKeeper())
    claim = keeper.acquire(build, 'producer')
    store = FakeClickHouse()
    writer = MarketDayCheckpointWriter(store, MarketDayCertificateClient(store), keeper, claim, scopes)
    async def load(*args):
        return CompletedMarketDayLedger(build, scopes, tuple(store.rows[UNIT]), tuple(store.rows[SEED]))
    monkeypatch.setattr(producer, 'load_completed_market_day_ledger', load)
    active = peak = 0
    calls, reports, publications = [], [], []
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        async def bars(factory, checkpoint, ledger, **kwargs):
            nonlocal active, peak
            row = kwargs['source_unit']
            day, ticker = row['source_date'], row['ticker']
            active += 1
            peak = max(peak, active)
            calls.append(('bars', day, ticker))
            if active == 2: entered.set()
            try:
                if mode == 'cancel': await release.wait()
                else: await asyncio.sleep(.001)
                if mode == 'failure' and ticker == 'T0': raise ValueError('Stage failure')
                for stage in ('bars', 'broker_100ms'):
                    store.rows[UNIT].append(dict(build_id=build, session_date=day, ticker=ticker,
                        stage=stage, attempt_id='attempt', source_hash='source', output_rows=0, output_hash='0'))
                return dict(status='completed_bar_stages')
            finally:
                active -= 1
        async def technical(factory, checkpoint, registry, **kwargs):
            day, ticker = kwargs['day'].isoformat(), kwargs['ticker']
            assert sum(row['stage'] == 'bars' and row['session_date'] == day for row in store.rows[UNIT]) == 3
            calls.append(('technical', day, ticker))
            await asyncio.sleep(.001)
            store.rows[UNIT].append(dict(build_id=build, session_date=day, ticker=ticker,
                stage='technical', attempt_id='attempt', source_hash='source', output_rows=0, output_hash='0'))
            store.rows[SEED].append(dict(build_id=build, session_date=day, ticker=ticker, attempt_id='attempt',
                mode=0, predecessor_date=plan['predecessors'][day] or '', prior_build_id='', prior_state_hash=''))
            return dict(status='completed_technical_seed_stages')
        async def publication(*args, **kwargs):
            assert len(store.rows[UNIT]) == 18 and len(store.rows[SEED]) == 6
            publications.append('final')
            return 'synthetic-proof'
        async def report(value): reports.append(dict(value))
        monkeypatch.setattr(producer, 'build_current_market_day_bars', bars)
        monkeypatch.setattr(producer, 'build_current_market_day_technical', technical)
        monkeypatch.setattr(producer, 'publish_current_market_day_build', publication)
        task = asyncio.create_task(producer.run_current_market_day_producer(definition, writer,
            sql_client_factory=lambda: None, checkpoint_reader_factory=lambda: None,
            source_client_factory=lambda: None, parents=(), workers=2, on_progress=report))
        if mode == 'cancel':
            await entered.wait()
            task.cancel()
            await asyncio.sleep(0)
            release.set()
            with pytest.raises(asyncio.CancelledError): await task
        elif mode == 'failure':
            with pytest.raises(BaseExceptionGroup): await task
        else:
            assert await task == 'synthetic-proof'
    asyncio.run(run())
    assert peak == 2 and active == 0
    assert all(sum(row[key] for key in ('queued', 'active', 'completed', 'skipped', 'failed')) == row['total'] for row in reports)
    if mode == 'complete':
        assert publications == ['final']
        assert reports[-1]['completed'] == 12 and reports[-1]['phase'] == 'complete'
        first_later = next(index for index, call in enumerate(calls) if call[1] == days[1])
        assert sum(call[0] == 'technical' for call in calls[:first_later]) == 3
    else:
        assert not publications and all(call[0] == 'bars' for call in calls)
        assert len(store.rows[UNIT]) == (4 if mode == 'cancel' else 2)
