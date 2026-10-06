import numpy as np
import pytest
import torch

from research.rl_trading.v6.bias_models import balance_weights,LocalWindowTeacher,current_objective
from research.rl_trading.v6.temporal_encoders import TemporalCandleEncoder
from research.rl_trading.v6.bias_metrics import action_report,calibration_thresholds
from research.rl_trading.v6.candle_stream import SparseCandleState
from research.rl_trading.v6.bias_panel import normalization
from research.rl_trading.v6.run_bias_campaign import reserve_calibration
from research.rl_trading.v6.bias_metrics import fit_probability_calibration,calibrated_probability


def test_branch_balancing_equalizes_conditional_class_mass():
    actions=np.array([0]+[1]*20+[2]*8+[3]*2);weights=np.linspace(.1,1,len(actions))
    balanced=balance_weights(actions,weights)
    mass=np.bincount(actions,weights=weights*balanced[actions],minlength=4)
    np.testing.assert_allclose(mass,np.full(4,weights.sum()/4),rtol=1e-6)
    with pytest.raises(ValueError):balance_weights(actions,np.zeros(len(actions)))
    with pytest.raises(ValueError):balance_weights(np.array([4]),np.ones(1))


def test_main_trainer_branch_balance_maps_identity_action_axes():
    from types import SimpleNamespace
    from research.rl_trading.v6.training import teacher_loss_balance
    # Two listings: WAIT=0, ENTRY=1, held EXIT=3, held HOLD=6.
    records=[SimpleNamespace(token=t,held_index=np.array([0]) if t in (3,6) else np.array([],int),sample_weight=1.) for t in [0]*20+[1]*2+[3]+[6]*8]
    weights,denominator=teacher_loss_balance(records,2,np.array([1_000_000,32_000_000]),32,wait_hold=True,mode='branch-balanced-v3')
    np.testing.assert_allclose(weights[[0,1,2,5]]*np.array([20,2,1,8]),np.full(4,31/4),rtol=1e-6)
    assert denominator==31 and not weights[3:5].any()


def test_candle_normalization_adds_no_execution_observations_and_is_single_authority():
    from research.rl_trading.v6.ranked_policy import RankedBracketActorCritic
    policy=RankedBracketActorCritic(width=8,wait_hold=True)
    norm=dict(mean=[0.]*147,std=[1.]*147,scope='train_only',units='bps-v1')
    policy.configure_candle_features(norm)
    assert not hasattr(policy,'execution_projection')
    with pytest.raises(ValueError):policy.configure_candle_features(norm)
    assert policy.encoder.feature_mean.shape==(147,)


def test_sparse_calibration_does_not_tune_thresholds():
    actions=np.array([0,1,2,3]);p=np.array([.2,.1,.1,.2])
    assert calibration_thresholds(actions,p)==(.5,.5)
    report=action_report(actions,p)
    assert report['ENTRY']['average_precision']==1 and report['ENTRY']['f1']==0


def test_probability_calibration_uses_natural_prior_and_preserves_ranking():
    actions=np.array([0]*20+[1]*180+[3]*20+[2]*180)
    probability=np.tile(np.linspace(.6,.5,200),2)
    parameters=fit_probability_calibration(actions,probability)
    adjusted=calibrated_probability(actions,probability,parameters)
    assert adjusted[:200].mean()==pytest.approx(.1,abs=.01)
    assert np.diff(adjusted[:200]).max()<=0
    assert all(p['scale']>=0 for p in parameters)


def test_normalization_never_reads_reserved_candles_and_preserves_presence():
    features=np.zeros((4,147),np.float32);features[1:3]=1;features[3]=100000
    windows=np.zeros((2,120),np.int32);windows[:,-1]=[1,2]
    norm=normalization(dict(features=features,windows=windows))
    assert norm['mean'][0]==1 and norm['observations']==2
    assert norm['mean'][47]==0 and norm['std'][47]==1


def test_calibration_reservation_keeps_development_untouched():
    def sample(clocks):
        n=len(clocks)
        return dict(features=np.zeros((2,147),np.float32),windows=np.ones((n,120),np.int32),clock=np.array(clocks),episode=['x']*n,action=np.zeros(n,int))
    original=dict(train=sample([1,2,10,11]),calibration=sample([20]),development=sample([30]))
    panel=reserve_calibration(original,dict(sessions={'2026-08-04':{'begin_us':10}}))
    assert panel['train']['clock'].tolist()==[1,2]
    assert panel['calibration']['clock'].tolist()==[10,11,20]
    assert panel['development'] is original['development']


def test_stationary_temporal_branch_is_price_scale_invariant():
    norm=dict(mean=[0.]*147,std=[1.]*147,scope='train_only')
    model=LocalWindowTeacher('lag',width=8,stationary=True,normalization=norm)
    with torch.no_grad():model.price_anchor.weight.zero_()
    windows=torch.randn(2,120,147);present=torch.ones(2,120,dtype=torch.bool)
    market=torch.randn(2,147);held=torch.zeros(2,11);held[1,:2]=torch.tensor([1.,10.])
    expected=model(windows,present,market,held)['logit']
    changed=windows.clone();changed[...,3]+=np.log(2)
    held2=held.clone();held2[:,1]*=2
    actual=model(changed,present,market,held2)['logit']
    torch.testing.assert_close(expected,actual,atol=1e-5,rtol=1e-5)


def test_real_bps_adapter_has_only_one_absolute_price_channel():
    from research.rl_trading.v6.execution_features import bps_input
    from research.rl_trading.v6.features import SCALAR_NAMES
    scalar=torch.zeros(2,37);scalar[:,:4]=torch.tensor([9.,11.,8.,10.]).log()
    scalar[:,SCALAR_NAMES.index('bar_price_valid')]=1
    levels=torch.zeros(2,2,5,11)
    changed=scalar.clone();changed[:,:4]+=np.log(2)
    original=bps_input(scalar,levels);doubled=bps_input(changed,levels)
    torch.testing.assert_close(original[:,:3],doubled[:,:3],atol=.01,rtol=1e-5)
    torch.testing.assert_close(doubled[:,3]-original[:,3],torch.full((2,),np.log(2)),atol=1e-6,rtol=1e-6)


def test_stationary_anchor_ignores_unpriced_tail_and_padding():
    norm=dict(mean=[0.]*147,std=[1.]*147,scope='train_only')
    model=LocalWindowTeacher('lag',width=8,stationary=True,normalization=norm)
    captured=[]
    hook=model.encoder.project.register_forward_pre_hook(lambda module,args:captured.append(args[0].detach().clone()))
    windows=torch.zeros(1,120,147);present=torch.ones(1,120,dtype=torch.bool)
    windows[0,-3,3]=np.log(10);windows[0,-3,35]=1
    windows[0,-2:,3]=123  # invalid sentinel must not become a price anchor
    model(windows,present,torch.zeros(1,147),torch.zeros(1,11))
    assert captured[0][0,-3,3]==0 and not captured[0][0,-2:,3].any()
    hook.remove()


def test_valid_price_history_retains_120_prices_elapsed_gaps_and_causal_fence():
    from dataclasses import replace
    from test_rl_trading_v6_teacher_forecast import fixture
    from research.rl_trading.v6.bias_panel import indexed_panel
    from research.rl_trading.v6.probe_teacher_sequence import subset
    from research.rl_trading.v6.bank import SessionBank
    from pathlib import Path
    _,session,decisions=fixture(count=300)
    session.bank.scalar[:,35]=np.tile(np.arange(300)%2==0,2)
    session.bank.scalar[:,3]=np.log(10)
    previous=SessionBank(Path('unused'),session.bank.manifest,session.bank.close_us-400_000_000,session.bank.scalar.copy(),session.bank.levels.copy())
    session=replace(session,previous=previous)
    packed,labels=subset(session,decisions,('A',),1_000_000,300_000_000,valid_price_context=True)
    assert (packed.previous.listing('A').scalar[:,35]>.5).sum()==121
    panel=indexed_panel(packed,labels,valid_price_history=True)
    assert (panel['features'][panel['windows']][:,:,35]==1).all()
    assert (panel['max_input_clock']<panel['clock']).all()
    assert panel['windows'].shape==(300,120)
    # First current price comes after the overnight elapsed gap, not one bar.
    first_current=int(np.flatnonzero(panel['features'][:,3]>0)[121])
    assert np.expm1(panel['features'][first_current,26])==pytest.approx(102.,rel=1e-5)


def test_training_normalization_includes_adjusted_context_and_rejects_future_context():
    from dataclasses import replace
    from test_rl_trading_v6_teacher_forecast import fixture
    from research.rl_trading.v6.feature_normalization import fit_normalization
    from research.rl_trading.v6.bank import SessionBank
    from research.rl_trading.v6.execution_features import CANDLE_NORMALIZATION_VERSION
    from pathlib import Path
    _,session,_=fixture(count=3)
    previous=SessionBank(Path('unused'),session.bank.manifest,session.bank.close_us-10_000_000,session.bank.scalar.copy(),session.bank.levels.copy())
    session=replace(session,previous=previous,context_split_receipt_sha256='split-receipt')
    proof=fit_normalization([session],dataset_sha256='dataset',include_context=True)
    assert proof['version']==CANDLE_NORMALIZATION_VERSION
    assert proof['current_observations']==6 and proof['context_observations']==6
    assert proof['context_split_receipts']=={'2026-07-31':'split-receipt'}
    assert proof['mean'][35]==0 and proof['std'][35]==1
    with pytest.raises(ValueError,match='fence'):
        fit_normalization([replace(session,previous=session.bank)],dataset_sha256='dataset',include_context=True)
    with pytest.raises(ValueError,match='development'):
        fit_normalization([replace(session,role='development')],dataset_sha256='dataset',include_context=True)


@pytest.mark.parametrize('architecture',['lag','tcn','gru','transformer','mlp'])
def test_real_ranked_training_core_accepts_encoder_and_separate_heads(architecture):
    from dataclasses import replace
    from test_rl_trading_v6_teacher_forecast import fixture
    from research.rl_trading.v6.teacher_forecast import configure
    from research.rl_trading.v6.temporal_encoders import replace_encoder
    from research.rl_trading.v6.training import train_session
    policy,session,labels=fixture(count=4)
    replace_encoder(policy,architecture,structured=True)
    configure(policy,hierarchical=True,shared_heads=False)
    assert policy.teacher_forecast.entry is not policy.decoder.heads.entry
    actions=np.array([0,1,0,1],np.int64)
    probabilities=np.array([[.8,.2,0,0],[.1,.9,0,0]]*2,np.float32)
    labels=tuple(replace(d,token=1 if actions[i]==0 else 0,allocation_ratio_target=.25 if actions[i]==0 else None,
        soft_probabilities=(.2,.8) if actions[i]==0 else (.9,.1),forecast_actions=actions[i:i+5],forecast_probabilities=probabilities[i:i+5]) for i,d in enumerate(labels))
    optimizer=torch.optim.Adam(policy.parameters(),lr=.001)
    policy.configure_candle_features(dict(mean=[0.]*147,std=[1.]*147,scope='train_only',units='bps-v1'))
    result=train_session(policy,optimizer,session,labels,(),device=torch.device('cpu'),clocks_per_chunk=2,teacher_loss='branch-balanced-v3')
    assert result.optimizer_steps==2 and np.isfinite(result.mean_loss)


@pytest.mark.parametrize('architecture',['lag','mlp','tcn','gru','transformer'])
def test_temporal_encoder_streaming_sparse_gradient_and_causal_parity(architecture):
    torch.set_num_threads(2);torch.manual_seed(13)
    encoder=TemporalCandleEncoder(8,architecture=architecture,structured=False)
    scalar=torch.randn(4,37);levels=torch.randn(4,2,5,11)
    expected=encoder.encode_listing(scalar,levels)
    altered=scalar.clone();altered[-1]+=100
    torch.testing.assert_close(expected[:-1],encoder.encode_listing(altered,levels)[:-1])
    state=encoder.initial_state(1,device=torch.device('cpu'),dtype=torch.float32)
    sparse=SparseCandleState.empty(encoder,1,device=torch.device('cpu'),dtype=torch.float32,refreshable=True)
    for i in range(4):
        encoder.observe(state,torch.tensor([0]),scalar[i:i+1],levels[i:i+1])
        sparse.advance(encoder,torch.tensor([0]),scalar[i:i+1],levels[i:i+1])
        torch.testing.assert_close(state.encoded[0],expected[i],atol=2e-5,rtol=2e-5)
        torch.testing.assert_close(sparse.embeddings()[0],expected[i],atol=2e-5,rtol=2e-5)
    sparse.embeddings().square().sum().backward()
    assert encoder.project.weight.grad is not None and torch.isfinite(encoder.project.weight.grad).all()
    empty=encoder.encode_history(torch.randn(2,120,8),torch.zeros(2,120,dtype=torch.bool))
    assert torch.isfinite(empty).all() and not empty.any()


@pytest.mark.parametrize('device',['cpu']+(['cuda'] if torch.cuda.is_available() else []))
@pytest.mark.parametrize('shared',[False,True])
def test_current_and_autoregressive_auxiliary_heads_backpropagate(device,shared):
    torch.set_num_threads(2);torch.manual_seed(17)
    model=LocalWindowTeacher('tcn',width=8,structured=True,shared_forecast=shared).to(device)
    windows=torch.randn(4,120,147,device=device)
    windows[...,47::11]=1
    present=torch.ones(4,120,dtype=torch.bool,device=device)
    held=torch.zeros(4,11,device=device);held[2:,0]=1
    result=model(windows,present,torch.randn(4,147,device=device),held,future_steps=5)
    actions=torch.arange(4,device=device)
    loss=current_objective(result['logit'],actions,torch.ones(4,device=device),torch.ones(4,device=device)).mean()
    loss+=-result['future'][:,:,0].mean()
    assert torch.isfinite(loss)
    loss.backward()
    for head in (model.heads.entry,model.heads.exit,model.forecast_gru):
        assert any(p.grad is not None and p.grad.abs().sum()>0 for p in head.parameters())
        assert all(torch.isfinite(p.grad).all() for p in head.parameters() if p.grad is not None)
