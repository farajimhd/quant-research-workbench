import sys
from pathlib import Path
from copy import deepcopy
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from strategy_222_base_gate_audit import recovery_audit,H


def sample():
    settings=dict(H.DEFAULTS,setup_recovery_enabled=1,setup_recovery_stop_gain_guard=1,
        setup_base_recovery_maximum_range_pct=0)
    metadata=dict(reference_price=10.,research_base_assessment=dict(status='measured',observed_at=100.,
        checks={'fresh_support':True},failed=[],swing=dict(lower=9.8,pivot_at=95.,confirmed_at=99.),
        range={'start':90.,'end':99.},range_pct=2.,risk_pct=3.),setup_recovery={'last_exit':dict(
            at=80.,stop=10.1,body_high=10.5,setup={'phase':'post_breakout','breakout_threshold':10.2})})
    return metadata,settings


def test_passing_base_can_have_an_independent_recovery_blocker_without_mutation():
    metadata,settings=sample();original=deepcopy(metadata)
    result=recovery_audit(metadata,100.,settings)
    assert result['status']=='base_passed'
    assert result['recovery_reason']=='waiting_for_post_move_recovery_or_higher_base'
    assert metadata==original
    assert recovery_audit(metadata,100.,dict(settings,setup_base_recovery_maximum_range_pct=3))['recovery_reason']==''


def test_missing_and_failed_are_not_treated_as_passing():
    metadata,settings=sample()
    assert recovery_audit({},100.,settings)['status']=='unavailable'
    metadata['research_base_assessment']['checks']['fresh_support']=False
    metadata['research_base_assessment']['failed']=['fresh_support']
    assert recovery_audit(metadata,100.,settings)['status']=='base_failed'


def test_future_or_missing_recovery_evidence_fails_closed():
    metadata,settings=sample()
    with pytest.raises(ValueError,match='clock'):recovery_audit(metadata,99.,settings)
    metadata['setup_recovery']['last_exit']['at']=101.
    with pytest.raises(ValueError,match='Future'):recovery_audit(metadata,100.,settings)
    metadata.pop('setup_recovery')
    with pytest.raises(ValueError,match='authority'):recovery_audit(metadata,100.,settings)
