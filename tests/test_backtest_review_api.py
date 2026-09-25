from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock, patch
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

from src.backend.app import (
    app, backtest_run_service, trading_backtest_run_review,
    trading_backtest_typed_financial_page, trading_backtest_typed_running_page,
)


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

    async def test_typed_financial_page_uses_only_readonly_typed_reader(self):
        run_id = str(uuid4())
        client = Mock()
        client.close = Mock()
        page = {"cursor": None, "fills": (), "commissions": (), "accounts": {}}
        with (patch('src.trading_runtime.arte_journal_reader.readonly_typed_journal_client', return_value=client),
              patch('src.backend.typed_backtest_financial_review.load_typed_backtest_financial_page',
                    return_value=page) as read,
              patch.object(backtest_run_service, 'review_saved', new=AsyncMock()) as saved):
            result = await trading_backtest_typed_financial_page(
                run_id, after_fill_sequence=7, after_commission_sequence=12, limit=20)
        self.assertEqual(result, page)
        read.assert_called_once_with(client, run_id, after_fill_sequence=7,
                                     after_commission_sequence=12, limit=20)
        client.close.assert_called_once()
        saved.assert_not_called()

    async def test_typed_financial_page_rejects_invalid_run_id_before_client(self):
        with patch('src.trading_runtime.arte_journal_reader.readonly_typed_journal_client') as factory:
            with self.assertRaises(HTTPException) as caught:
                await trading_backtest_typed_financial_page(
                    'invalid', after_fill_sequence=0, after_commission_sequence=0, limit=20)
        self.assertEqual(caught.exception.status_code, 400)
        factory.assert_not_called()

    async def test_typed_running_page_is_readonly_and_never_opens_saved_review(self):
        run_id = str(uuid4())
        client = Mock()
        page = {"running_prefix_only": True, "terminal_status_unknown": True,
                "events": [], "next_sequence": 4}
        with (patch('src.trading_runtime.arte_journal_reader.readonly_typed_journal_client',
                    return_value=client),
              patch('src.backend.typed_backtest_review_core.load_typed_backtest_running_page',
                    return_value=page) as read,
              patch.object(backtest_run_service, 'review_saved', new=AsyncMock()) as saved):
            result = await trading_backtest_typed_running_page(
                run_id, after_sequence=3, limit=20)
        self.assertEqual(result, page)
        read.assert_called_once_with(client, run_id, after_sequence=3, limit=20)
        client.close.assert_called_once()
        saved.assert_not_called()

    async def test_typed_running_http_route_serializes_readonly_page(self):
        run_id = str(uuid4())
        client = Mock()
        page = {"running_prefix_only": True, "terminal_status_unknown": True,
                "events": [], "next_sequence": 0}
        with (patch('src.trading_runtime.arte_journal_reader.readonly_typed_journal_client',
                    return_value=client),
              patch('src.backend.typed_backtest_review_core.load_typed_backtest_running_page',
                    return_value=page)):
            async with AsyncClient(transport=ASGITransport(app=app),
                                   base_url='http://test') as http:
                response = await http.get(
                    f'/api/trading/backtest/runs/{run_id}/typed-running-page')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), page)
        client.close.assert_called_once()

    def test_typed_financial_route_is_separate_from_saved_review(self):
        paths = {route.path: route.methods for route in app.routes if hasattr(route, 'methods')}
        self.assertIn('GET', paths['/api/trading/backtest/runs/{run_id}/typed-financial-page'])

    async def test_typed_financial_http_route_serializes_page(self):
        run_id = str(uuid4())
        client = Mock()
        client.close = Mock()
        page = {"run": {"run_id": run_id}, "status": "completed", "fills": [],
                "commissions": [], "accounts": {}, "next_fill_sequence": 0,
                "next_commission_sequence": 0, "complete": True}
        with (patch('src.trading_runtime.arte_journal_reader.readonly_typed_journal_client',
                    return_value=client),
              patch('src.backend.typed_backtest_financial_review.load_typed_backtest_financial_page',
                    return_value=page)):
            async with AsyncClient(transport=ASGITransport(app=app),
                                   base_url='http://test') as http:
                response = await http.get(
                    f'/api/trading/backtest/runs/{run_id}/typed-financial-page')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), page)
        client.close.assert_called_once()
