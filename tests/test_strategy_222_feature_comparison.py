import importlib.util
from pathlib import Path
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]/'scripts'
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location('feature_comparison', SCRIPTS/'strategy_222_feature_comparison.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def decision(price):
    return dict(action='hold', reason='test', metadata=dict(reference_price=price,
        macd=dict(observed_at=9., line=.2, signal=.1, episode=8., kind='forming')))


def test_same_timestamp_after_fill_is_excluded():
    point = dict(at=10., sequence_limit=5)
    rows = [(9., 1, decision(10)), (10., 4, decision(20)), (10., 6, decision(100))]
    result = m.sample(point, 0, rows)
    assert result['decision_sequence'] == 4
    assert result['features']['macd_histogram_bps'] == 50


def test_future_decision_and_stale_decision_are_not_used():
    point = dict(at=10., sequence_limit=None)
    assert m.sample(point, 0, [(7., 1, decision(10)), (11., 2, decision(20))])['status'].startswith('missing')


def test_future_macd_fails_closed():
    with pytest.raises(ValueError, match='Future'):
        m.features(decision(10)['metadata'], 8.)


def test_allowlist_excludes_identity_outcome_and_absolute_clock():
    metadata = decision(10)['metadata']
    metadata.update(ticker='EXAMPLE', net=1000, hindsight_peak=100, outcome='winner')
    result = m.features(metadata, 10.)
    assert set(result) == {'macd_age_s', 'macd_episode_age_s', 'macd_histogram_bps', 'macd_is_forming'}
    assert result['macd_episode_age_s'] == 2


def test_research_geometry_requires_measured_causal_evidence_and_preserves_existing_base():
    research=dict(status='measured',observed_at=9.,risk_pct=2.,range_pct=3.)
    assert m.features({'research_base_assessment':research},10.)['base_risk_pct']==2.
    assert m.features({'research_base_assessment':dict(research,status='unavailable')},10.)=={}
    with pytest.raises(ValueError,match='Future'):
        m.features({'research_base_assessment':dict(research,observed_at=11.)},10.)
    existing=dict(observed_at=9.,risk_pct=1.)
    assert m.features({'early_base_assessment':existing,'research_base_assessment':research},10.)['base_risk_pct']==1.


def test_recovery_geometry_uses_only_recorded_prior_position_and_base():
    metadata = {'reference_price':10., 'setup_recovery':{'last_exit':{
        'at':5., 'initial_fill_price':12., 'stop':11., 'body_high':13.,
        'setup':{'breakout_threshold':12.5}}}, 'research_base_assessment':{
        'status':'measured','observed_at':10.,'range':{'start':6.,'end':9.},
        'swing':{'confirmed_at':9.,'lower':9.5}}}
    result=m.features(metadata,10.)
    assert result['price_to_previous_initial_fill_pct']==pytest.approx((10/12-1)*100)
    assert result['price_to_previous_reclaim_pct']==pytest.approx((10/13-1)*100)
    assert result['support_to_previous_stop_pct']==pytest.approx((9.5/11-1)*100)
    assert result['base_entirely_after_previous_exit']==1.
    metadata['setup_recovery']['last_exit']['at']=11.
    with pytest.raises(ValueError,match='Future'):m.features(metadata,10.)


def test_ties_have_half_auc():
    assert m.auc([2, 2], [2, 2]) == .5
    assert m.auc([3], [1, 2]) == 1


def test_group_deduplication_and_missing_denominators():
    def row(group, label, value, symbol='X'):
        return dict(group=group, label=label, symbol=symbol, kind='move_onset', offset_s=0,
                    features={} if value is None else {'a':value})
    rows = [row('p','major',3),row('p','major',3),row('q','major',None),row('n','small',1,'Y')]
    result = m.contrasts(rows)[0]
    assert (result['positive_n'],result['positive_total'],result['negative_n']) == (1,2,1)
    assert result['auc'] == 1
    assert not result['direction_survives_every_ticker_removal']


def test_ticker_removal_exposes_reversal():
    rows = [dict(group=str(i),label=label,symbol=symbol,kind='move_onset',offset_s=0,features={'a':value})
            for i,(label,symbol,value) in enumerate([
                ('major','X',100),('major','X',101),('major','Y',0),
                ('small','X',1),('small','Y',2)])]
    result=m.contrasts(rows)[0]
    assert result['auc'] > .5
    assert result['leave_one_ticker_out_min_auc'] == 0
    assert not result['direction_survives_every_ticker_removal']


def test_hindsight_anchors_deduplicate_original_positions_and_keep_open_label():
    case=dict(opportunity_id='X:10',symbol='X',major_move=True,peak_time='1970-01-01T00:00:20+00:00')
    trial=dict(tickers=['X'],run_id='r',episodes=[dict(symbol='X',opened_at='1970-01-01T00:00:11+00:00',fills=[dict(sequence=5)])])
    result=m.anchors([case,case],trial)
    assert len(result)==3
    assert result[-1]['label']=='open'
    assert result[-1]['sequence_limit']==5
