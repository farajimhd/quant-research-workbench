import copy
from dataclasses import asdict, replace
import numpy as np
import pytest
import torch
from research.rl_trading.v6.bias_panel import indexed_panel, normalization
from research.rl_trading.v6.features import SCALAR_NAMES, LEVEL_NAMES
from research.rl_trading.v6.market_attention import MarketAttentionConfig
from research.rl_trading.v6.ranked_normalization import fit_normalization
from research.rl_trading.v6.run_ranked_teacher_generalization import build_policy
from research.rl_trading.v6.training import train_session
from test_rl_trading_v6_teacher_forecast import fixture


def test_unique_prior_normalization_matches_existing_contract_and_ignores_future():
    _, session, labels = fixture()
    actual = fit_normalization(session, labels)
    expected = normalization(indexed_panel(session, labels))
    assert actual['observations'] == expected['observations']
    np.testing.assert_allclose(actual['mean'], expected['mean'], rtol=1e-7, atol=1e-7)
    np.testing.assert_allclose(actual['std'], expected['std'], rtol=1e-7, atol=1e-7)
    assert actual == fit_normalization(session, labels+labels)
    scalar=session.bank.scalar.copy(); scalar[session.bank.manifest['offsets']['A'][1]-1]+=100
    changed=replace(session, bank=replace(session.bank, scalar=scalar))
    assert actual == fit_normalization(changed, labels)
    for i,name in enumerate(SCALAR_NAMES):
        if name.endswith(('valid','present','available')) or name in ('premarket','regular','after_hours'):
            assert actual['mean'][i] == 0 and actual['std'][i] == 1
    for slot in range(10):
        i=37+slot*11+LEVEL_NAMES.index('present')
        assert actual['mean'][i] == 0 and actual['std'][i] == 1
    with pytest.raises(ValueError, match='TRAIN'): fit_normalization(replace(session,role='development'),labels)
    with pytest.raises(ValueError, match='TRAIN'): fit_normalization(replace(session,role='sealed_test'),labels)
    with pytest.raises(ValueError, match='TRAIN'): fit_normalization(session,())


def test_prior_session_tail_retains_original_sparse_history_contract():
    _,session,labels=fixture()
    previous=replace(session.bank,close_us=session.bank.close_us-20_000_000)
    session=replace(session,previous=previous)
    actual=fit_normalization(session,labels)
    expected=normalization(indexed_panel(session,labels))
    assert actual['observations']==expected['observations']
    np.testing.assert_allclose(actual['mean'],expected['mean'],rtol=1e-7,atol=1e-7)
    np.testing.assert_allclose(actual['std'],expected['std'],rtol=1e-7,atol=1e-7)


@pytest.mark.parametrize('device',['cpu']+(['cuda'] if torch.cuda.is_available() else []))
def test_real_normalized_ranked_path_trains_and_reloads_with_full_population(device):
    _, session, labels=fixture(device)
    labels=tuple(replace(d,forecast_actions=np.zeros(len(d.forecast_close_us),np.int64)) for d in labels)
    norm=fit_normalization(session,labels)
    ranking=MarketAttentionConfig(top_r=1,market_tokens=2,heads=2)
    policy=build_policy(ranking,torch.device(device),width=16,normalization=norm)
    before=copy.deepcopy(policy.state_dict())
    fit=train_session(policy,torch.optim.AdamW(policy.parameters(),lr=.001),session,labels,(),device=torch.device(device),
        teacher_loss='branch-balanced-v3',regression_weights=(0.,0.))
    assert fit.decisions==len(labels) and len(session.listings)==2
    assert any(not torch.equal(before[n],p) for n,p in policy.state_dict().items())
    metrics=asdict(train_session(policy,None,session,labels,(),device=torch.device(device),evaluation=True,evaluate_train=True))
    restored=build_policy(ranking,torch.device(device),width=16,normalization=norm)
    restored.load_state_dict(policy.state_dict(),strict=True)
    assert metrics==asdict(train_session(restored,None,session,labels,(),device=torch.device(device),evaluation=True,evaluate_train=True))
