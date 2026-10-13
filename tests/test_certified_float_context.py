from datetime import date
from unittest.mock import Mock,patch

import pytest

from pipelines.certified_research.float_data import required_price_sessions
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
