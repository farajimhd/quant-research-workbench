from pathlib import Path
import json
import pytest
from scripts.audit_market_day_reporting_repair import independent_prices, recurrence
from research.rl_trading.v6.run_reporting_repair import bar_arguments
from research.rl_trading.v6.opportunity_dataset import reporting_plan, write_json, digest, REPORTING_REVISION


def test_repair_resumes_exact_immutable_build(tmp_path):
    assert '--rebuild' in bar_arguments(tmp_path)
    (tmp_path/'latest.json').write_text(json.dumps(dict(status='failed',build_id='pinned')))
    arguments=bar_arguments(tmp_path)
    assert '--rebuild' not in arguments
    assert arguments[-2:]==['--build-id','pinned']
    assert '--allow-carried-forward-universe' in arguments


def test_independent_ohlc_excludes_delayed_trade_even_at_extreme():
    rule=dict(token_id=1,modifier_int=0,update_last=1,update_high_low=1,update_volume=1)
    base=dict(sip_timestamp_us=14400*1_000_000,size_primary=1.,condition_token_1=1,
        **{f'condition_token_{i}':0 for i in range(2,6)})
    events=[dict(base,event_meta=67,price_primary_int=100000),
        dict(base,event_meta=195,price_primary_int=1000),
        dict(base,event_meta=67,price_primary_int=101000)]
    result,evidence=independent_prices(events,[rule],0)
    assert result[14400]==dict(open_int=100000,close_int=101000,high_int=101000,low_int=100000)
    assert evidence==dict(delayed_excluded=1,unknown_clock_trades=0)


def test_independent_macd_recurrence_detects_wrong_signal():
    row=dict(close_int=100000,ema_12=10.,ema_26=10.,macd_line=0.,macd_signal=0.)
    assert recurrence([row])==((10.,10.,0.),0.)
    with pytest.raises(ValueError,match='MACD recurrence mismatch'):
        recurrence([dict(row,macd_signal=1.)])


def test_labels_reject_legacy_or_unverified_feature_plans(tmp_path):
    plan=dict(day='2026-07-31',previous_day=None)
    write_json(tmp_path/'plan.json',dict(plan,hash=digest(plan)))
    with pytest.raises(ValueError,match='reporting-certified'):
        reporting_plan(tmp_path,'2026-07-31')
    plan['reporting_coverage']={'current':[dict(source_date='2026-07-31',
        revision=REPORTING_REVISION,status='complete',counts={'bad':0})]}
    write_json(tmp_path/'plan.json',dict(plan,hash=digest(plan)))
    assert reporting_plan(tmp_path,'2026-07-31')['day']=='2026-07-31'
