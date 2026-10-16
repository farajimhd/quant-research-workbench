from datetime import date
from types import SimpleNamespace
import pytest
from scripts.run_certified_market_tranche import request_identity,run_launch,validate_saved
from pipelines.certified_research.execution import execute,Unit


def test_tranche_is_scoped_by_actual_exchange_sessions():
    args=SimpleNamespace(start_date=date(2026,1,2),end_date=date(2026,1,13),tickers=('AAPL',),
                         anchor_date=date(2026,10,15),conid_review_hash='a'*64)
    assert request_identity(args)['database']=='arte'
    args.end_date=date(2026,1,14)
    with pytest.raises(ValueError,match='eight'):request_identity(args)
    args.end_date=date(2026,1,13);args.tickers=('AAPL','TSLA')
    with pytest.raises(ValueError,match='eight'):request_identity(args)


def test_real_execution_receives_event_and_drains_owned_marker(tmp_path):
    class Launch:
        def run(self,*,stop,progress):
            def work(unit,parents,received):
                assert received is stop and hasattr(received,'is_set')
                return dict(output_hash='a'*64)
            return execute((Unit('one','test'),),{'test':work},workers=1,stop=stop,progress=progress)
    assert run_launch(Launch(),tmp_path,None)['status']=='complete'
    (tmp_path/'stop.request').touch()
    assert run_launch(Launch(),tmp_path,None)['status']=='interrupted'


def test_saved_tranche_requires_pinned_config_hash():
    from pipelines.certified_research.contracts import CampaignConfig
    config=CampaignConfig(date(2026,1,2),date(2026,1,13),product_scope='market_bars',
        identity_policy='current_conid_retrospective_v1',conid_review_hash='a'*64)
    saved=SimpleNamespace(config=config,units=())
    request=dict(start='2026-01-02',end='2026-01-13',database='arte',conid_review_hash='a'*64,tickers=('AAPL',))
    with pytest.raises(ValueError,match='configuration'):validate_saved(saved,dict(config_hash='b'*64),request)
