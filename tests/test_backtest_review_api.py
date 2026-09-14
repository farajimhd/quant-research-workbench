from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock, patch
from datetime import datetime, timezone

from src.backend.app import backtest_run_service, trading_backtest_run_review


class BacktestReviewAPITests(IsolatedAsyncioTestCase):
    def test_run_chart_cannot_expand_past_backtest_boundary(self):
        from src.backend.app import trading_canvas_live_chart_history
        cutoff = datetime(2026, 8, 21, 8, tzinfo=timezone.utc)
        controller = Mock(current_time=cutoff)
        with (patch.object(backtest_run_service, 'get', return_value=controller),
              patch('src.backend.app._canvas_live_chart_history', return_value={}) as history):
            trading_canvas_live_chart_history(symbol='AAPL', run_id='saved', mode='backtest',
                as_of='2026-08-21T20:00:00+00:00', full_session=True, stage='bars', row_limit=200)
            self.assertEqual(history.call_args.kwargs['as_of'], cutoff.isoformat())
            self.assertFalse(history.call_args.kwargs['full_session'])

    async def test_compact_review_avoids_full_snapshot(self):
        controller = Mock()
        controller.stream_snapshot.return_value = {'run_id': 'saved', 'status': 'stopped'}
        controller.snapshot.return_value = {'assignments': ['full evidence']}
        with patch.object(backtest_run_service, 'review_saved', new=AsyncMock(return_value=controller)):
            self.assertEqual(await trading_backtest_run_review('saved', compact=True), controller.stream_snapshot.return_value)
            controller.snapshot.assert_not_called()
            self.assertEqual(await trading_backtest_run_review('saved'), controller.snapshot.return_value)
