import asyncio
from datetime import date
from types import SimpleNamespace

import pytest

from src.backend.replay_run_service import ReplayRunController, RunMode
from src.market_engine.derived_trade_policy import POLICY


@pytest.mark.parametrize('book_id', ['level-book-v7', 'level-book-v7-SUGP'])
@pytest.mark.parametrize('policy', [None, 'legacy-unfiltered', POLICY])
def test_ladder_preflight_checks_single_symbol_and_filtered_seed(tmp_path, monkeypatch, book_id, policy):
    from src.backend import qmd_gateway_client

    async def publish(**kwargs):
        pass

    calls = []
    def coverage(path, request, **kwargs):
        calls.append(request)
        return dict(catalog_hash='fixture', rows=[dict(ticker='SUGP', eligible=True, input_policy=policy)])

    monkeypatch.setattr(qmd_gateway_client, 'qmd_history_post_json', coverage)
    controller = SimpleNamespace(
        definition=SimpleNamespace(mode=RunMode.BACKTEST, experimental_structure_book=book_id,
            execution_mode='strategy', session_date=date(2026, 8, 21), final_session_date=None,
            configuration_revision={'payload': {'strategy': {'parameters': {'vwap_ladder_contract': 'v1'}}}}),
        _selected_assignments=lambda: [dict(ticker='SUGP')], _publish=publish,
        _v7_excluded_tickers=set(), run_dir=tmp_path, _journal=None,
        _record_data_authority=lambda *args: None)
    if policy == POLICY:
        asyncio.run(ReplayRunController._prepare_v7_coverage(controller))
        assert controller._v7_coverage_report['eligible_ticker_count'] == 1
    else:
        with pytest.raises(ValueError, match='data preparation failure, not a zero-trade'):
            asyncio.run(ReplayRunController._prepare_v7_coverage(controller))
    assert calls == [dict(tickers=['SUGP'], as_of='2026-08-21T04:00:00-04:00')]
