import numpy as np
import pytest
from research.vectorized_backtest.v5.torch_backtest.splits import price_factor, history_view, rvol_view


def row(day, before, after):
    return dict(execution_date=day, split_from=before, split_to=after,inserted_at='2026-07-01 08:00:00.000000')


@pytest.mark.parametrize('before,after', [(1, 2), (10, 1)])
def test_share_basis_and_rvol(before, after):
    factor = price_factor([row('2026-08-03', before, after)], '2026-07-31', '2026-08-03')
    bank = np.zeros((3, 147), dtype=np.float32)
    bank[:, :4] = np.log(20)
    bank[:, 8] = np.log1p(100)
    bank[:, 10] = np.log1p(400)
    bank[:, 12] = np.log1p(3)
    bank[:, 13] = 1
    bank[:, 35:37] = 1
    bank[:, 14:20] = .25
    bank[:, 37:] = .5
    original = bank.copy()
    view = history_view(bank, factor)
    np.testing.assert_allclose(np.exp(view[:, :4]), 20 * factor, rtol=1e-6)
    np.testing.assert_allclose(np.expm1(view[:, 8]), 100 / factor, rtol=1e-6)
    np.testing.assert_allclose(np.expm1(view[:, 10]), 400 / factor, rtol=1e-6)
    np.testing.assert_array_equal(view[:, 14:], bank[:, 14:])
    current = rvol_view(bank, factor)
    np.testing.assert_allclose(np.expm1(current[:, 12]), 3 * factor, rtol=1e-6)
    np.testing.assert_array_equal(bank, original)


def test_effective_date_deduplication_and_compound_actions():
    rows = [row('2026-08-01', 1, 2), row('2026-08-01', 1, 2), row('2026-08-03', 5, 1), row('2026-08-04', 1, 100)]
    assert price_factor(rows, '2026-07-31', '2026-08-03') == 2.5
    assert price_factor(rows, '2026-08-03', '2026-08-04') == .01
    with pytest.raises(ValueError, match='Conflicting'):
        price_factor([row('2026-08-03', 1, 2), row('2026-08-03', 1, 3)], '2026-07-31', '2026-08-03')
    with pytest.raises(ValueError, match='Invalid'):
        price_factor([row('2026-08-03', 0, 2)], '2026-07-31', '2026-08-03')


@pytest.mark.parametrize('factor',[.5,10.])
def test_normalized_indicators_reconstruct_on_adjusted_price_basis(factor):
    # V6 stores MACD/close, ATR/close and EMA/close-1, rather than dollar values.
    close=20.
    raw=np.array([.8,.6,60.,1.2,21.,19.])
    encoded=np.array([raw[0]/close,raw[1]/close,raw[2]/100.,raw[3]/close,
                      raw[4]/close-1.,raw[5]/close-1.],dtype=np.float32)
    bank=np.zeros((1,147),dtype=np.float32)
    bank[0,:4]=np.log(close);bank[0,14:20]=encoded;bank[0,35:37]=1
    adjusted=history_view(bank,factor)
    adjusted_close=np.exp(adjusted[0,3])
    reconstructed=np.array([adjusted[0,14]*adjusted_close,adjusted[0,15]*adjusted_close,
                            adjusted[0,16]*100,adjusted[0,17]*adjusted_close,
                            (adjusted[0,18]+1)*adjusted_close,(adjusted[0,19]+1)*adjusted_close])
    expected=raw.copy();expected[[0,1,3,4,5]]*=factor
    np.testing.assert_allclose(reconstructed,expected,rtol=1e-6)


def test_invalid_prices_remain_invalid_and_no_action_is_identity():
    bank = np.zeros((2, 147), dtype=np.float32)
    np.testing.assert_array_equal(history_view(bank, 1), bank)
    changed = history_view(bank, .5)
    np.testing.assert_array_equal(changed[:, :4], bank[:, :4])
    np.testing.assert_array_equal(changed[:, 35:37], bank[:, 35:37])


def test_real_listing_loader_restates_context_without_touching_current_quotes():
    from research.vectorized_backtest.v5.torch_backtest.feature_bank import CertifiedBank
    def bank(price, volume, clock):
        value = CertifiedBank.__new__(CertifiedBank)
        value.manifest = dict(offsets={'stock': [0, 1]})
        value.clocks = np.array([clock], dtype=np.int64)
        value.scalar = np.zeros((1, 37), dtype=np.float32)
        value.scalar[:, :4] = np.log(price)
        value.scalar[:, 8] = np.log1p(volume)
        value.scalar[:, 12] = np.log1p(2)
        value.scalar[:, 13] = 1
        value.scalar[:, 35:37] = 1
        value.levels = np.zeros((1, 2, 5, 11), dtype=np.float32)
        return value
    old = bank(20, 100, 1_000_000)
    current = bank(10, 200, 10_000_000)
    old.split_basis = {'stock': dict(rvol_price_factor=1.)}
    current.split_basis = {'stock': dict(history_price_factor=.5, rvol_price_factor=.5)}
    clocks, features, valid = current.listing('stock', previous=old)
    np.testing.assert_array_equal(clocks.numpy(), [1_000_000, 10_000_000])
    np.testing.assert_allclose(features[:, 3].exp().numpy(), [10, 10], rtol=1e-6)
    np.testing.assert_allclose(features[:, 8].expm1().numpy(), [200, 200], rtol=1e-6)
    np.testing.assert_allclose(features[:, 12].expm1().numpy(), [2, 1], rtol=1e-6)
    np.testing.assert_allclose(np.exp(old.scalar[0, 3]), 20, rtol=1e-6)
    assert valid[:, 3].all()


def test_certificate_binding_and_arithmetic_are_enforced(tmp_path):
    import json
    from types import SimpleNamespace
    from research.rl_trading.v1.common import digest
    from research.vectorized_backtest.v5.torch_backtest.splits import VERSION, load_basis
    previous = SimpleNamespace(day={'day': '2026-07-31'}, certificate_hash='prior-bank')
    current = SimpleNamespace(day={'day': '2026-08-03', 'previous_day': '2026-07-31'}, certificate_hash='current-bank')
    value = dict(version=VERSION, status='complete', day='2026-08-03', opening_asof_utc='2026-08-03 08:00:00.000000000',bank_certificate_sha256='current-bank',
        previous_bank_certificate_sha256='prior-bank', listings={'stock': dict(ticker='ABC',
        splits=[row('2026-08-03', 1, 2)], history_price_factor=.5, rvol_price_factor=.5)})
    path = tmp_path / 'splits.json'
    def save():
        value['hash'] = digest({k: v for k, v in value.items() if k != 'hash'})
        path.write_text(json.dumps(value))
    save()
    basis, _ = load_basis(path, current, previous, {'stock': 'ABC'})
    assert basis['stock']['history_price_factor'] == .5
    assert basis['stock']['split_this_session'] and not basis['stock']['reverse_split_this_session']
    value['listings']['stock']['history_price_factor'] = 1.
    save()
    with pytest.raises(ValueError, match='arithmetic'):
        load_basis(path, current, previous, {'stock': 'ABC'})
    value['bank_certificate_sha256'] = 'wrong-bank'
    save()
    with pytest.raises(ValueError, match='current bank'):
        load_basis(path, current, previous, {'stock': 'ABC'})


@pytest.mark.parametrize('factor,split,reverse', [(1.,False,False),(.5,True,False),(10.,True,True)])
def test_searchable_session_flags_are_current_asof_and_vectorized(factor,split,reverse,tmp_path):
    import json
    from types import SimpleNamespace
    from research.rl_trading.v1.common import digest
    from research.vectorized_backtest.v5.torch_backtest.splits import VERSION, load_basis, append_session_flags
    from research.vectorized_backtest.v5.torch_backtest.feature_bank import CATALOG,validity
    from research.vectorized_backtest.v5.torch_backtest.program import Node,Program,Op,TorchPrograms
    import torch
    bank=SimpleNamespace(day={'day':'2026-08-03','previous_day':None},certificate_hash='bank')
    actions=[] if factor==1 else [row('2026-08-03',factor,1)]
    value=dict(version=VERSION,status='complete',day='2026-08-03',opening_asof_utc='2026-08-03 08:00:00.000000000',bank_certificate_sha256='bank',previous_bank_certificate_sha256=None,
        listings={'stock':dict(ticker='ABC',splits=actions,history_price_factor=1.,rvol_price_factor=1.)})
    value['hash']=digest(value);path=tmp_path/'actions.json';path.write_text(json.dumps(value))
    basis,_=load_basis(path,bank,None,{'stock':'ABC'})
    features=torch.from_numpy(append_session_flags(np.zeros((3,147),dtype=np.float32),basis['stock']))
    assert features.shape==(3,149)
    for index,expected in ((147,split),(148,reverse)):
        program=Program((Node(Op.FEATURE,feature=index),),0)
        output,known=TorchPrograms([program],CATALOG)(features,validity(features))
        assert known.all() and (output==int(expected)).all()
    for action in (row('2026-08-04',100,1),{**row('2026-08-03',1,2),'inserted_at':'2026-08-03 08:00:00.001'}):
        value['listings']['stock']['splits']=[action]
        value['hash']=digest({k:v for k,v in value.items() if k!='hash'});path.write_text(json.dumps(value))
        with pytest.raises(ValueError,match='known at session opening'):
            load_basis(path,bank,None,{'stock':'ABC'})
