import pytest
from scripts.freeze_structural_stock_population import safe_url, select_stocks


def test_reference_pagination_rejects_foreign_hosts_and_strips_keys():
    assert 'secret' not in safe_url('https://api.massive.com/v3/reference/tickers?cursor=abc&apiKey=secret')
    with pytest.raises(ValueError):
        safe_url('https://foreign.example/v3/reference/tickers')


def test_classification_preserves_rank_and_keeps_splits_disjoint():
    p = dict(ranked_eligible=[dict(source_ticker=f'T{i}') for i in range(12)])
    refs = [dict(ticker=f'T{i}', market='stocks', locale='us', type='ETF' if i == 0 else 'CS') for i in range(12)]
    result = select_stocks(p, refs)
    assert result['development'] == ['T1', 'T2', 'T3', 'T4', 'T5']
    assert result['sealed_holdout'] == ['T6', 'T7', 'T8', 'T9', 'T10']
    assert result['counts']['not_common_stock'] == 1
    with pytest.raises(ValueError, match='Unknown classification'):
        select_stocks(p, refs[1:])
