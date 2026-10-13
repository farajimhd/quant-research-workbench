from types import SimpleNamespace
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
    async def test_exact_journal_http_query_uses_owned_worker_and_preserves_price(self):
        from threading import get_ident
        run_id = str(uuid4())
        client = Mock()
        main_thread = get_ident()
        price = 10.001000000000001
        def read(actual_client, actual_id, **scope):
            self.assertIs(actual_client, client)
            self.assertEqual(actual_id, run_id)
            self.assertTrue(scope["exact_prices"] and scope["journal_only"])
            self.assertNotEqual(get_ident(), main_thread)
            return {"events": [{"detail": {"price": price}}], "verified_sequence": 20}
        with (patch('src.trading_runtime.arte_journal_writer.backtest_v4_operator_client_from_env', return_value=client),
              patch('src.backend.backtest_v4_query.query_saved_journal', side_effect=read)):
            async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as http:
                response = await http.get(f'/api/trading/backtest/runs/{run_id}/v4-journal-query'
                    '?journal_only=true&exact_prices=true')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["events"][0]["detail"]["price"].hex(), price.hex())
        client.close.assert_called_once()

    async def test_exact_journal_http_query_rejects_missing_authority_before_client(self):
        with patch('src.trading_runtime.arte_journal_writer.backtest_v4_operator_client_from_env') as factory:
            async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as http:
                response = await http.get(f'/api/trading/backtest/runs/{uuid4()}/v4-journal-query?exact_prices=true')
        self.assertEqual(response.status_code, 400)
        factory.assert_not_called()

    def test_run_chart_cannot_expand_past_backtest_boundary(self):
        from src.backend.app import trading_canvas_live_chart_history
        cutoff = datetime(2026, 8, 21, 8, tzinfo=timezone.utc)
        controller = Mock(
            current_time=cutoff,
            definition=SimpleNamespace(configuration_revision={"payload": {"strategy": {}}}),
        )
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

    async def test_exact_running_http_route_forwards_v4_mode(self):
        run_id = str(uuid4())
        client = Mock()
        page = {"schema_version": "typed-backtest-running-page-v4", "events": []}
        with (patch('src.trading_runtime.arte_journal_reader.readonly_typed_journal_client', return_value=client),
              patch('src.backend.typed_backtest_review_core.load_typed_backtest_running_page', return_value=page) as read,
              patch.object(backtest_run_service, 'review_saved', new=AsyncMock()) as saved):
            async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as http:
                response = await http.get(f'/api/trading/backtest/runs/{run_id}/typed-running-page?exact_prices=true')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), page)
        read.assert_called_once_with(client, run_id, after_sequence=0, limit=500, exact_prices=True)
        client.close.assert_called_once()
        saved.assert_not_called()

    def test_typed_financial_route_is_separate_from_saved_review(self):
        paths = {route.path: route.methods for route in app.routes if hasattr(route, 'methods')}
        self.assertIn('GET', paths['/api/trading/backtest/runs/{run_id}/typed-financial-page'])

    async def test_exact_financial_http_route_forwards_mode_and_closes_client(self):
        run_id = str(uuid4())
        client = Mock()
        page = {"fills": [{"price": 10.250000000000002}]}
        with (patch('src.trading_runtime.arte_journal_reader.readonly_typed_journal_client', return_value=client),
              patch('src.backend.typed_backtest_financial_review.load_typed_backtest_financial_page', return_value=page) as read,
              patch.object(backtest_run_service, 'review_saved', new=AsyncMock()) as saved):
            async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as http:
                response = await http.get(f'/api/trading/backtest/runs/{run_id}/typed-financial-page?exact_prices=true')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), page)
        read.assert_called_once_with(client, run_id, after_fill_sequence=0,
                                     after_commission_sequence=0, limit=500, exact_prices=True)
        client.close.assert_called_once()
        saved.assert_not_called()

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

    async def test_complete_financial_http_evidence_uses_owned_exact_reader(self):
        run_id = str(uuid4())
        client = Mock()
        page = {"complete": True, "fills": [], "commissions": []}
        with (patch('src.trading_runtime.arte_journal_reader.readonly_typed_journal_client', return_value=client),
              patch('src.backend.typed_backtest_financial_review.load_typed_backtest_financial_evidence', return_value=page) as read):
            async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as http:
                response = await http.get(f'/api/trading/backtest/runs/{run_id}/typed-financial-page'
                    '?exact_prices=true&complete_evidence=true&maximum_rows=5000&limit=100')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), page)
        read.assert_called_once_with(client, run_id, page_size=100, maximum_rows=5000)
        client.close.assert_called_once()

    async def test_complete_financial_http_evidence_rejects_partial_scope_before_io(self):
        run_id = str(uuid4())
        with patch('src.trading_runtime.arte_journal_reader.readonly_typed_journal_client') as factory:
            async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as http:
                for query in ('complete_evidence=true',
                    'complete_evidence=true&exact_prices=true',
                    'complete_evidence=true&exact_prices=true&maximum_rows=50&after_fill_sequence=1'):
                    response = await http.get(f'/api/trading/backtest/runs/{run_id}/typed-financial-page?{query}')
                    self.assertEqual(response.status_code, 400)
        factory.assert_not_called()
