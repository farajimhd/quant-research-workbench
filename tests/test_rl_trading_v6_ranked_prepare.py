from dataclasses import replace
import json
import pytest
from research.rl_trading.v6.ranked_teacher_data import load_prepared,coverage_report
from research.rl_trading.v6.run_ranked_teacher_prepare import save_cache
from test_rl_trading_v6_teacher_forecast import fixture


def test_real_prepared_cache_roundtrip_and_resume_binding(tmp_path):
    _,session,targets=fixture()
    scope=dict(day='2026-07-31',target_ids=['A'],begin_us=0,end_us=600_000_000,
        coverage=coverage_report(targets,2))
    binding=dict(day='2026-07-31',seconds=600,target_listing_ids=['A'],input_listings=['A','B'],
        dataset_sha256='labels',market_dataset_sha256='market',bank_certificate_sha256='fixture',
        market_certificate_sha256='marketcert',context_split_receipt_sha256=None)
    plan=save_cache(tmp_path/'day',session,targets,scope,binding)
    restored,labels,_=load_prepared(tmp_path/'day',plan)
    assert restored.listings==session.listings and len(labels)==len(targets)
    bad=dict(plan,dataset_sha256='other')
    with pytest.raises(ValueError,match='binding changed'):load_prepared(tmp_path/'day',bad)
    with pytest.raises(ValueError,match='TRAIN'):save_cache(tmp_path/'sealed',replace(session,role='sealed_test'),targets,scope,binding)
    with pytest.raises(ValueError,match='TRAIN'):save_cache(tmp_path/'subset',replace(session,listings=('A',)),targets,scope,binding)
