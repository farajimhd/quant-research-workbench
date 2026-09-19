from copy import deepcopy
from dataclasses import replace

import pytest

from src.trading_runtime import early_squeeze_fast as F
from tests.test_early_squeeze_fast import fixture
from tests.test_vwap_resistance_ladder import advance, NOW


def corrected_fixture():
    h,a,old=fixture()
    a=replace(a,parameters={**a.parameters,'early_squeeze_breakout_contract':F.CORRECTED_CONTRACT})
    def obs(*args,**kwargs):
        o=old(*args,**kwargs)
        market=deepcopy(o.structural_detector_state)
        market['row']['effective_at']=NOW.timestamp()-60
        market['fast_structure_evidence']=dict(as_of=int(o.observed_at.timestamp()),max_input_timestamp=int(o.observed_at.timestamp()))
        sources={k:v for k,v in o.source_values.items() if k!='indicator.vwap.execution_value@1s'}
        sources['indicator.vwap.execution_value@100ms']=dict(value=9.,observed_at=o.observed_at.isoformat())
        return replace(o,structural_detector_state=market,source_values=sources,bar_volume=1000.)
    return h,a,obs


@pytest.mark.parametrize('body,admitted',[(.0124,False),(.0125,True),(.015,True)])
def test_125_boundary_without_fresh_one_second_row(body,admitted):
    h,a,o=corrected_fixture();a=advance(a,h.evaluate(a,o()))
    r=h.evaluate(a,o(.1,10.48,body=body,mean=.01))
    assert bool(r.evaluation.intents)==admitted
    if admitted:
        m=r.evaluation.intents[0].metadata
        assert m['candle_strength']['multiplier']==1.25
        assert m['vwap_gate']['source_id'].endswith('@100ms')


@pytest.mark.parametrize('proof',[{},dict(as_of=NOW.timestamp()-10,max_input_timestamp=NOW.timestamp()-10),
    dict(as_of=NOW.timestamp(),max_input_timestamp=NOW.timestamp()+1)])
def test_missing_stale_or_future_structure_still_fails_closed(proof):
    h,a,o=corrected_fixture();a=advance(a,h.evaluate(a,o()))
    bar=o(.1,10.48,body=.09)
    market=dict(bar.structural_detector_state,fast_structure_evidence=proof)
    assert not h.evaluate(a,replace(bar,structural_detector_state=market)).evaluation.intents


@pytest.mark.parametrize('new_cross',[False,True])
def test_recovery_stop_uses_latest_break_instead_of_old_anchor_or_swing(new_cross):
    h,a,o=corrected_fixture();a=advance(a,h.evaluate(a,o()))
    bar=o(.1,10.6,body=.1)
    old=dict(bar.structural_resistance_levels[0],lower=9.,upper=9.02)
    recent=dict(old,lower=10.4,upper=10.42,unified_level_id='latest')
    d=a.state['squeeze_breakout']
    d['recovery']=dict(anchor=old,high=10.5,stopped_at=NOW.timestamp(),breakout_at=NOW.timestamp()-10)
    d['latest_broken_resistance']=recent
    if not new_cross:
        d['close']=10.55
    market=deepcopy(bar.structural_detector_state);market['fast_squeeze_context']['prior_hod']=9.
    r=h.evaluate(a,replace(bar,structural_detector_state=market))
    i=r.evaluation.intents[0]
    assert i.reason=='stopout_close_high_reentry'
    assert i.invalidation_price==pytest.approx(10.39)
    assert i.metadata['entry_selection']['unified_level_id']==('R2' if new_cross else 'latest')
    assert i.metadata['stop_source']=='latest_broken_resistance_lower'
    # Moving the stop must not silently move the next recovery's entry gate.
    from src.trading_runtime.early_squeeze_breakout import record_exit
    r.state['squeeze_entry']['first_fill_at']=NOW.timestamp()+.1
    record_exit(r.state,bar.observed_at,'protective_stop',0.,contract=F.CORRECTED_CONTRACT)
    assert r.state['squeeze_breakout']['recovery']['anchor']['upper']==9.02


def test_latest_stop_band_does_not_add_a_new_reentry_clearance_gate():
    h,a,o=corrected_fixture();a=advance(a,h.evaluate(a,o()))
    bar=o(.1,10.6,body=.1)
    old=dict(bar.structural_resistance_levels[0],lower=9.,upper=9.02)
    recent=dict(old,lower=10.56,upper=10.58,unified_level_id='latest')
    d=a.state['squeeze_breakout'];d.update(close=10.55,latest_broken_resistance=recent,
        recovery=dict(anchor=old,high=10.5,stopped_at=NOW.timestamp(),breakout_at=NOW.timestamp()-10))
    market=deepcopy(bar.structural_detector_state);market['fast_squeeze_context']['prior_hod']=9.
    r=h.evaluate(a,replace(bar,structural_detector_state=market))
    assert r.evaluation.intents[0].invalidation_price==pytest.approx(10.55)
