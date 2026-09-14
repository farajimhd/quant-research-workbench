from copy import deepcopy
import json
import pickle

import pytest

from src.market_engine.immutable_evidence import freeze


def test_freezing_preserves_json_pickle_and_old_publications():
    original=[dict(id=1,fit=dict(center=2.3,values=[1,2]))]
    sealed=freeze(original)
    original[0]['fit']['values'].append(3)
    assert sealed[0]['fit']['values']==[1,2]
    assert deepcopy(sealed) is sealed
    assert json.loads(json.dumps(sealed))==sealed
    restored=pickle.loads(pickle.dumps(sealed))
    assert restored==sealed
    with pytest.raises(TypeError):restored[0]['fit']['values'].append(4)
    with pytest.raises(TypeError):sealed[0].update(id=4)
    with pytest.raises(TypeError):sealed += [dict(id=2)]


def test_historical_selection_remains_equal_with_sealed_input():
    from types import SimpleNamespace
    from src.trading_runtime.historical_hod import selected_levels
    row=dict(unified_level_id='x',book_version='causal-level-book-v7-mle-1',role='resistance',side=-1,
        lifecycle='active',lower=2.,price=2.1,upper=2.2,confirmed_at_ms=2000.,
        oldest_member_confirmed_at_ms=1000.,fit={'center':2.1})
    settings=dict(v7_zone_enabled=True,v7_encounters_enabled=True)
    def observation(levels):
        return SimpleNamespace(structural_support_levels=(),structural_resistance_levels=levels,structural_transition_levels=())
    ordinary=selected_levels(observation([row]),settings,3)
    sealed=selected_levels(observation(freeze([row])),settings,3)
    assert ordinary==sealed
    assert deepcopy(sealed) is sealed
    assert selected_levels(observation(sealed),settings,1)==[]


def test_compact_detector_checkpoint_preserves_exact_continuation():
    from src.market_engine.structural_detector import StructuralDetector
    from src.market_engine.structural_detector_checkpoint import checkpoint,restore
    from tests.test_structural_detector import candle,level
    detector=StructuralDetector()
    levels=freeze([dict(level(),fit={'center':10.2})])
    for i in range(20):
        detector.observe(candle(i,10+i*.01,10.01+i*.01),levels,'available')
    ordinary=checkpoint(detector)
    compact=checkpoint(detector,compact=True)
    assert compact['format_version']==3
    resumed=restore(json.loads(json.dumps(compact)))
    reference=restore(json.loads(json.dumps(ordinary)))
    assert checkpoint(resumed)==checkpoint(reference)
    for i in range(20,40):
        bar=candle(i,10+i*.01,10.01+i*.01)
        assert resumed.observe(bar,levels,'available')==reference.observe(bar,levels,'available')


def test_native_checkpoint_preserves_special_containers_and_legacy_format():
    from collections import Counter,deque
    from src.market_engine.structural_detector_checkpoint import encode_json,decode_json,JSON_TYPE,encode,restore
    from src.market_engine.structural_detector import StructuralDetector,VERSION
    value={JSON_TYPE:'literal',1:Counter({'x':2}),'deque':deque([1,2],maxlen=4),'tuple':(1,2),'set':{1,2},'infinity':float('-inf')}
    assert decode_json(json.loads(json.dumps(encode_json(value))))==value
    old={'contract':VERSION,'format_version':2,'state':encode(StructuralDetector(),compact=True)}
    assert type(restore(json.loads(json.dumps(old)))) is StructuralDetector


def test_derived_projection_reuses_values_without_entering_checkpoint():
    from src.market_engine.structural_evidence import level_evidence,band_key
    row=freeze(dict(unified_level_id='x',side=-1,lower=2.,upper=3.,price=2.5))
    before=json.dumps(row)
    assert level_evidence(row) is level_evidence(row)
    assert band_key(row)==band_key(dict(row))
    assert json.dumps(row)==before
    assert not hasattr(pickle.loads(pickle.dumps(row)),'_derived')


def test_strategy_projection_retains_unchanged_rows_when_neighbor_changes():
    from src.trading_runtime.structure_level_contract import strategy_snapshot
    from datetime import datetime,timezone
    row=freeze(dict(unified_level_id='a',book_version='causal-level-book-v7-mle-1',lifecycle='active',
        side=-1,lower=2.,upper=3.,price=2.5,confirmed_at_ms=1000,fit={'status':'estimated'}))
    at=datetime.fromtimestamp(2,timezone.utc)
    def snapshot(rows):return {'book_version':'causal-level-book-v7-mle-1','unified_levels':rows}
    first=strategy_snapshot(snapshot([row]),at)
    changed=strategy_snapshot(snapshot([row,freeze(dict(row,unified_level_id='b'))]),at)
    assert first['unified_levels'][0] is changed['unified_levels'][0]
    assert first==strategy_snapshot(snapshot([dict(row)]),at)
    assert strategy_snapshot(snapshot([row]),datetime.fromtimestamp(0,timezone.utc))['unified_levels']==[]


def test_checkpoint_shares_equal_parameters_without_freezing_strategy():
    from types import SimpleNamespace
    from dataclasses import replace
    from tests.test_long_momentum_strategy import assignment
    from src.backend.replay_run_service import ReplayRunController
    first=assignment();second=replace(first,assignment_id='other',parameters=deepcopy(first.parameters))
    controller=SimpleNamespace(_strategy=SimpleNamespace(assignments=lambda:(first,second)),_prepared_v7=object())
    rows=ReplayRunController._checkpoint_assignments(controller)
    assert rows==[first.payload(),second.payload()]
    assert rows[0]['parameters'] is rows[1]['parameters']
    assert rows[0]['parameters'] is not first.parameters


def test_quiet_band_fast_path_matches_every_event_and_track():
    import random
    from src.market_engine.structural_evidence import Interactions
    rng=random.Random(273)
    ordinary=Interactions(15);fast=Interactions(15);previous=None
    rows=[dict(unified_level_id=str(i),side=1 if i%2 else -1,lower=1+i*.25,upper=1.1+i*.25,
        price=1.05+i*.25,confirmed_at_ms=1000) for i in range(80)]
    for i in range(500):
        close=max(.5,(previous or 10)+rng.uniform(-1.2,1.2))
        opened=max(.4,close+rng.uniform(-.5,.5))
        bar=dict(time=i+2,end=i+3,open=opened,close=close,low=max(.1,min(opened,close)-rng.random()),
            high=max(opened,close)+rng.random())
        current=[r for index,r in enumerate(rows) if (index+i//20)%9]
        q=None if i%11==0 else dict(atr=.3,ready=i%7!=0,penetration_atr=.1,price_floor=.001,
            body_atr=.3,body_fraction=.4,acceptance_closes=2)
        assert ordinary.observe(bar,previous,current,.2,i,q)==fast.observe(bar,previous,freeze(current),.2,i,q)
        assert ordinary.tracks==fast.tracks
        previous=close


def test_evidence_dataclass_payload_matches_asdict_without_expanding_sealed_rows():
    from dataclasses import asdict,dataclass
    from datetime import datetime,timezone
    from src.market_engine.immutable_evidence import evidence_payload
    @dataclass
    class Packet:
        at: datetime
        rows: list
        mutable: dict
    packet=Packet(datetime.now(timezone.utc),freeze([{'fit':{'center':2.1}}]),{'x':[1]})
    actual=evidence_payload(packet)
    assert actual==asdict(packet)
    assert actual['rows'] is packet.rows
    packet.mutable['x'].append(2)
    assert actual['mutable']=={'x':[1]}
