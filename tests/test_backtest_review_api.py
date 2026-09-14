from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock, patch

from src.backend.app import backtest_run_service, trading_backtest_run_review


class BacktestReviewAPITests(IsolatedAsyncioTestCase):
    async def test_compact_review_avoids_full_snapshot(self):
        controller = Mock()
        controller.stream_snapshot.return_value = {'run_id': 'saved', 'status': 'stopped'}
        controller.snapshot.return_value = {'assignments': ['full evidence']}
        with patch.object(backtest_run_service, 'review_saved', new=AsyncMock(return_value=controller)):
            self.assertEqual(await trading_backtest_run_review('saved', compact=True), controller.stream_snapshot.return_value)
            controller.snapshot.assert_not_called()
            self.assertEqual(await trading_backtest_run_review('saved'), controller.snapshot.return_value)
