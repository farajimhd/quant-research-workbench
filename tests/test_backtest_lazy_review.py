import asyncio
import json
from datetime import date, time, timedelta
from unittest.mock import patch

import pytest

from src.backend.replay_run_service import ReplayRunController, ReplayRunDefinition, ReplayRunService, RunMode
from src.trading_runtime.journal import TradingJournal
from tests.test_replay_run_service import approved_configuration, NEW_YORK
from tests.test_trading_runtime import quote


def test_review_financial_parity_and_fenced_pages_without_execution_restore(tmp_path):
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
        source.status = 'stopped'
        source._runtime_finished = True
        source.processed_events = 2
        source._source_cursor = {'market': {'ticker': 'AAPL', 'sequence': 2, 'ts': source.current_time.isoformat()}}
        for n in range(5):
            source._journal.append(run_id=source.run_id, category='strategy_decision', entity_type='signal', entity_id=str(n),
                event_time=at, payload={'ticker': 'AAPL', 'action': 'wait', 'reason': str(n)})
        source._save_restart_checkpoint(source.current_time)
        source._write_approved_configuration()
        source._write_manifest()
        expected = (await source.canvas_payload('AAPL'))['trading']
        source._journal.close()
        original = (source.run_dir / 'journal.sqlite3').read_bytes()
        with (patch.object(TradingJournal, 'load_checkpoint', side_effect=AssertionError('Execution checkpoint loaded')),
              patch.object(ReplayRunController, '_initialize_runtime', side_effect=AssertionError('Execution runtime restored'))):
            review = await ReplayRunService(runtime_root=tmp_path).review_saved(source.run_id)
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
    asyncio.run(check())
