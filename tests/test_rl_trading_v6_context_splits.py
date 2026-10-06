from datetime import date
import numpy as np
import pytest
from research.rl_trading.v6.context_splits import factors, SplitScalarView
from research.rl_trading.v6.features import SCALAR_NAMES


def test_split_revisions_compound_once_and_conflicts_fail_closed():
    def row(day, before, after):
        return dict(listing_id='A',execution_date=day,split_from=before,split_to=after)
    rows=[row('2026-08-23',1,2),row('2026-08-23',1,2),row('2026-08-24',10,1)]
    assert factors(rows,['A','B'],date(2026,8,21),date(2026,8,24)) == {'A':.2,'B':1.}
    with pytest.raises(ValueError,match='Conflicting'):
        factors(rows+[row('2026-08-24',5,1)],['A'],date(2026,8,21),date(2026,8,24))
    with pytest.raises(ValueError,match='interval'):
        factors([row('2026-08-25',1,2)],['A'],date(2026,8,21),date(2026,8,24))
    with pytest.raises(ValueError,match='quantities'):
        factors([row('2026-08-24',0,1)],['A'],date(2026,8,21),date(2026,8,24))
    late=row('2026-08-24',1,2);late['inserted_at']='2026-08-24 08:00:00.000000001'
    with pytest.raises(ValueError,match='unavailable'):
        factors([late],['A'],date(2026,8,21),date(2026,8,24))


@pytest.mark.parametrize('ratio',[2.,.1])
def test_context_price_volume_vwap_indicator_and_invalid_mask_parity(ratio):
    original=np.zeros((4,len(SCALAR_NAMES)),dtype=np.float32)
    original[:,:4]=np.log(100.)
    original[:,35:37]=1
    original[1,:4]=0;original[1,35:37]=0
    original[:,8]=np.log1p(1000.)
    original[:,10]=np.log1p(6000.)
    original[:,12]=np.log1p(2.)
    original[:,4:6]=[.01,.02]
    original[:,14:20]=[.003,.002,.7,.01,.005,.006]
    baseline=original.copy()
    offsets={'A':[0,2],'B':[2,4]}
    prior=SplitScalarView(original,offsets,{'A':ratio},prior=True)
    values=prior[:]
    assert np.exp(values[0,3]) == pytest.approx(100/ratio,rel=1e-6)
    assert np.expm1(values[0,8]) == pytest.approx(1000*ratio,rel=1e-6)
    assert np.expm1(values[0,10]) == pytest.approx(6000*ratio,rel=1e-6)
    np.testing.assert_array_equal(values[1,:4],0)
    np.testing.assert_array_equal(values[:,14:20],original[:,14:20])
    np.testing.assert_array_equal(values[:,4:8],original[:,4:8])
    np.testing.assert_array_equal(values[2:],original[2:])
    np.testing.assert_array_equal(original,baseline)
    np.testing.assert_array_equal(prior[[2,0]],values[[2,0]])
    np.testing.assert_array_equal(prior[0,:4],values[0,:4])
    assert not values.flags.writeable
    current=SplitScalarView(original,offsets,{'A':ratio})
    assert np.expm1(current[0,12]) == pytest.approx(2/ratio,rel=1e-6)
    np.testing.assert_array_equal(current[:,:4],original[:,:4])


def test_stream_warmup_uses_adjusted_bank_same_as_history():
    import torch
    from pathlib import Path
    from research.rl_trading.v6.bank import SessionBank
    from research.rl_trading.v6.candle_stream import SparseCandleState, seed_previous_session
    from research.rl_trading.v6.model import ActualCandleEncoder
    raw=np.zeros((3,37),dtype=np.float32);raw[:,:4]=np.log(100);raw[:,35:37]=1
    adjusted=SplitScalarView(raw,{'A':[0,3]},{'A':2.},prior=True)
    previous=SessionBank(Path('unused'),{'offsets':{'A':[0,3]}},np.arange(3,dtype=np.int64),adjusted,np.zeros((3,2,5,11),np.float32))
    encoder=ActualCandleEncoder()
    state=SparseCandleState.empty(encoder,1,device=torch.device('cpu'),dtype=torch.float32,refreshable=True)
    seed_previous_session(state,encoder,('A',),previous)
    np.testing.assert_array_equal(state.raw_history[0,-3:,:37].numpy(),previous.listing_tail('A').scalar)
    np.testing.assert_array_equal(raw[:,:4],np.full((3,4),np.log(100),dtype=np.float32))


def test_v7_relative_slots_not_adjusted_twice_in_chart():
    from pathlib import Path
    from research.rl_trading.v6.bank import SessionBank
    from research.rl_trading.v6.model_candle_audit import select_window, project
    scalar=np.zeros((1,37),np.float32);scalar[:,:4]=np.log(100);scalar[:,35:37]=1
    levels=np.zeros((1,2,5,11),np.float32)
    levels[0,1,0,:3]=[.1,.09,.11];levels[0,1,0,10]=1
    old=SessionBank(Path('unused'),{'offsets':{'A':[0,1]}},np.array([1_000_000],np.int64),
        SplitScalarView(scalar,{'A':[0,1]},{'A':2.},prior=True),levels)
    now_scalar=scalar.copy();now_scalar[:,:4]=np.log(50)
    now=SessionBank(Path('unused'),{'offsets':{'A':[0,1]}},np.array([2_000_000],np.int64),now_scalar,levels.copy())
    raw,_=select_window(now.listing('A'),old.listing('A'))
    _,lines,_=project(raw)
    center=next(x for x in lines if x['column']=='v7_1_0_center')
    assert [x['value'] for x in center['data']] == pytest.approx([55.,55.],rel=1e-6)
    np.testing.assert_array_equal(old.listing('A').levels,levels)


def test_cached_receipt_rechecks_binding_hash_and_never_queries(tmp_path,monkeypatch):
    import json
    from research.rl_trading.v6.context_splits import receipt,VERSION
    from research.rl_trading.v1.common import digest
    from research.rl_trading.v1 import arte_source
    from research.rl_trading.v1.reference_features import opening
    plan=dict(hash='plan',day='2026-08-24',previous_day='2026-08-21',census={'A':1})
    binding=dict(version=VERSION,plan_hash='plan',day='2026-08-24',previous_day='2026-08-21',cutoff=opening(date(2026,8,24)),identities=['A'])
    saved=dict(binding=binding,rows=[]);saved['hash']=digest(saved)
    root=tmp_path/'rl-v6-context-splits'/VERSION;root.mkdir(parents=True)
    path=root/'plan.json';path.write_text(json.dumps(saved))
    monkeypatch.setattr(arte_source,'reader',lambda **_:pytest.fail('Cached receipt queried metadata again'))
    from research.rl_trading.v1.common import exclusive
    with exclusive(path.with_suffix('.lock')):
        assert receipt(tmp_path,plan)==({'A':1.},saved['hash'])
    saved['rows']=[dict(listing_id='A',execution_date='2026-08-24',split_from=1,split_to=2)]
    path.write_text(json.dumps(saved))
    with pytest.raises(ValueError,match='binding/hash'):
        receipt(tmp_path,plan)
