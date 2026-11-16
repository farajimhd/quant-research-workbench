from types import SimpleNamespace

import pytest
from research.bar_gpt.phase1_v5.reporting import categorized,configure,publish


def validation():
    return dict(masked_objective=.2,masked_families=[.1,.3,.2],all_visible_families=[.1,.2,.1],
        full_raw_mae={'1s':{'trade_close':.05,'trade_volume':20.,'bid_close':None}},
        reference_raw_mae=[1.,2.,3.,4.,5.,6.,7.],negative_activity_predictions=[0,1,2,3,4,5],
        full_population=True,origins=100,raw_nonfinite=0,reference_nonfinite=0,
        **{'full_price_mape_pct/1s/trade_close':1.25,
           'full_price_mape_pct_by_price_band/below_1/1s/trade_close':4.,
           'full_price_relative_counts/1s/trade_close':64,
           'full_price_relative_quantile_bin_relative_width':.005})


def test_all_nested_metrics_keep_named_meaning_and_units():
    result=categorized(validation(),2,45580)
    assert result['validation_full_raw_mae/1s/trade_close']==.05
    assert result['validation_full_raw_mae/1s/trade_volume']==20.
    assert result['validation_references_raw_mae/price']==1.
    assert result['validation_references_raw_mae/ask_count']==7.
    assert result['validation_loss/masked_activity']==.3
    assert result['validation_full_price_mape_pct/1s/trade_close']==1.25
    assert result['validation_full_price_mape_pct_by_price_band/below_1/1s/trade_close']==4.
    assert not any('volume' in key for key in result if key.startswith('validation_full_price_mape_pct/'))
    assert result['validation_price_coverage/counts/1s/trade_close']==64
    assert 'validation_full_raw_mae/1s/bid_close' not in result
    assert result['validation_health/unavailable/validation_full_raw_mae/1s/bid_close']==1
    assert result['validation_optimizer_step']==45580


def test_future_fields_are_preserved_and_bad_shapes_fail():
    assert categorized({'new_result':{'count':7}},1,10)['validation_other/new_result/count']==7
    with pytest.raises(ValueError,match='families'):categorized({'masked_families':[1.]},1,10)


def test_backfill_uses_custom_evaluation_axis_and_next_open_row(tmp_path):
    calls=[];definitions=[]
    reporter=SimpleNamespace(id='original-run',step=45582)
    def log(metrics,*,step,commit):
        calls.append((metrics,step,commit));reporter.step=step+1
    reporter.log=log;reporter.define_metric=lambda *a,**kw:definitions.append((a,kw))
    configure(reporter)
    assert publish(reporter,validation(),1,22792,tmp_path)
    assert publish(reporter,validation(),2,45580,tmp_path)
    assert [row[1] for row in calls]==[45582,45583]
    assert [row[0]['validation_optimizer_step'] for row in calls]==[22792,45580]
    assert all(row[2] for row in calls)
    assert not publish(reporter,validation(),1,22792,tmp_path)
    assert any(a==('validation_full_raw_mae/*',) and kw['step_metric']=='validation_optimizer_step' for a,kw in definitions)
    changed=validation();changed['masked_objective']=.3
    with pytest.raises(ValueError,match='changed'):publish(reporter,changed,1,22792,tmp_path)
    reporter.id='different-run'
    with pytest.raises(ValueError,match='another run'):publish(reporter,validation(),1,22792,tmp_path)
