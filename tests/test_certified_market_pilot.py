from datetime import date
from dataclasses import replace
from unittest.mock import Mock
import pytest
from scripts.profile_certified_market import pilot_guard,ticker_scope
from pipelines.certified_research.contracts import CampaignConfig,digest
from pipelines.certified_research.campaign import PlannedUnit
from pipelines.certified_research.execution import Unit


def test_explicit_pilot_strata_are_bounded_and_exact():
    import argparse
    assert ticker_scope('F,GE,BRID')==('F','GE','BRID')
    for value in ('', 'AAPL,AAPL', ',AAPL', 'AAPL;SELECT', ','.join('T'+str(i) for i in range(9))):
        with pytest.raises(argparse.ArgumentTypeError):ticker_scope(value)


def test_pilot_reservation_binds_scope_and_preserves_reserve():
    config=CampaignConfig(date(2026,1,2),date(2026,1,2),workers=1,product_scope='market_bars')
    plans=(PlannedUnit(Unit('a','market_aggregate'),{}),)
    query=Mock(return_value=[dict(name=p,free_space=2*1024**4,unreserved_space=2*1024**4)
                             for p in ('live_market_ssd','sip_raw_ssd')])
    guard=pilot_guard(query,config,plans)
    result=guard('a'*64,plans)
    fingerprint=result.pop('certificate_hash')
    assert fingerprint==digest(result)
    assert result['reservation_bytes']==128*1024**3
    with pytest.raises(ValueError,match='finite reservation'):
        pilot_guard(query,replace(config,workers=2),plans)
    with pytest.raises(ValueError,match='finite reservation'):
        pilot_guard(query,config,tuple(PlannedUnit(Unit(str(i),'market_aggregate'),{}) for i in range(9)))
    with pytest.raises(ValueError,match='plan changed'):
        guard('a'*64,())
    query.return_value=[dict(name=p,free_space=1024**4,unreserved_space=1024**4)
                        for p in ('live_market_ssd','sip_raw_ssd')]
    with pytest.raises(ValueError,match='reservation plus'):
        guard('a'*64,plans)
