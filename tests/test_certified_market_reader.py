from datetime import date,datetime,timezone
from unittest.mock import Mock,patch

import pytest

from pipelines.certified_research import market_reader
from pipelines.certified_research.publication import Scope

SCOPE=Scope('00000000-0000-0000-0000-000000000001',date(2026,1,2),91,'bars','a'*64)
CUTOFF=datetime(2026,1,2,14,30,1,123456,tzinfo=timezone.utc)


def test_read_pins_attempt_and_exact_completed_bucket_clock():
    query=Mock(return_value=[{'close_int':10000}]);guard=Mock();claim=Mock()
    receipt={'attempt_id':'00000000-0000-0000-0000-000000000002','status':1}
    publisher=Mock();publisher.receipt.return_value=receipt
    with patch.object(market_reader,'Publisher',return_value=publisher):
        result=market_reader.read(query,'arte',SCOPE,claim,guard,cutoff=CUTOFF)
    sql=query.call_args.args[0]
    assert receipt['attempt_id'] in sql and 'America/New_York' in sql
    assert f"<={result['cutoff_us']}" in sql
    assert result['cutoff_us']%1000000==123456
    assert 'LIMIT 100001' in sql and 'INSERT' not in sql
    assert publisher.receipt.call_count==2 and guard.call_count==3


@pytest.mark.parametrize('failure',('missing','changed','overflow','authority'))
def test_incomplete_or_changed_authority_never_returns_rows(failure):
    receipt={'attempt_id':'00000000-0000-0000-0000-000000000002'}
    publisher=Mock();publisher.receipt.side_effect=[None if failure=='missing' else receipt,
        dict(receipt,status=2) if failure=='changed' else receipt]
    query=Mock(return_value=[{},{}] if failure=='overflow' else [{}])
    guard=Mock(side_effect=[None,ValueError('authority changed')] if failure=='authority' else None)
    with patch.object(market_reader,'Publisher',return_value=publisher),pytest.raises(ValueError):
        market_reader.read(query,'arte',SCOPE,Mock(),guard,cutoff=CUTOFF,maximum_rows=1)


def test_naive_cutoff_and_implicit_liquidity_frame_are_rejected_before_reads():
    query=Mock()
    with pytest.raises(ValueError):market_reader.read(query,'arte',SCOPE,Mock(),Mock(),cutoff=CUTOFF.replace(tzinfo=None))
    liquidity=Scope(SCOPE.dataset_id,SCOPE.session_date,91,'broker_100ms','a'*64)
    with pytest.raises(ValueError):market_reader.read(query,'arte',liquidity,Mock(),Mock(),cutoff=CUTOFF)
    query.assert_not_called()
