import numpy as np
import polars as pl
import pytest
import torch
from research.rl_trading.v6.execution_features import bps_input,execution_estimates,net_execution_bps,VERSION
from research.rl_trading.v6.execution_sidecar import window_estimates,score_candidates
from research.rl_trading.v6.features import SCALAR_NAMES
from research.rl_trading.v6.broker_shards import SCHEMA


def test_bps_geometry_units_gradients_and_no_future_dependence():
    scalar=torch.zeros(2,37);scalar[:,35]=1
    scalar[:,3]=torch.log(torch.tensor([10.,100.]))
    scalar[:,0]=torch.log(torch.tensor([10.1,101.]))
    scalar[:,14]=.002;scalar[:,16]=.65
    levels=torch.zeros(2,2,5,11);levels[:,:,:,0]=.01
    scalar.requires_grad_();levels.requires_grad_()
    result=bps_input(scalar,levels)
    assert result[:,0].tolist()==pytest.approx([100,100],abs=.004)
    assert result[:,14].tolist()==pytest.approx([20,20])
    assert result[:,16].tolist()==pytest.approx([.65,.65])
    assert float(result[0,37].detach())==pytest.approx(100)
    changed=scalar.detach().clone();changed[1]=800
    assert torch.equal(result[0],bps_input(changed,levels.detach())[0])
    result.sum().backward();assert scalar.grad.abs().sum()>0 and levels.grad.abs().sum()>0


def test_size_conditioned_fee_minimum_capacity_and_unknown_masks():
    x=lambda v:torch.tensor(v,dtype=torch.float64)
    result=execution_estimates(x([10,10]),x([9.99,9.99]),x([10.01,10.01]),x([10,10]),x([1000,1000]),
        torch.tensor([True,False]),torch.tensor([True,True]),x([.1,.1]))
    assert result[0,0]==pytest.approx(20)
    assert result[0,5]>result[0,7]
    assert result[0,8]>result[0,10]  # Minimum commission matters for small orders.
    assert result[1,3]==0 and result[1,0]==0 and result[1,5:11].sum()==0


def test_net_bps_penalizes_unfillable_profit_and_preserves_unknown_vs_zero():
    x=lambda v:torch.tensor(v,dtype=torch.float64)
    net=net_execution_bps(x([1,10,10,10]),x([1.05,10.5,10.5,10.5]),x([10000,10000,0,10000]),
        x([10000,10000,0,10000]),torch.tensor([True,True,True,False]))
    assert net[0]<net[1]  # Same gross bps: penny fees cost more, not less.
    assert net[2]==0 and torch.isnan(net[3])
    partial=net_execution_bps(x([10]),x([10.5]),x([1]),x([1]),torch.tensor([True]))
    assert partial[0]<net[1]


def rows():
    records=[]
    for i in range(20):
        records.append(dict(ticker='X',bucket_index=i,execution_volume=100.,execution_notional=1000.,
            volume_valid=1,high_int=100100,low_int=99900,extremes_valid=1,
            quote_timestamp_us=(i+1)*100000-1000,bid_int=99900,ask_int=100100,quote_valid=1))
    return pl.DataFrame(records,schema=SCHEMA)


def test_window_features_causal_future_prices_only_in_score():
    req=pl.DataFrame({'ticker':['X'],'time_us':[1000000],'reference':[10.]})
    source=rows();past=window_estimates(req,source,0)
    changed=source.with_columns(pl.when(pl.col('bucket_index')>=10).then(pl.col('execution_notional')*2)
        .otherwise(pl.col('execution_notional')).alias('execution_notional'))
    assert past.equals(window_estimates(req,changed,0))
    assert not window_estimates(req,source,0,future=True).equals(window_estimates(req,changed,0,future=True))
    assert past['cost_available'][0]
    missing=window_estimates(req,source.filter(pl.col('bucket_index')!=0),0)
    assert not missing['cost_available'][0]
    future=source.with_columns(pl.when(pl.col('bucket_index')==0).then(200000).otherwise(pl.col('quote_timestamp_us')).alias('quote_timestamp_us'))
    with pytest.raises(ValueError,match='Future quote'):window_estimates(req,future,0)


def test_per_bucket_participation_rounding_matches_broker():
    req=pl.DataFrame({'ticker':['X'],'time_us':[1000000],'reference':[10.]})
    source=rows().with_columns(pl.lit(1.).alias('execution_volume'),pl.lit(10.).alias('execution_notional'))
    result=window_estimates(req,source,0)
    assert result['capacity'][0]==0  # Ten one-share bars cannot become one fill.


def test_policy_new_contract_checkpoint_gradients_and_batch_decoder():
    from research.rl_trading.v6.ranked_policy import RankedBracketActorCritic
    from research.rl_trading.v6.model import INPUT_WIDTH
    from research.rl_trading.v6.candle_stream import SparseCandleState
    p=RankedBracketActorCritic(width=8,wait_hold=True)
    p.configure_execution_features(dict(version=VERSION,scope='train_only',mean=[0.]*INPUT_WIDTH,std=[1.]*INPUT_WIDTH))
    p.reset_market(2)
    state=SparseCandleState.empty(p.encoder,2,device=torch.device('cpu'),dtype=torch.float32)
    scalar=torch.zeros(2,37);scalar[:,35]=1;scalar[:,3]=2.;scalar[:,14]=.002
    levels=torch.zeros(2,2,5,11)
    state.advance(p.encoder,torch.arange(2),scalar,levels)
    p.observe_market(state,1000000,np.arange(2),scalar.numpy())
    features=torch.zeros(2,11);features[:,3:5]=1;features[:,0]=20
    p.set_execution_features(torch.arange(2),features)
    masks=dict(enter_allowed=torch.ones(2,dtype=torch.bool),exit_allowed=torch.empty(0,dtype=torch.bool),
        stop_allowed=torch.empty(0,dtype=torch.bool),target_allowed=torch.empty(0,dtype=torch.bool))
    account=torch.tensor([10000.,10000.,0,0,0,0,0]);held=torch.empty(0,dtype=torch.long);hf=torch.empty(0,11)
    memory=p.initial_action_state(device=torch.device('cpu'),dtype=torch.float32)
    logits,*_=p.decide(state.embeddings(),account,held,hf,memory,**masks)
    (-logits.log_softmax(0)[1]).backward()
    assert p.execution_projection.weight.grad.abs().sum()>0
    assert 'encoder.feature_mean' in p.state_dict()
    with pytest.raises(RuntimeError):RankedBracketActorCritic(width=8,wait_hold=True).load_state_dict(p.state_dict())



def test_capacity_excludes_buckets_without_executable_quotes():
    source=rows().with_columns(pl.when(pl.col('bucket_index')<9).then(0).otherwise(pl.col('quote_valid')).alias('quote_valid'))
    result=window_estimates(pl.DataFrame({'ticker':['X'],'time_us':[1000000],'reference':[10.]}),source,0)
    assert result['cost_available'][0] and result['capacity'][0]==10
    assert result['fill_fraction_1000'][0]<.2


def test_normalization_training_authority_and_mask_units():
    from types import SimpleNamespace
    from research.rl_trading.v6.feature_normalization import fit_normalization
    scalar=np.zeros((2,37),np.float32);scalar[:,35]=1;scalar[:,3]=np.log(10);scalar[:,0]=np.log(10.1)
    scalar[:,14]=[.001,.003]
    session=SimpleNamespace(role='train',day='fixture',source_certificate_sha256='cert',
        bank=SimpleNamespace(scalar=scalar,levels=np.zeros((2,2,5,11),np.float32)))
    proof=fit_normalization([session],dataset_sha256='dataset')
    assert proof['mean'][14]==pytest.approx(20) and proof['std'][14]==pytest.approx(10)
    assert proof['mean'][35]==0 and proof['std'][35]==1
    session.role='dev'
    with pytest.raises(ValueError,match='development'):fit_normalization([session],dataset_sha256='dataset')


def test_vectorized_affordability_matches_canonical_fees():
    from research.rl_trading.v6.execution_features import affordable_buy_quantity
    from research.rl_trading.v6.tensor_broker import order_fee
    price=torch.logspace(-3,4,1000,dtype=torch.float64)[:,None]
    budget=torch.tensor([100.,1000.,10000.],dtype=torch.float64)[None,:]
    q=affordable_buy_quantity(price,budget)
    assert ((q*price+order_fee(q,q*price,False))<=budget).all()
    after=(q+1)*price+order_fee(q+1,(q+1)*price,False)
    assert (after>budget).all()


def test_sparse_sidecar_retains_old_score_and_no_future_features(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from research.rl_trading.v6 import execution_sidecar as module
    source=SimpleNamespace(origin=0,day='fixture',source={'build_id':'build','definition_hash':'definition'},
        attempts={'X':'attempt'},luld=SimpleNamespace(rows={},end_us=None),luld_certificate='modeled-fixture')
    candidates=pl.DataFrame(dict(ticker=['X'],listing_id=['X-id'],episode_uid=['episode'],direction=[1],
        time_us=[1000000],decision_close=[10.],exit_hint_us=[1000000],exit_hint_close=[10.],score=[.02]))
    requests=pl.DataFrame({'ticker':['X'],'time_us':[1000000],'reference':[10.]})
    monkeypatch.setattr('research.rl_trading.v6.sparse_coverage.certify_source',lambda source,tickers:({t:(0,4000000) for t in tickers},'fixture'))
    monkeypatch.setattr(module,'read_requested_windows',lambda source,requests:rows())
    proof=module.build_cost_sidecar(source,candidates,requests,tmp_path/'sidecar',bank_sha='bank')
    scores=pl.read_parquet(tmp_path/'sidecar'/'scores.parquet')
    assert scores['old_score'][0]==.02 and scores['old_score_bps'][0]==200
    assert scores['netbps'][0]<0 and proof['missing_cost_rows']==0
    features=pl.read_parquet(tmp_path/'sidecar'/'features.parquet')
    assert 'netbps' not in features.columns and 'entry_price' not in features.columns
    assert pl.read_parquet(tmp_path/'sidecar'/'allocation_netbps.parquet').is_empty()


def test_exposing_cost_availability_preserves_existing_action_masks():
    from research.rl_trading.v6.tensor_broker import TensorBroker
    broker=TensorBroker(1,device='cpu')
    broker.update_marks(torch.tensor([0]),torch.tensor([10.]),1000000)
    original=broker.observe(1000000,torch.tensor([True]))
    broker.expose_cost_features=True
    enriched=broker.observe(1000000,torch.tensor([True]))
    assert torch.equal(original.enter_allowed,enriched.enter_allowed)
    assert enriched.execution_features[0,3:5].sum()==0


def test_unknown_market_member_masks_allocation_not_opportunity(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from research.rl_trading.v6 import execution_sidecar as module
    extra=rows().filter(pl.col('bucket_index')<10).with_columns((pl.col('bucket_index')+20).alias('bucket_index'),
        (pl.col('quote_timestamp_us')+2000000).alias('quote_timestamp_us'),
        pl.lit(1100.).alias('execution_notional'),pl.lit(109900,dtype=pl.Int64).alias('bid_int'),pl.lit(110100,dtype=pl.Int64).alias('ask_int'))
    source=SimpleNamespace(origin=0,day='fixture',source={'build_id':'build','definition_hash':'definition'},
        attempts={'X':'a','Y':'b'},luld=SimpleNamespace(rows={},end_us=None),luld_certificate='fixture')
    candidates=pl.DataFrame(dict(ticker=['X','Y'],listing_id=['X-id','Y-id'],episode_uid=['x','y'],direction=[1,1],
        time_us=[1000000,1000000],decision_close=[10.,10.],exit_hint_us=[2000000,2000000],exit_hint_close=[11.,11.],score=[.02,.02]))
    requests=candidates.select('ticker','time_us',pl.col('decision_close').alias('reference'))
    monkeypatch.setattr('research.rl_trading.v6.sparse_coverage.certify_source',lambda source,tickers:({'X':(0,4000000),'Y':(2000000,4000000)},'fixture'))
    monkeypatch.setattr(module,'read_requested_windows',lambda source,requests:pl.concat([rows(),extra]))
    proof=module.build_cost_sidecar(source,candidates,requests,tmp_path/'cohort',bank_sha='bank')
    scored=pl.read_parquet(tmp_path/'cohort'/'scores.parquet')
    assert scored.filter(pl.col('ticker')=='X')['netbps'][0]>100
    assert scored.height==2 and not scored['allocation_known'].any()
    assert proof['incomplete_allocation_clocks']==1
    assert pl.read_parquet(tmp_path/'cohort'/'allocation_netbps.parquet').is_empty()

    from research.rl_trading.v6.training import TeacherDecision
    label=TeacherDecision(1000000,0,1,np.zeros(7,np.float32),np.empty(0,np.int64),np.zeros((0,11),np.float32),
        np.array([True,False]),np.empty(0,bool),np.empty(0,bool),np.empty(0,bool),size_fraction=.5,
        episode_uid='x',soft_tokens=(0,1),soft_probabilities=(.1,.9))
    session=SimpleNamespace(day='fixture',source_certificate_sha256='bank',listings=('X-id','Y-id'))
    attached=module.attach_teacher_costs([label],session,tmp_path/'cohort')[0]
    assert attached.size_fraction is None and attached.token==label.token
    assert attached.soft_probabilities==label.soft_probabilities and attached.execution_features.shape==(1,11)


def test_certified_sparse_windows_quote_age_and_known_zero():
    req=pl.DataFrame({'ticker':['X'],'time_us':[1000000],'reference':[10.]})
    sparse=rows().filter(pl.col('bucket_index').is_in([0,7]))
    covered={'X':(0,3000000)}
    estimate=window_estimates(req,sparse,0,coverage=covered)
    assert estimate['cost_available'][0] and estimate['capacity'][0]==20
    assert estimate['quote_age_seconds'][0]==pytest.approx(.201)
    assert not window_estimates(req,sparse,0)['cost_available'][0]
    empty=window_estimates(req,rows().head(0),0,future=True,coverage=covered)
    assert empty['cost_available'][0] and empty['capacity'][0]==0 and empty['volume_valid'][0]==1
    assert empty['quote_valid'][0]==0
    expired=window_estimates(req.with_columns(pl.lit(2000000).alias('time_us')),sparse,0,coverage=covered)
    assert not expired['cost_available'][0] and expired['volume_valid'][0]==1
    incomplete=window_estimates(req,sparse,0,coverage={'X':(500000,3000000)})
    assert not incomplete['cost_available'][0]
    later=sparse.with_columns((pl.col('bucket_index')+20).alias('bucket_index'),
        (pl.col('quote_timestamp_us')+2000000).alias('quote_timestamp_us'))
    assert estimate.equals(window_estimates(req,pl.concat([sparse,later]),0,coverage=covered))


def test_known_no_event_window_scores_zero_without_fabricated_quote():
    net=net_execution_bps(torch.tensor([0.]),torch.tensor([0.]),torch.tensor([0.]),torch.tensor([0.]),torch.tensor([True]))
    assert net.item()==0


@pytest.mark.parametrize('device',['cpu']+(['cuda'] if torch.cuda.is_available() else []))
def test_sparse_tensor_materialization_costs_and_no_invented_fills(device):
    from types import SimpleNamespace
    from research.rl_trading.v6.broker_shards import materialize
    from research.rl_trading.v6.tensor_broker import TensorBroker
    sparse=rows().filter(pl.col('bucket_index').is_in([0,7])).with_columns(
        pl.when(pl.col('bucket_index')==7).then(0.).otherwise(pl.col('execution_volume')).alias('execution_volume'),
        pl.when(pl.col('bucket_index')==7).then(0.).otherwise(pl.col('execution_notional')).alias('execution_notional'),
        pl.when(pl.col('bucket_index')==7).then(0).otherwise(pl.col('volume_valid')).alias('volume_valid'))
    coverage={'X':(0,4000000)};broker=TensorBroker(1,device=device);broker.expose_cost_features=True
    tape=list(materialize(sparse,['X'],0,0,1000000,device=device,
        luld=SimpleNamespace(rows={},end_us=None),coverage=coverage))
    for b in tape:assert broker.advance(b).listing.numel()==0
    broker.update_marks(torch.tensor([0],device=device),torch.tensor([10.],device=device),1000000)
    observation=broker.observe(1000000,torch.tensor([True],device=device))
    expected=window_estimates(pl.DataFrame({'ticker':['X'],'time_us':[1000000],'reference':[10.]}),sparse,0,coverage=coverage)
    from research.rl_trading.v6.execution_features import EXECUTION_NAMES
    assert np.allclose(observation.execution_features.cpu().numpy()[0],expected.select(*EXECUTION_NAMES).to_numpy()[0],atol=1e-5)
    # Only the original trade bucket offers capacity; empty buckets cannot fill.
    assert sum(float(b.volume.sum()) for b in tape)==100 and broker.quantity.sum()==0
    for b in materialize(rows().head(0),['X'],0,1000000,2000000,device=device,
        luld=SimpleNamespace(rows={},end_us=None),coverage=coverage):broker.advance(b)
    expired=broker.observe(2000000,torch.tensor([True],device=device)).execution_features
    assert expired[0,3]==0 and expired[0,4]==1 and expired[0,5:8].sum()==0


def test_sparse_coverage_integrity_cached_and_mismatch_rejected(tmp_path,monkeypatch):
    import sqlite3
    from types import SimpleNamespace
    from datetime import date
    from research.rl_trading.v6 import sparse_coverage as module
    ledger=tmp_path/'ledger.sqlite3'
    with sqlite3.connect(ledger) as db:
        db.execute('CREATE TABLE units(ticker,attempt_id,status,output_rows,output_hash,build_id,session_date,stage)')
        db.execute("INSERT INTO units VALUES('X','attempt','complete',2,'123','build','2026-08-18','broker_100ms')")
    source=SimpleNamespace(ledger=ledger,day=date(2026,8,18),source={'build_id':'build'},attempts={'X':'attempt'},reader=object())
    calls=[]
    monkeypatch.setattr(module.sql,'query',lambda reader,statement:calls.append(statement) or [{'ticker':'X','n':2,'keys':2,'hash':'123'}])
    coverage,_=module.certify_source(source,['X']);module.certify_source(source,['X'])
    assert len(calls)==1 and coverage['X'][1]>coverage['X'][0]
    source.sparse_coverage={}
    monkeypatch.setattr(module.sql,'query',lambda reader,statement:[{'ticker':'X','n':1,'keys':1,'hash':'123'}])
    with pytest.raises(ValueError,match='content changed'):module.certify_source(source,['X'])
