from copy import deepcopy
from dataclasses import replace

import pytest

from src.trading_runtime import early_squeeze_price as P, strategy_engine as S
from tests.test_early_squeeze_fast_corrected import corrected_fixture
from tests.test_vwap_resistance_ladder import advance


def fixture():
    h, a, old = corrected_fixture()
    a = replace(a, parameters={**a.parameters, 'early_squeeze_breakout_contract': P.CONTRACT})
    def trade(t=0., price=10.39, position=0.):
        o = old(t, price, position)
        market = deepcopy(o.structural_detector_state)
        market['fast_squeeze_context']['hod'] = 10.415
        market['price_vwap_evidence'] = dict(as_of=o.observed_at.isoformat(),
            source_observed_at=o.source_values['indicator.vwap.execution_value@100ms']['observed_at'],
            authority='latest-completed-qmd-100ms')
        return replace(o, source_timeframe='', evaluation_events=('market_data_update',),
            changed_source_ids=('market.last_price',), source_signal_ids=('trade:test',),
            bar_open=None, structural_detector_state=market,
            source_values={**o.source_values,
                'market.last_price':dict(value=price, observed_at=o.observed_at.isoformat()),
                'market.trade_size':dict(value=100., observed_at=o.observed_at.isoformat())})
    return h, a, trade


def test_sugp_midpoint_selection_does_not_skip_band_above_hod():
    intended = dict(unified_level_id='R1', role='resistance', lower=3.4906543358, upper=3.6066358204)
    gray = dict(unified_level_id='gray', role='transition', transition_from='resistance', lower=3.3643393429, upper=3.4293118384)
    assert P.entry_level({'R1':intended, 'gray':gray}, 3.55) == intended
    next_level = dict(unified_level_id='next', role='resistance', lower=3.6160640124, upper=3.6550229686)
    limit = P.boundary(intended, {'R1':intended, 'next':next_level})
    assert 3.53 < limit['price'] < 3.56
    assert P.below(intended['lower'], .01) == 3.49


def test_price_cross_enters_without_candle_or_body_and_displays_exact_boundary():
    h,a,trade=fixture()
    a=advance(a,h.evaluate(a,trade()))
    o=trade(.01,10.44)
    r=h.evaluate(a,o)
    i=r.evaluation.intents[0]
    assert i.action=='enter_long'
    assert i.invalidation_price==pytest.approx(10.39)
    assert i.metadata['entry_selection']['unified_level_id']=='R2'
    assert i.metadata['breakout_boundary']['price']==pytest.approx(10.43)
    assert i.metadata['unified_structural_trigger']['current_snapshot']['levels'][0]['entry_boundary']==pytest.approx(10.43)
    assert i.capital_request.value==pytest.approx(1/3)


@pytest.mark.parametrize('kind',['quote','bar','stale_price','stale_book','below','preactivation'])
def test_non_crossing_or_invalid_evidence_cannot_enter(kind):
    h,a,trade=fixture();a=advance(a,h.evaluate(a,trade()))
    o=trade(.01,10.44)
    if kind=='quote':o=replace(o,source_signal_ids=('quote:test',))
    if kind=='bar':o=replace(o,source_timeframe='100ms',evaluation_events=('bar_close',))
    if kind=='stale_price':o=replace(o,source_values={**o.source_values,'market.last_price':dict(value=10.44,observed_at='2000-01-01T00:00:00+00:00')})
    if kind=='stale_book':o=replace(o,structural_detector_state={**o.structural_detector_state,'fast_structure_evidence':{}})
    if kind=='below':o=trade(.02,10.429)
    if kind=='preactivation':
        a=replace(a,state={})
        o=replace(o,source_values={k:v for k,v in o.source_values.items() if not k.startswith('signal.activation')})
    assert not h.evaluate(a,o).evaluation.intents


def test_add_and_target_count_on_price_without_close():
    h,a,trade=fixture();a=advance(a,h.evaluate(a,trade()))
    a=advance(a,h.evaluate(a,trade(.01,10.44)))
    a.state['squeeze_entry'].update(first_fill_at=trade().observed_at.timestamp(),slice_notional=3000.)
    a=replace(a,status=S.AssignmentStatus.MANAGING)
    r=h.evaluate(a,trade(.02,10.84,100.))
    assert len(r.state['squeeze_entry']['broken_levels'])==2
    assert any(i.action=='add_long' for i in r.evaluation.intents)
    assert any(i.action=='replace_profit_target' for i in r.evaluation.intents)


def test_pending_breakout_can_enter_after_quality_recovers_but_not_after_recross_down():
    h,a,trade=fixture();a=advance(a,h.evaluate(a,trade()))
    o=trade(.01,10.44)
    a=advance(a,h.evaluate(a,replace(o,ask=11.)))
    assert h.evaluate(a,trade(.02,10.45)).evaluation.intents
    a=advance(a,h.evaluate(a,trade(.03,10.42)))
    assert 'initial_breakout' not in a.state['squeeze_breakout']


def test_recovery_is_price_triggered_and_uses_latest_band_without_five_ticks():
    h,a,trade=fixture();a=advance(a,h.evaluate(a,trade(0.,10.55)))
    o=trade(.01,10.6)
    anchor=dict(o.structural_resistance_levels[0],lower=10.5,upper=10.58)
    d=a.state['squeeze_breakout']
    d['recovery']=dict(high=10.59,stopped_at=o.observed_at.timestamp()-.005,
        breakout_at=o.observed_at.timestamp()-1,anchor=anchor)
    d['latest_broken_resistance']=anchor
    r=h.evaluate(a,o);i=r.evaluation.intents[0]
    assert i.reason=='stopout_close_high_reentry'
    assert i.invalidation_price==pytest.approx(10.49)
    assert i.metadata['breakout_boundary']['price']==10.59
    assert i.metadata['unified_structural_trigger']['current_snapshot']['levels'][0]['entry_boundary']==10.59


@pytest.mark.parametrize('certified',[False,True])
def test_quiet_interval_vwap_requires_causal_prefix_proof(certified):
    from datetime import timedelta
    h,a,trade=fixture();a=advance(a,h.evaluate(a,trade()))
    o=trade(.01,10.44)
    source=dict(value=9.,observed_at=(o.observed_at-timedelta(seconds=10)).isoformat())
    market=deepcopy(o.structural_detector_state)
    if certified:market['price_vwap_evidence']['source_observed_at']=source['observed_at']
    o=replace(o,structural_detector_state=market,
        source_values={**o.source_values,'indicator.vwap.execution_value@100ms':source})
    assert bool(h.evaluate(a,o).evaluation.intents)==certified


def test_actual_sugp_geometry_rejects_353_then_enters_356_with_349_stop():
    h,a,trade=fixture()
    def sugp(t,price):
        o=trade(t,price)
        prototype=o.structural_resistance_levels[0]
        rows=tuple(dict(prototype,unified_level_id=str(n),lower=lo,upper=hi,role='resistance')
            for n,(lo,hi) in enumerate([(3.4916564515,3.6063055302),(3.6160640124,3.6550229686),(3.75,3.79),(3.9,3.92)]))
        market=deepcopy(o.structural_detector_state);market['fast_squeeze_context']['hod']=3.55
        return replace(o,structural_resistance_levels=rows,structural_support_levels=(),structural_transition_levels=(),
            structural_detector_state=market,source_values={**o.source_values,
                'indicator.vwap.execution_value@100ms':dict(value=3.49,observed_at=o.observed_at.isoformat())})
    r=h.evaluate(a,sugp(0.,3.53));assert not r.evaluation.intents
    a=advance(a,r);i=h.evaluate(a,sugp(.01,3.56)).evaluation.intents[0]
    assert i.invalidation_price==3.49
    assert i.metadata['entry_selection']['unified_level_id']=='0'


@pytest.mark.parametrize('count,ordinal',[(0,3),(3,3),(4,2),(5,2),(6,1)])
def test_target_table_preserved_on_trade_prices(count,ordinal):
    h,a,trade=fixture();a=advance(a,h.evaluate(a,trade()))
    a=advance(a,h.evaluate(a,trade(.01,10.44)))
    a.state['squeeze_entry'].update(first_fill_at=trade().observed_at.timestamp(),slice_notional=3000.,
        broken_levels=[f'prior-{n}' for n in range(count)])
    a.state['structural_profit_targets']=[10.5]
    a=replace(a,status=S.AssignmentStatus.MANAGING)
    r=h.evaluate(a,trade(.02,10.45,100.))
    i=next(i for i in r.evaluation.intents if i.action=='replace_profit_target')
    assert i.metadata['profit_target_selection']['ordinal']==ordinal


def test_original_contracts_and_price_candidate_compile(monkeypatch):
    from src.backend import early_squeeze_price_candidate as C
    from src.backend.trading_configuration_service import configuration_base, _build_configuration_release
    from tests.test_early_squeeze_candidate import baseline
    base=configuration_base()
    base['strategy']['profiles']=[p for p in base['strategy']['profiles'] if p['profile_id']!=C.CONTRACT]
    monkeypatch.setattr('src.backend.trading_configuration_service.configuration_base',lambda:deepcopy(base))
    payload,canvas,plan=C.build(base,baseline(base))
    _build_configuration_release(canvas_revision=canvas['revision'],canvas_profile=canvas['profile'],
        configuration=payload,run_plan_id=plan,strategy_profile_id=C.CONTRACT)
