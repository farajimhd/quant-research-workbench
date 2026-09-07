from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from src.backend import experimental_structure_book as E
from src.backend import structure_chart_timeline as T
from src.backend.replay_activity_index import ReplayActivityIndex
from src.backend.trading_runtime_service import strategy_activity_payload
from src.trading_runtime.journal import TradingJournal


def test_timeline_prefix_parity_concurrency_rewind_and_mutation_isolation():
    start = datetime(2026, 8, 21, 4, tzinfo=E.NY)
    born = E.micros(start)
    levels = [dict(id=str(i), price=10+i/10, lower=9.99+i/10, upper=10.01+i/10,
        tick=.01, side=1, born_us=born+(i-1)*1000000, timeframe='1s',
        state=[0,0,0,[0.,0.,0.,0,0,0],0], confirmed_ordinal=i) for i in range(3)]
    observations = [dict(known_us=born+i*1000000, price=p, prior_range=.1)
        for i,p in enumerate([10,9,9,10,11,10,9,11])]
    basis = dict(lower=0, upper=100, p_min=0, p_max=3, normalization_status='ready')
    with patch.object(E, 'resolve', return_value={'id':'book','fingerprint':'hash'}), \
         patch.object(E, 'load_day', return_value=(levels,observations,2.)), \
         patch.object(E, 'session_normalization', return_value=basis):
        T._CACHE.clear()
        for seconds in (0,2,4,7,1,5,7):
            end=start+timedelta(seconds=seconds)
            assert E.chart_rows('book','T',start,end,'hash') == E.uncached_chart_rows('book','T',start,end,'hash')
        timeline=next(iter(T._CACHE.values()))
        count=timeline.transitions
        with ThreadPoolExecutor(max_workers=3) as pool:
            results=list(pool.map(lambda _: E.chart_rows('book','T',start,start+timedelta(seconds=7),'hash'),range(3)))
        assert results[0]==results[1]==results[2]
        assert timeline.transitions==count
        first=E.chart_rows('book','T',start,start+timedelta(seconds=2),'hash')
        delta=E.chart_rows('book','T',start,start+timedelta(seconds=7),'hash',after=start+timedelta(seconds=2))
        assert first+delta==results[1]
        results[0][0]['qmd_structure_unified_levels'].clear()
        assert E.chart_rows('book','T',start,start,'hash')[0]['qmd_structure_unified_levels']
        with patch.object(T,'MAX_BYTES',1):
            assert E.chart_rows('book','T',start,start+timedelta(seconds=7),'hash')==results[1]
            assert not T._CACHE


def test_incremental_activity_matches_sql_pagination_and_future_cutoffs():
    at=datetime(2026,8,21,8,tzinfo=timezone.utc)
    with TemporaryDirectory() as directory:
        journal=TradingJournal(Path(directory)/'journal.sqlite3')
        index=ReplayActivityIndex(journal,'test')
        try:
            for i in range(8):
                journal.append(run_id='test',category='strategy_decision',entity_type='signal',
                    entity_id=str(i//2),event_time=at+timedelta(seconds=i),
                    payload=dict(ticker='SUGP',strategy_id='strategy',action='enter_long' if i%3 else 'wait'))
                for cutoff in (at+timedelta(seconds=i),at+timedelta(seconds=2)):
                    for consequential in (False,True):
                        options=dict(as_of=cutoff,ticker='SUGP',limit=3,offset=1,consequential_only=consequential)
                        actual=index.payload(**options)
                        expected=strategy_activity_payload(journal=journal,run_id='test',include_decision_evidence=False,**options)
                        assert actual==expected
            with patch.object(journal,'strategy_activity_records',wraps=journal.strategy_activity_records) as query:
                index.payload(**options)
                index.payload(**options)
                assert query.call_count==0
        finally:
            journal.close()


class CanvasReadTests(unittest.IsolatedAsyncioTestCase):
    async def test_canvas_uses_projected_snapshot_without_reconciling(self):
        from src.backend.replay_run_service import ReplayRunController, ReplayRunDefinition
        from src.trading_runtime.runtime import TradingRuntime, RunMode
        from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter
        from src.trading_runtime.canonical_session import CanonicalBrokerSession
        from src.trading_runtime.domain import TradingMode, BrokerProvider
        from tests.test_replay_run_service import approved_configuration
        from datetime import date, time
        from unittest.mock import AsyncMock
        broker=SimulatedBrokerAdapter(['SIM'],mode=TradingMode.BACKTEST)
        session=CanonicalBrokerSession(broker,mode=TradingMode.BACKTEST,provider=BrokerProvider.SIMULATED)
        await session.bootstrap()
        runtime=object.__new__(TradingRuntime)
        runtime._canonical_session=session
        runtime.canonical_snapshot=AsyncMock(side_effect=AssertionError('Canvas must not reconcile'))
        with TemporaryDirectory() as directory:
            controller=ReplayRunController(ReplayRunDefinition(session_date=date(2026,8,21),
                start_time=time(4),end_time=time(4,30),mode=RunMode.BACKTEST,
                tickers=('SUGP',),configuration_revision=approved_configuration()),runtime_root=Path(directory))
            controller._runtime=runtime
            controller._journal=TradingJournal(Path(directory)/'journal.sqlite3')
            controller.status='running'
            controller.current_time=datetime(2026,8,21,8,tzinfo=timezone.utc)
            try:
                before=session.projector.snapshot()
                count=controller._journal.latest_sequence(controller.run_id)
                first=await controller.canvas_payload('SUGP')
                await controller.canvas_payload('SUGP')
                assert first['trading']['accounts']
                assert session.projector.snapshot()==before
                assert controller._journal.latest_sequence(controller.run_id)==count
                runtime.canonical_snapshot.assert_not_awaited()
            finally:
                controller._journal.close()
