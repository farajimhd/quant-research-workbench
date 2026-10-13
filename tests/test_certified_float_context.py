from datetime import date
from unittest.mock import Mock,patch

import pytest

from pipelines.certified_research.float_data import required_price_sessions,source_price_sessions
from pipelines.certified_research.reference_publication import ReferenceScope
from pipelines.certified_research.planning import price_context_days


def scope():return ReferenceScope('00000000-0000-0000-0000-000000000001',1,1,'fundamentals',
    date(2024,1,1),date(2026,12,31),'a'*64,'b'*64)


def test_measurement_price_context_precedes_launch_and_uses_exchange_calendar():
    query=Mock(return_value=[dict(period_end='2025-12-31')])
    with patch('pipelines.certified_research.float_data.ReferencePublisher') as publisher:
        publisher.return_value.receipt.return_value=dict(attempt_id='00000000-0000-0000-0000-000000000002',output_hash='c'*64)
        result=required_price_sessions(query,'arte',Mock(),scope(),date(2026,1,2),date(2026,1,5))
    assert result['sessions'] and all(day<date(2026,1,2) for day in result['sessions'])
    assert date(2025,12,25) not in result['sessions']
    assert '520' not in query.call_args.args[0] # Bound is a concrete derived date.
    # UTC midnight can be the previous NY date; the oldest usable measurement
    # then precedes UTC start by 521 dates while its NY age is only 520 days.
    from datetime import timedelta
    assert str(date(2026,1,2)-timedelta(days=521)) in query.call_args.args[0]
    days=price_context_days((date(2026,1,2),date(2026,1,5)),{1:result})
    assert date(2025,12,31) in days and date(2026,1,5) in days
    with pytest.raises(ValueError,match='fingerprint'):
        price_context_days((date(2026,1,2),),{1:dict(result,sessions=())})


def test_uncertified_fundamentals_do_not_generate_guessed_price_dates():
    with patch('pipelines.certified_research.float_data.ReferencePublisher') as publisher:
        publisher.return_value.receipt.return_value=None
        with pytest.raises(ValueError,match='certified fundamentals'):
            required_price_sessions(Mock(),'arte',Mock(),scope(),date(2026,1,2),date(2026,1,5))


def test_changed_fundamental_receipt_invalidates_context():
    query=Mock(return_value=[dict(period_end='2025-12-31')])
    with patch('pipelines.certified_research.float_data.ReferencePublisher') as publisher:
        publisher.return_value.receipt.side_effect=[
            dict(attempt_id='00000000-0000-0000-0000-000000000002',output_hash='c'*64),
            dict(attempt_id='00000000-0000-0000-0000-000000000003',output_hash='d'*64)]
        with pytest.raises(ValueError,match='authority changed'):
            required_price_sessions(query,'arte',Mock(),scope(),date(2026,1,2),date(2026,1,5))


def test_measurement_bound_fails_without_truncating_history():
    query=Mock(return_value=[dict(period_end='2024-12-31'),dict(period_end='2025-12-31')])
    with patch('pipelines.certified_research.float_data.ReferencePublisher') as publisher:
        publisher.return_value.receipt.return_value=dict(attempt_id='00000000-0000-0000-0000-000000000002',output_hash='c'*64)
        with pytest.raises(ValueError,match='metadata bound'):
            required_price_sessions(query,'arte',Mock(),scope(),date(2026,1,2),date(2026,1,5),maximum_measurements=1)


@pytest.mark.parametrize('changed',(False,True))
def test_fresh_sec_source_context_reuses_publication_projection_and_fences_drift(changed):
    query=Mock(return_value=[dict(period_end='2025-12-31')])
    before=dict(source_hash='a'*64,rows=4)
    with patch('pipelines.certified_research.reference_sql.sec_source_evidence',side_effect=[
            before,dict(before,source_hash='b'*64) if changed else before]) as evidence:
        args=(query,Mock(),Mock(),1,'320193',date(2024,1,1),date(2026,1,5),date(2026,1,2),date(2026,1,5),'a'*64)
        if changed:
            with pytest.raises(ValueError,match='authority changed'):source_price_sessions(*args)
        else:
            result=source_price_sessions(*args)
            assert result['sec_source_hash']=='a'*64
            assert date(2025,12,31) in price_context_days((date(2026,1,2),date(2026,1,5)),{1:result})
        assert evidence.call_count==2
        sql=query.call_args.args[0]
        assert 'sec_bulk_mirror_xbrl_fact_v3' in sql and 'source_context_kind' in sql
        assert 'LIMIT 2049' in sql
        from datetime import timedelta
        assert str(date(2026,1,2)-timedelta(days=521)) in sql


def test_measurement_cap_separates_final_market_date_from_utc_availability_date():
    query=Mock(return_value=[]);proof=dict(source_hash='a'*64,rows=4)
    with patch('pipelines.certified_research.reference_sql.sec_source_evidence',return_value=proof):
        source_price_sessions(query,Mock(),Mock(),1,'320193',date(2025,1,1),date(2026,1,3),
            date(2026,1,2),date(2026,1,3),'a'*64,measurement_end=date(2026,1,2))
    sql=query.call_args.args[0]
    assert "AND toDate('2026-01-02')" in sql
    assert "available_at<toDateTime64('2026-01-04'" in sql
