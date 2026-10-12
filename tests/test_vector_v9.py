"""V9 arithmetic, ancestry, state accounting, and display isolation."""
from dataclasses import replace
from io import StringIO
import json
import numpy as np
import pytest
import torch
from rich.console import Console
from research.vectorized_backtest.v7.qualify import synthetic,member
from research.vectorized_backtest.v7.evaluator import replay_cohort as baseline
from research.vectorized_backtest.v9.evaluator import replay_cohort,Execution
from research.vectorized_backtest.v9.objectives import Objective,fractional_tail,episode_statistics,score
from research.vectorized_backtest.v9.genome import sample,mutate
from research.vectorized_backtest.v9.lineage import Pedigree,Selection,next_population,program_key
from research.vectorized_backtest.v9.complexity import simplify,count


def metrics(profits):
    return dict(net_pnl=[sum(p) for p in profits],episode_profits=profits,
        closed_positions=list(map(len,profits)),winning_positions=[sum(v>0 for v in p) for p in profits],
        terminal_valid=[True]*len(profits),drawdown=[0.]*len(profits),capital_dollar_seconds=[0.]*len(profits),
        stop_risk_dollar_seconds=[0.]*len(profits),worst_position_pnl=[min(p,default=0.) for p in profits])


def test_feature_worker_reuses_initialized_source_context(monkeypatch):
    from datetime import date
    import polars as pl
    from research.vectorized_backtest.v9 import extract_features as producer
    from research.rl_trading.v1 import arte_source
    from research.rl_trading.v6 import source,reference,features
    day,prior_day=date(2026,9,14),date(2026,9,11)
    current={'build_id':'current'}
    prior={'units':{str(prior_day):{'ABC':{}}}}
    client=object();calls=[];expected=object()
    monkeypatch.setattr(producer,'CLIENT',None)
    monkeypatch.setattr(producer,'SOURCE_CONTEXT',None)
    with pytest.raises(RuntimeError,match='not initialized'):
        producer.work({'listing_id':'id','ticker':'ABC'})
    monkeypatch.setattr(arte_source,'reader',lambda **kw:client)
    bars=pl.DataFrame({'x':[1]});indicators=object();previous=object()
    def candles(c,build,d,ticker):
        assert c is client and build is current and d==day
        calls.append(ticker)
        return bars,indicators
    def volume(c,build,d,ticker):
        assert c is client and build is prior and d==prior_day and ticker=='ABC'
        return previous
    monkeypatch.setattr(source,'read_candles',candles)
    monkeypatch.setattr(source,'read_previous_volume',volume)
    monkeypatch.setattr(reference,'read_reference',lambda *args:('seed','splits','fundamentals','evidence'))
    def encode(*args):
        assert args==(day,bars,indicators,previous,'seed','splits','fundamentals')
        return expected
    monkeypatch.setattr(features,'encode',encode)
    producer.initialize(day,prior_day,current,prior)
    for identity in ('first','second'):
        assert producer.work({'listing_id':identity,'ticker':'ABC'})==(identity,expected)
    assert calls==['ABC','ABC']


def test_top_feature_scope_includes_transient_members_and_rejects_bad_indices():
    from research.vectorized_backtest.v9.opening_basis import selected_identities
    members=[{'listing_id':str(i)} for i in range(4)]
    top=np.array([[0,1],[2,0],[-1,3]],dtype=np.int32)
    assert selected_identities(top,members)==['0','1','2','3']
    assert selected_identities(np.full((2,2),-1),members)==[]
    for invalid in (np.array([[4]]),np.array([[-2]]),np.array([[1.5]])):
        with pytest.raises(ValueError):selected_identities(invalid,members)


def test_ranked_scope_binds_history_and_rejects_changed_ranking(tmp_path):
    from research.vectorized_backtest.v9.opening_basis import feature_scope
    from research.vectorized_backtest.v6.torch_backtest.runtime import file_hash
    days=['2026-09-11','2026-09-14']
    for day,index in zip(days,[0,1]):
        folder=tmp_path/day;folder.mkdir()
        (folder/'eligibility.json').write_text(json.dumps({'listings':[{'listing_id':'a'},{'listing_id':'b'}]}))
        np.save(folder/'top_indices.npy',np.array([[index,-1]],dtype=np.int32))
        record=dict(status='complete',day=day,split_role='validation',validation_opened=True,
            identity=dict(sessions_sha256='sessions',ranking_before_features=True),
            files={name:file_hash(folder/name) for name in ['eligibility.json','top_indices.npy']})
        (folder/'complete.json').write_text(json.dumps(record))
    scope=feature_scope(tmp_path,days,'sessions')
    assert scope['needed']=={days[0]:['a','b'],days[1]:['b']}
    assert scope['selected']=={days[0]:['a'],days[1]:['b']}
    with pytest.raises(ValueError):feature_scope(tmp_path,days[::-1],'sessions')
    with pytest.raises(ValueError):feature_scope(tmp_path,days,'wrong-session')
    np.save(tmp_path/days[1]/'top_indices.npy',np.array([[0,-1]],dtype=np.int32))
    with pytest.raises(ValueError,match='source changed'):feature_scope(tmp_path,days,'sessions')


def test_same_backend_accounting_gate_rejects_even_roundoff():
    from research.vectorized_backtest.v9.qualify_synthetic import exact_accounting
    expected={'net_pnl':torch.tensor([1.],dtype=torch.float64)}
    actual={'net_pnl':torch.tensor([1.+1e-13],dtype=torch.float64)}
    with pytest.raises(AssertionError):exact_accounting(expected,actual)
    exact_accounting(expected,expected)


def test_worker_preserves_nonpickleable_compiler_error(monkeypatch):
    from research.vectorized_backtest.v9 import session_lanes
    import pickle
    class Worker:
        def run(self,*args):
            error=ValueError('compiler failure')
            error.callback=lambda:None
            raise error
    monkeypatch.setattr(session_lanes,'_worker',Worker())
    with pytest.raises(RuntimeError,match='compiler failure') as error:session_lanes.evaluate()
    assert 'compiler failure' in str(pickle.loads(pickle.dumps(error.value)))


def test_authorized_stop_gate_binds_checkpoint_and_sealed_receipts(tmp_path):
    from research.vectorized_backtest.v9.prepare_validation import require_finished
    from research.vectorized_backtest.v6.torch_backtest.runtime import write_json,file_hash
    root=tmp_path/'v7';session=root/'generation-0001'/'2026-08-01';session.mkdir(parents=True)
    write_json(session/'receipt.json',dict(full_session=True))
    seal=session.parent/'complete.json'
    write_json(seal,dict(session_receipts={'2026-08-01':file_hash(session/'receipt.json')}))
    write_json(root/'checkpoint.json',dict(completed_generations=1,contract=dict(generations=50,input_receipts={'2026-08-01':{}})))
    write_json(root/'status.json',dict(status='interrupted'));write_json(root/'active.json',dict(pid=None))
    receipt=tmp_path/'stop.json'
    write_json(receipt,dict(version='v8-authorized-v7-stop-v1',campaign=str(root),generation=1,
        checkpoint_sha256=file_hash(root/'checkpoint.json'),seal_sha256=file_hash(seal),processes=[]))
    with pytest.raises(RuntimeError,match='Wait for V7'):require_finished(root)
    require_finished(root,receipt)
    (root/'owner.lock').write_text('held')
    with pytest.raises(RuntimeError,match='ownership'):require_finished(root,receipt)
    (root/'owner.lock').unlink();write_json(session/'receipt.json',dict(full_session=False))
    with pytest.raises(RuntimeError,match='receipt changed'):require_finished(root,receipt)


def test_fractional_trade_tails():
    config=Objective(top_trim_fraction=.25,trade_tail_fraction=.25)
    # Tail mass 1.25; losses are retained and then penalized independently.
    result=episode_statistics([[-20,-10,1,5,100],[]],config)
    assert result['trimmed_profit'][0]==pytest.approx(-25.25)
    assert result['trade_tail_loss'][0]==pytest.approx(18.)
    assert result['closed_positions'].tolist()==[5,0]
    total,mean=fractional_tail([[-20,-10,1,5,100]],.25)
    assert total[0]==-22.5 and mean[0]==-18


def test_objective_reconciles_and_empty_is_ineligible():
    config=Objective(top_trim_fraction=.25,trade_tail_fraction=.25,session_tail_fraction=.5,
        complexity_dollars_per_node=2,block_tail_weight=0,concentration_weight=0)
    result=score([metrics([[-20,100],[]]),metrics([[-10,1,5],[]])],[7,1],config)
    assert result['score'][0]==pytest.approx(-25.25-.25*5*18+.25*2*(-4)-14)
    assert result['feasible'].tolist()==[True,False]
    bad=metrics([[2]])
    bad['net_pnl']=[3]
    with pytest.raises(ValueError,match='reconcile'):score([bad],[1])


@pytest.mark.parametrize('fees',[(0,0,0),(10,.005,1)])
def test_episode_accounting_matches_v7(fees):
    execution=Execution(cost_bps=fees[0],fee_per_share=fees[1],minimum_order_fee=fees[2])
    gates=torch.tensor([1,4,8,0,0,0],dtype=torch.uint8)[None,:,None]
    left=baseline([synthetic('cpu')],[member()],[gates],execution=execution)[0]
    right=replay_cohort([synthetic('cpu')],[member()],[gates],execution=execution)[0]
    for key in left:torch.testing.assert_close(left[key],right[key],rtol=0,atol=0)
    assert sum(right['episode_profits'][0])==pytest.approx(right['net_pnl'][0].item())
    assert right['turnover'][0]>2000


def test_episode_overflow_fails_closed():
    data=synthetic('cpu');data.tensors['mark'].fill_(10)
    gates=torch.tensor([1,2,1,2,1,0],dtype=torch.uint8)[None,:,None]
    with pytest.raises(MemoryError,match='overflow'):
        replay_cohort([data],[member()],[gates],episode_capacity=1)


def test_exact_boundary_and_two_ticker_closes():
    from research.vectorized_backtest.v9.episodes import Episodes
    ledger=Episodes(1,1,2,'cpu',max_listings=2);before=torch.zeros(1,1,2,9,dtype=torch.float64)
    before[...,0]=1;before[...,8]=torch.tensor([2.,4.])
    after=before.clone();after[...,0]=0;after[...,7]=torch.tensor([10.,1.])
    ledger.append(before,after,torch.tensor([True]))
    value=ledger.export()[0]
    assert value['episode_profits']==[[8.,-3.]] and value['episode_listings']==[[0,1]]


def test_compiled_episode_offsets_are_dynamic():
    from research.vectorized_backtest.v9.episodes import Episodes
    ledger=Episodes(1,1,4,'cpu',max_listings=2)
    before=torch.zeros(1,1,2,9,dtype=torch.float64);before[...,0]=1
    after=before.clone();after[...,0]=0;after[...,7]=torch.tensor([2.,3.])
    compiled=torch.compile(ledger.append,backend='eager',fullgraph=True)
    compiled(before,after,torch.tensor([True]));compiled(before,after,torch.tensor([True]))
    assert ledger.export()[0]['episode_profits']==[[2.,3.,2.,3.]]


def test_pedigree_restriction_and_resume_determinism():
    rng=np.random.default_rng(19);members=sample(rng,30);ped=Pedigree(5)
    ids=ped.birth(members,1,[(-1,-1)]*30,['fresh']*30)
    child=mutate(rng,members[0]);child_id=ped.birth([child],2,[(0,-1)],['mutation'])[0]
    assert ped.relatives(child_id,[0,1],2).tolist()==[True,False]
    late=ped.birth([child],20,[(0,-1)],['mutation'])[0]
    assert ped.relatives(late,[child_id],20).tolist()==[True]
    saved=json.loads(json.dumps(ped.records));state=json.loads(json.dumps(rng.bit_generator.state))
    first,first_ids,diag=next_population(rng,members,ids,ped,list(range(30)),3)
    other=Pedigree(5,saved);r=np.random.default_rng();r.bit_generator.state=state
    second,second_ids,diag2=next_population(r,members,ids,other,list(range(30)),3)
    assert [v.payload() for v in first]==[v.payload() for v in second]
    assert first_ids.tolist()==second_ids.tolist() and diag==diag2
    for i in first_ids:
        record=ped.records[int(i)]
        if record['operation']=='crossover':
            a,b=record['parents'];assert not ped.relatives(a,[b],3)[0]


def test_discrete_policies_mutate():
    rng=np.random.default_rng(0);parent=sample(rng,1)[0]
    children=[mutate(rng,parent) for _ in range(400)]
    for field in ('maximum_adds','cooldown','swing_left','swing_right'):
        assert len({getattr(c.policy,field) for c in children})>1


def test_complexity_shared_nodes_and_dead_code():
    from research.vectorized_backtest.v6.torch_backtest.program import Node,Program,Op
    p=Program((Node(Op.CONSTANT,value=1,unit='bool'),Node(Op.CONSTANT,value=0,unit='bool'),Node(Op.AND,a=0,b=0)),2)
    assert len(simplify(p).nodes)==1


@pytest.mark.parametrize('width,height',[(90,28),(130,38),(190,45)])
def test_validation_terminal(width,height):
    from research.vectorized_backtest.v9.terminal import render
    from research.vectorized_backtest.v9.run_search import ranking
    status=dict(status='running',stage='Validation',config=dict(population=1024,generations=50,training_sessions=30),
        completed_generations=1,completed_sessions=0,completed_batches=0,total_batches=60,
        validation_status='Display only',validation_generation=1,validation_ranking=[],top_strategies=[],
        progress={},execution={},started_epoch=0,updated_epoch=0,messages=[])
    population=sample(np.random.default_rng(2),2);ped=Pedigree();ids=ped.birth(population,1,[(-1,-1)]*2,['fresh']*2)
    _,rows=ranking([metrics([[20.],[]])],population,ids,ped,Objective(),selection_required=False)
    for row in rows:row['training_rank']=row['rank']
    status['validation_ranking']=rows
    out=StringIO();console=Console(file=out,width=width,height=height,color_system=None)
    console.print(render(status,width=width,height=height,now=0,view='validation'))
    text=out.getvalue();assert 'Validation ranking' in text and 'NO selection feedback' in text


def test_validation_no_trade_is_displayable():
    from research.vectorized_backtest.v9.run_search import ranking
    rng=np.random.default_rng(2);population=sample(rng,1);ped=Pedigree();ids=ped.birth(population,1,[(-1,-1)],['fresh'])
    _,rows=ranking([metrics([[]])],population,ids,ped,Objective(),selection_required=False)
    assert rows[0]['metrics']['closed_positions']==0 and not rows[0]['valid']


@pytest.mark.parametrize("mixed_initialization",[False,True])
def test_validation_cannot_change_successors(monkeypatch,tmp_path,mixed_initialization):
    import research.vectorized_backtest.v9.run_search as search
    from research.vectorized_backtest.v6.torch_backtest.runtime import write_json,require_runtime
    class Panel:
        def __init__(self,*a):self.generation_samples=[]
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def update(self,**kwargs):pass
        def begin_generation(self,*a):pass
        def begin_evaluation(self,*a):pass
        def publish(self):pass
    validation_multiplier=[1]
    class Lanes:
        def __init__(self,config,*a):
            assert config['capture_gib']==8.
            self.config=config
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def release_plans(self):pass
        def generation(self,days,population,generation,token):
            for day in days:
                profits=[[float(int(program_key(m)[:6],16)%1000+1)] for m in population]
                if self.config['split']=='validation':profits=[[p[0]*validation_multiplier[0]] for p in profits]
                root=require_runtime(Path(self.config['output'])/f'generation-{generation:04d}'/day)
                write_json(root/'receipt.json',dict(metrics=metrics(profits),full_session=True,population_sha256=token))
            return []
    from pathlib import Path
    monkeypatch.setattr(search,'CampaignProgress',Panel);monkeypatch.setattr(search,'SessionLanes',Lanes)
    monkeypatch.setattr(search,'bind_dataset',lambda path,role:dict(days=['2026-08-01'] if role=='training' else ['2026-09-01'],
        inputs='unused',history='unused',receipts={'day':'bound'}))
    initial=None
    if mixed_initialization:
        from research.vectorized_backtest.v9.genome import sample
        initial=tmp_path/'initial.json';payload=[m.payload() for m in sample(np.random.default_rng(13),10)]
        write_json(initial,dict(version='v8-mixed-initial-population-v1',population=payload,
            population_sha256=search.digest(payload),source=dict(generation=43),
            initialization=dict(seed_count=1,random_count=9,selected_candidates=[7],validation_feedback=False)))
    for suffix,multiplier in [('a',1),('b',-100)]:
        validation_multiplier[0]=multiplier
        search.run('train','validation',tmp_path/suffix,population_size=10,generations=2,batch_size=4,device='cpu',backend='eager',initial_population=initial,capture_gib=8.)
    assert json.loads((tmp_path/'a'/'checkpoint.json').read_text())['contract']['capture_gib']==8.
    first=json.loads((tmp_path/'a'/'checkpoint.json').read_text())['state']
    second=json.loads((tmp_path/'b'/'checkpoint.json').read_text())['state']
    for key in ('population','ids','pedigree','rng'):assert first[key]==second[key]
    assert first['validation_ranking']!=second['validation_ranking']
    if mixed_initialization:
        assert first['pedigree'][0]['operation']=='v7_seed'
        assert first['pedigree'][0]['v7_source']==dict(generation=43,candidate=7)
        assert all(r['operation']=='fresh' for r in first['pedigree'][1:10])
        assert (tmp_path/'a/initialization.json').is_file()
    # Completed observations remain integrity checked on resume, without selection feedback.
    assert search.run('train','validation',tmp_path/'a',population_size=10,generations=2,batch_size=4,device='cpu',backend='eager',initial_population=initial,capture_gib=8.)==0
    receipt=tmp_path/'a/validation/generation-0001/2026-09-01/receipt.json'
    receipt.write_text('{}',encoding='utf-8')
    with pytest.raises(ValueError,match='Sealed validation receipt changed'):
        search.run('train','validation',tmp_path/'a',population_size=10,generations=2,batch_size=4,device='cpu',backend='eager',initial_population=initial,capture_gib=8.)


def test_shared_rule_groups_expand_exactly():
    from research.vectorized_backtest.v9.rule_plan import RulePlan
    from research.vectorized_backtest.v9.features import CATALOG
    members=[member()]*12
    plan=RulePlan(members,'cpu')
    for group in plan.groups:
        assert group['unique_count']==1
        values=torch.zeros((2,16,len(group['columns'])))
        valid=torch.ones_like(values,dtype=torch.bool)
        result=plan.execute(plan.groups.index(group),values,valid)[group['expand']]
        assert result.shape==(12,2,16)
        assert torch.equal(result,result[:1].expand_as(result))


def test_display_authorization_rejects_training_date(tmp_path):
    from research.vectorized_backtest.v9.input_authority import authorize_day
    from research.vectorized_backtest.v6.torch_backtest.runtime import write_json,file_hash
    sessions=tmp_path/'sessions.json';authorization=tmp_path/'authorization.json'
    write_json(sessions,dict(training=[dict(day='2026-08-01')],validation=[dict(day='2026-09-01')],validation_mode='display_only'))
    write_json(authorization,dict(version='v8-validation-display-authorization-v1',sessions_sha256=file_hash(sessions),selection_feedback=False))
    assert authorize_day(sessions,'2026-09-01',authorization)['day']=='2026-09-01'
    with pytest.raises(ValueError,match='explicitly'):authorize_day(sessions,'2026-08-01',authorization)


def test_real_session_worker_reuses_exact_results(tmp_path):
    from test_vector_v7_lightweight import fixture_files
    from research.vectorized_backtest.v9.materialize_history import prepare
    from research.vectorized_backtest.v9.session_lanes import Worker
    from research.vectorized_backtest.v6.torch_backtest.runtime import write_json
    import queue
    root,_=fixture_files(tmp_path)
    receipt=json.loads((root/'complete.json').read_text())
    receipt['listings']=[dict(listing_id='stable-listing-a',ticker='A')]
    write_json(root/'complete.json',receipt)
    history=tmp_path/'history'/'inputs';prepare(root,history,workers=1)
    config=dict(inputs=str(tmp_path),history=str(tmp_path/'history'),output=str(tmp_path/'output'),
        feature_cache=None,device='cpu',backend='eager',batch_size=2,maximum_data_gib=1,
        maximum_gate_gib=.1,execution=dict(fee_per_share=.005,minimum_order_fee=1),
        source_sha256='test-code',split='training',capture_gib=1,lane_memory_bytes=0,plan_limit=1)
    worker=Worker(config,queue.Queue(),0)
    try:
        payload=[member().payload()]*2
        worker.run('inputs',None,payload,1,'test-population',1)
        worker.run('inputs',None,payload,2,'test-population',1)
        first=json.loads((tmp_path/'output/generation-0001/inputs/receipt.json').read_text())
        second=json.loads((tmp_path/'output/generation-0002/inputs/receipt.json').read_text())
        assert {k:v for k,v in first['metrics'].items() if k!='result_reused'}=={k:v for k,v in second['metrics'].items() if k!='result_reused'}
        assert second['metrics']['result_reused']==[True,True]
        assert second['reused_candidates']==2 and second['evaluated_candidates']==0
        assert first['metrics']['ticker_pnl'][0]=={'stable-listing-a':-2.}
        cache=tmp_path/'output/result-cache/inputs.json';value=json.loads(cache.read_text())
        value['results'][next(iter(value['results']))]['net_pnl']=1e9;write_json(cache,value)
        with pytest.raises(ValueError,match='integrity'):worker.run('inputs',None,payload,3,'test-population',1)
    finally:worker.close()


def test_validation_source_contract_keeps_legacy_guard(tmp_path):
    from test_vector_v7_lightweight import fixture_files
    from research.vectorized_backtest.v9.materialize_history import prepare
    from research.vectorized_backtest.v9.data import SessionData
    from research.vectorized_backtest.v6.torch_backtest.sparse_replay import verify_sparse_receipt
    from research.vectorized_backtest.v6.torch_backtest.runtime import write_json
    root,_=fixture_files(tmp_path);receipt=json.loads((root/'complete.json').read_text())
    receipt.update(status='complete',split_role='validation',validation_opened=True)
    receipt['identity']['version']='v8-validation-sparse-compact-input-v1'
    write_json(root/'complete.json',receipt)
    with pytest.raises(ValueError):verify_sparse_receipt(root)
    history=tmp_path/'history';prepare(root,history,workers=1)
    data=SessionData(root,history)
    assert data.receipt['validation_opened'] is True
    data.close()


def test_grouped_rules_match_v7_on_certified_fixture(tmp_path):
    from test_vector_v7_lightweight import fixture_files
    from research.vectorized_backtest.v9.data import SessionData
    from research.vectorized_backtest.v7.rule_plan import RulePlan as Original
    from research.vectorized_backtest.v9.rule_plan import RulePlan as Optimized
    root,history=fixture_files(tmp_path);data=SessionData(root,history)
    members=sample(np.random.default_rng(128),12);members=members+members+members[:4]
    left=Original(members,'cpu');right=Optimized(members,'cpu')
    try:
        expected=left.evaluate(data,members,17,1,.1)
        actual=right.evaluate(data,members,17,1,.1)
        torch.testing.assert_close(actual,expected,rtol=0,atol=0)
    finally:left.close();right.close();data.close()


def test_episode_compact_export_preserves_empty_and_ragged_candidates():
    from research.vectorized_backtest.v9.episodes import Episodes
    ledger = Episodes(2, 3, 8, 'cpu', max_listings=2)
    closed = torch.tensor([[[True, False], [False, False], [True, True]],
                           [[False, True], [False, False], [False, False]]])
    profits = torch.arange(12, dtype=torch.float64).reshape(2, 3, 2)
    ledger.append_events(closed, profits)
    assert ledger.export() == [
        dict(episode_profits=[[0.], [], [4., 5.]], episode_listings=[[0], [], [0, 1]]),
        dict(episode_profits=[[7.], [], []], episode_listings=[[1], [], []])]
    ledger.reset()
    assert ledger.export() == [dict(episode_profits=[[], [], []], episode_listings=[[], [], []])] * 2


def test_initial_seeds_filter_scaled_behavior_and_fill_random_shortfall():
    from research.vectorized_backtest.v9.initial_population import diverse_seeds,mix
    from research.vectorized_backtest.v9.genome import sample
    population=sample(np.random.default_rng(19),4)
    ordered=[dict(net_pnl=[10.,20.,0.,0.],closed_positions=[1,2,0,0]),
             dict(net_pnl=[0.,0.,10.,0.],closed_positions=[0,0,1,0])]
    selected,rejected=diverse_seeds(population,ordered,[0,1,2,3],4)
    assert selected==[0,2] and rejected==1
    left,report=mix(population,ordered,[0,1,2,3],100,77)
    right,again=mix(population,ordered,[0,1,2,3],100,77)
    assert report==again and [v.payload() for v in left]==[v.payload() for v in right]
    assert report['seed_count']==2 and report['random_count']==98 and report['requested_seed_count']==10
    assert len({program_key(m) for m in left})==100
    assert left[0].payload()==population[0].payload() and left[1].payload()==population[2].payload()
    with pytest.raises(ValueError):mix(population,ordered,[0],100,fraction=1.1)


@pytest.mark.parametrize('budget',[0.,17.,float('nan'),float('inf')])
def test_search_rejects_unbounded_capture_cache_before_opening_data(budget):
    from research.vectorized_backtest.v9.run_search import run
    with pytest.raises(ValueError,match='Capture cache'):
        run('unused','unused','unused',capture_gib=budget)


def test_v9_breadth_beats_single_session_with_equal_profit():
    from research.vectorized_backtest.v9.objectives import period_statistics
    panel=np.zeros((30,2));panel[0,0]=300;panel[:,1]=10
    stats=period_statistics(panel,Objective())
    assert stats['block_tail_daily_profit'].tolist()==[0,10]
    assert stats['session_concentration_dollars'].tolist()==[300,10]
    rows=[metrics([[float(x)] if x else [] for x in day]) for day in panel]
    result=score(rows,[1,1])
    assert result['score'][1]>result['score'][0]


def test_v9_short_block_uses_session_mass_and_losses_are_preserved():
    from research.vectorized_backtest.v9.objectives import period_statistics
    panel=np.array([[10.]]*5+[[-20.]])
    stats=period_statistics(panel,Objective())
    assert stats['block_tail_daily_profit'][0]==pytest.approx(0) # (-20 + 2*10)/3
    assert stats['session_concentration_dollars'][0]==10
    assert stats['block_count'][0]==2
    assert period_statistics(np.zeros((6,1)),Objective())['session_concentration_dollars'][0]==0
    assert period_statistics(-np.ones((6,1)),Objective())['block_tail_daily_profit'][0]==-1


def test_v9_zero_weights_reproduce_v8_score_exactly():
    from research.vectorized_backtest.v8.objectives import score as old_score
    rng=np.random.default_rng(18)
    rows=[metrics([rng.normal(size=7).tolist(),[]]) for _ in range(11)]
    new=score(rows,[7,0],Objective(block_tail_weight=0,concentration_weight=0))
    old=old_score(rows,[7,0])
    for key in ('score','cost','feasible','total_pnl','trimmed_profit'):
        np.testing.assert_array_equal(new[key],old[key])


@pytest.mark.parametrize('kwargs',[dict(block_sessions=0),dict(block_sessions=2.5),dict(block_sessions=True),dict(block_tail_fraction=0),dict(concentration_weight=-1)])
def test_v9_invalid_objective_rejected(kwargs):
    with pytest.raises(ValueError):Objective(**kwargs).validate()
