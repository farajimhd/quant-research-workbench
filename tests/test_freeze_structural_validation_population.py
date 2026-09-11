import pytest

from scripts.freeze_structural_validation_population import select


def rows():
    return [dict(source_ticker=f'T{i}', session_date='2026-07-31', session_kind='regular',
                 source_contract='ordered_sip_events_unadjusted', adjusted=0, available_at_us=1,
                 trade_present=1, trade_close=5, trade_size_sum=2000000,
                 trade_price_size_sum=10000000) for i in range(20)]


def test_selection_is_order_independent_and_has_disjoint_splits():
    a = select(rows())
    assert a == select(list(reversed(rows())))
    assert len(set(a['development'] + a['sealed_holdout'])) == 10


def test_invalid_authority_and_future_availability_fail_closed():
    for field, value in [('adjusted', 1), ('available_at_us', 2**63), ('trade_close', float('nan'))]:
        data = rows()
        data[0][field] = value
        with pytest.raises(ValueError):
            select(data)


def test_duplicates_and_insufficient_population_fail_closed():
    data = rows()
    with pytest.raises(ValueError, match='Duplicate'):
        select(data + data[:1])
    with pytest.raises(ValueError, match='Insufficient'):
        select(data[:9])
