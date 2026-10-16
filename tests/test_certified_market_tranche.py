from datetime import date
from types import SimpleNamespace
import pytest
from scripts.run_certified_market_tranche import request_identity


def test_tranche_is_scoped_by_actual_exchange_sessions():
    args=SimpleNamespace(start_date=date(2026,1,2),end_date=date(2026,1,13),tickers=('AAPL',),
                         anchor_date=date(2026,10,15),conid_review_hash='a'*64)
    assert request_identity(args)['database']=='arte'
    args.end_date=date(2026,1,14)
    with pytest.raises(ValueError,match='eight'):request_identity(args)
    args.end_date=date(2026,1,13);args.tickers=('AAPL','TSLA')
    with pytest.raises(ValueError,match='eight'):request_identity(args)
