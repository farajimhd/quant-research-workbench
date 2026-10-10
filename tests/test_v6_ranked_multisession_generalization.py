import json
import pytest
from research.rl_trading.v1.common import file_hash
from research.rl_trading.v6.run_ranked_teacher_prepare import save_cache
from research.rl_trading.v6.ranked_teacher_data import coverage_report
from research.rl_trading.v6.run_ranked_multisession_generalization import bind_train_cache, require_natural_train_gate
from test_rl_trading_v6_teacher_forecast import fixture


def prepared(tmp_path):
    _,s,t=fixture();root=tmp_path/'day'
    scope=dict(day='2026-07-31',target_ids=['A'],begin_us=0,end_us=600_000_000,coverage=coverage_report(t,2))
    binding=dict(day='2026-07-31',seconds=600,target_listing_ids=['A'],input_listings=['A','B'],
        dataset_sha256='labels',market_dataset_sha256='market',bank_certificate_sha256='fixture',
        market_certificate_sha256='marketcert',context_split_receipt_sha256=None)
    save_cache(root,s,t,scope,binding)
    b=dict(day='2026-07-31',source_sha256=file_hash(root/'source.json'),cache_sha256=file_hash(root/'prepared-train.pt'),
        input_listings=['A','B'],bank_certificate_sha256='fixture',context_split_receipt_sha256=None)
    return root,b,dict(dataset_sha256='labels',market_dataset_sha256='market')


def test_actual_train_cache_binding_roundtrip(tmp_path):
    root,b,p=prepared(tmp_path);s,t=bind_train_cache(root,root/'source.json',b,p)
    assert s.role=='train' and s.listings==('A','B') and len(t)==12


def test_natural_train_gate_rejects_failed_fit_and_nonexact_replay():
    from copy import deepcopy
    from test_v6_ranked_multisession_metrics import report
    from research.rl_trading.v6.ranked_multisession_metrics import pool_gate_metrics
    good=pool_gate_metrics([report(),report()])
    bad=deepcopy(good);bad['action_class_f1']['enter_long']=.418
    for metrics,exact in [(bad,True),(good,False)]:
        with pytest.raises(ValueError,match='development targets remain unopened'):
            require_natural_train_gate(metrics,exact)
    require_natural_train_gate(good,True)


@pytest.mark.parametrize('changed',['source','cache','population','dataset'])
def test_changed_train_evidence_is_rejected(tmp_path,changed):
    root,b,p=prepared(tmp_path)
    if changed=='source':(root/'source.json').write_text(json.dumps({}))
    elif changed=='cache':(root/'prepared-train.pt').write_bytes(b'changed')
    elif changed=='population':b['input_listings']=['A']
    else:p['dataset_sha256']='other'
    with pytest.raises(ValueError):bind_train_cache(root,root/'source.json',b,p)
