import asyncio
import json
from datetime import date, time, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from src.backend.replay_run_service import ReplayRunController, ReplayRunDefinition, ReplayRunService, RunMode
from src.trading_runtime.journal import TradingJournal
from tests.test_replay_run_service import approved_configuration, NEW_YORK
from tests.test_trading_runtime import quote


@pytest.mark.parametrize('status', ['stopped', 'completed', 'failed', 'paused'])
def test_review_financial_parity_and_fenced_pages_without_execution_restore(tmp_path, status):
    async def check():
        from datetime import datetime
        from src.trading_runtime.ibkr_schema import OrderRequest
        source = ReplayRunController(ReplayRunDefinition(session_date=date(2026, 7, 28), start_time=time(9, 45),
            initial_cash=123456, mode=RunMode.BACKTEST, tickers=('AAPL',), configuration_revision=approved_configuration()), runtime_root=tmp_path)
        source.run_dir.mkdir(parents=True)
        source._journal = TradingJournal(source.run_dir / 'journal.sqlite3')
        await source._initialize_runtime(record_configuration=False, record_lifecycle=False)
        at = datetime(2026, 7, 28, 10, tzinfo=NEW_YORK)
        broker = source._runtime.broker
        from dataclasses import replace
        await broker.on_market_event(replace(quote(bid=99, ask=100), ts=at))
        await broker.place_orders(source.account_ids[0], [OrderRequest(acctId=source.account_ids[0], conid=265598,
            cOID='review-buy', ticker='AAPL', orderType='MKT', side='BUY', quantity=10)])
        await broker.on_market_event(replace(quote(bid=101, ask=102), ts=at + timedelta(seconds=1)))
        await source._runtime._canonical_session.reconcile()
        source.current_time = at + timedelta(seconds=1)
        source.status = status
        source.error = 'QMD History timeout' if status == 'failed' else ''
        source._runtime_finished = True
        source.processed_events = 2
        source._source_cursor = {'market': {'ticker': 'AAPL', 'sequence': 2, 'ts': source.current_time.isoformat()}}
        for n in range(5):
            source._journal.append(run_id=source.run_id, category='strategy_decision', entity_type='signal', entity_id=str(n),
                event_time=at, payload={'ticker': 'AAPL', 'action': 'wait', 'reason': str(n)})
        source._session_relative_volume_store.identities['AAPL'] = 'sha256:pinned-rvol-baseline'
        if status == 'failed':
            source._incomplete_processing_unit = dict(kind='external_signal', ticker='AAPL', at=at.isoformat())
        source._save_restart_checkpoint(source.current_time)
        source._write_approved_configuration()
        source._write_manifest()
        expected = (await source.canvas_payload('AAPL'))['trading']
        if status == 'paused':
            checkpoint = source._journal.load_checkpoint(source.run_id)
            # Match service shutdown after pause, including a non-interval cursor.
            source._runtime.processed_events = 2
            source._runtime.last_event_time = source.current_time
            source._runtime._latest_checkpoint_cursor = 'last-market-event'
            await source._runtime.finish(status='paused')
            assert source._journal.load_checkpoint(source.run_id) == checkpoint
        source._journal.close()
        original = (source.run_dir / 'journal.sqlite3').read_bytes()
        with (patch.object(TradingJournal, 'load_checkpoint', side_effect=AssertionError('Execution checkpoint loaded')),
              patch.object(ReplayRunController, '_initialize_runtime', side_effect=AssertionError('Execution runtime restored'))):
            service = ReplayRunService(runtime_root=tmp_path)
            review = await service.review_saved(source.run_id)
            assert await service.review_saved(source.run_id) is review
            assert review.status == status
            assert review.review_only and review.snapshot()['review_only']
            if status == 'failed':
                assert not review.snapshot()['checkpoint']['resume_supported']
                assert 'processing event' in review.snapshot()['review_warning']
            assert str(review.definition.session_date) == '2026-07-28'
            assert review.session_relative_volume_artifacts == {'AAPL': 'sha256:pinned-rvol-baseline'}
            assert not hasattr(review, '_session_relative_volume_store')
            assert review.snapshot()["error"] == source.error
            try:
                actual = (await review.canvas_payload('AAPL', include_chart=False))['trading']
                def normalized(value):
                    # Snapshot identity and receipt time describe each read; all
                    # causal event times, fill IDs and financial values remain exact.
                    if isinstance(value, dict): return {k: normalized(v) for k, v in value.items() if k not in {'snapshot_id', 'received_at'}}
                    if isinstance(value, list): return [normalized(v) for v in value]
                    return value
                for key in ('positions', 'orders', 'executions', 'closed_trades', 'performance_snapshot', 'portfolio'):
                    def differences(a, b, path=key):
                        if isinstance(a, dict) and isinstance(b, dict):
                            return [d for k in a.keys() | b.keys() for d in differences(a.get(k), b.get(k), f'{path}.{k}')]
                        if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
                            return [d for i, (left, right) in enumerate(zip(a, b)) for d in differences(left, right, f'{path}[{i}]')]
                        return [] if a == b else [(path, a, b)]
                    assert not differences(normalized(actual[key]), normalized(expected[key])), differences(normalized(actual[key]), normalized(expected[key]))
                first = review.strategy_activity_snapshot(limit=2, include_decision_evidence=False)
                assert not first['complete'] and len(first['rows']) == 2
                second = review.strategy_activity_snapshot(limit=2, offset=first['next_offset'], include_decision_evidence=False,
                    through_sequence=first['presentation_sequence'], as_of=at + timedelta(days=1))
                assert not {r['record_id'] for r in first['rows']} & {r['record_id'] for r in second['rows']}
                assert second['as_of'] == review.current_time.isoformat()
                fenced = review.strategy_activity_snapshot(through_sequence=0)
                assert fenced['presentation_sequence'] == 0 and not fenced['rows']
                with pytest.raises(ValueError, match='read-only'):
                    await review.command('play')
            finally:
                review._journal.close()
        assert (source.run_dir / 'journal.sqlite3').read_bytes() == original
        if status == 'failed':
            with pytest.raises(ValueError, match='no complete restart-safe checkpoint'):
                await service.resume(source.run_id)
            import sqlite3
            with sqlite3.connect(source.run_dir / 'journal.sqlite3') as connection:
                connection.execute('DELETE FROM checkpoints')
            from src.backend.backtest_review import SavedBacktestReview
            empty = SavedBacktestReview(source.run_dir)
            try:
                payload = await empty.canvas_payload('AAPL', include_chart=False)
                assert not payload['trading']['complete']
                assert 'financial results are unavailable' in payload['errors']['review']
                assert not empty.snapshot()['checkpoint']['resume_supported']
            finally:
                empty._journal.close()
        if status == 'paused':
            # A resident execution still uses play, while a saved read-only view
            # can be replaced at capacity by an explicitly requested resume.
            service = ReplayRunService(runtime_root=tmp_path, max_resident_runs=1)
            source._journal = None
            service._runs[source.run_id] = source
            with pytest.raises(ValueError, match='already resident and active'):
                await service.resume(source.run_id)
            service._runs.clear()
            saved = await service.review_saved(source.run_id)
            with patch.object(ReplayRunController, 'start', new_callable=AsyncMock) as start:
                resumed = await service.resume(source.run_id)
                start.assert_awaited_once()
            assert service.get(source.run_id) is resumed
            assert saved._journal is None
    asyncio.run(check())
