"""V18 arithmetic, ancestry, state accounting, and display isolation."""
from dataclasses import replace
from io import StringIO
import json
import numpy as np
import pytest
import torch
from rich.console import Console
from research.vectorized_backtest.v7.qualify import synthetic,member
from research.vectorized_backtest.v7.evaluator import replay_cohort as baseline
from research.vectorized_backtest.v18.evaluator import replay_cohort,Execution
from research.vectorized_backtest.v18.objectives import Objective,fractional_tail,episode_statistics,score
from research.vectorized_backtest.v18.genome import sample,mutate
from research.vectorized_backtest.v18.lineage import Pedigree,Selection,next_population,program_key
from research.vectorized_backtest.v18.complexity import simplify,count


def test_overall_ranking_is_net_profit_not_score():
    from research.vectorized_backtest.v18.run_search import ranking
    pop=sample(np.random.default_rng(1),2);p=Pedigree();ids=p.birth(pop,1,[(-1,-1)]*2,['fresh']*2)
    data=metrics([[200.],[100.]]);data['drawdown']=[10000.,0.]
    rank,rows=ranking([data],pop,ids,p,Objective())
    assert rank==[0,1] and rows[0]['score']<rows[1]['score']


@pytest.mark.parametrize('split',['training','validation'])
def test_all_display_sorts_preserve_overall_rank_and_input(split):
    from research.vectorized_backtest.v18.ranking_display import SortedRows
    rows=[dict(candidate=2,serial=11,rank=1,metrics=dict(total_pnl=120)),dict(candidate=1,serial=10,rank=2,metrics=dict(total_pnl=100))]
    before=json.dumps(rows)
    scores=np.array([[10.,20.],[90.,100.]]);profits=np.array([[30.,80.],[70.,40.]])
    active=np.ones((2,2),bool);days=['2026-08-01','2026-08-02']
    session=SortedRows(rows,days,scores,profits,active,'session_score',split)
    assert [(r['session'],r['candidate']) for r in session[:]]==[(days[0],2),(days[0],1),(days[1],2),(days[1],1)]
    profit=SortedRows(rows,days,scores,profits,active,'session_profit',split)
    assert [r['session_profit'] for r in profit[:]]==[80.,70.,40.,30.]
    net=SortedRows(rows,days,scores,profits,active,'net_profit',split)
    assert [r['candidate'] for r in net[:]]==[2,1]
    assert [r['rank'] for r in profit[:]]==[1,2,1,2]
    assert json.dumps(rows)==before


def test_observer_ui_reads_do_not_wait_for_disk_or_sort(monkeypatch,tmp_path):
    import threading,time
    from research.vectorized_backtest.v18 import ranking_display as display
    entered=threading.Event();release=threading.Event();threads=[]
    (tmp_path/'status.json').write_text(json.dumps(dict(validation_feedback=False,training_ranking_generation=1)))
    def blocked(*args):
        threads.append(threading.get_ident());entered.set();assert release.wait(5)
        return [],np.array(['2026-08-01']),np.zeros((1,0)),np.zeros((1,0)),np.zeros((1,0),bool)
    monkeypatch.setattr(display,'load_panel',blocked)
    observer=display.RankingObserver(tmp_path).start()
    try:
        assert entered.wait(3)
        start=time.perf_counter();observer.set_view('validation','net_profit');assert observer.read()=={}
        assert time.perf_counter()-start<.1
        assert threads==[observer.thread.ident] and threads[0]!=threading.get_ident()
    finally:release.set();observer.close()


def test_origin_prefix_and_actual_birth_are_separate():
    rng=np.random.default_rng(9);pop=sample(rng,4);p=Pedigree()
    first=p.birth(pop,1,[(-1,-1)]*4,['fresh']*4)
    original=json.dumps(p.records)
    p.birth(pop[:2],7,[(0,1),(2,-1)],['crossover','mutation'])
    p.birth(pop[2:],7,[(-1,-1)]*2,['fresh']*2)
    assert json.dumps(p.records[:4])==original
    assert all(r['label'].startswith('G001-') and r['generation']==7 for r in p.records[4:6])
    assert all(r['label'].startswith('G007-') for r in p.records[6:])


def test_vectorized_ancestry_matches_reference():
    from research.vectorized_backtest.v18.lineage import MateIndex
    pop=sample(np.random.default_rng(1),40);p=Pedigree()
    p.birth(pop[:20],1,[(-1,-1)]*20,['fresh']*20)
    p.birth(pop[20:],2,[(i,(i+1)%20) for i in range(20)],['crossover']*20)
    ids=np.arange(40);index=MateIndex(p,ids)
    for a in ids:np.testing.assert_array_equal(index.unrelated(a),~p.relatives(a,ids,3))


def test_session_quota_crossovers_and_deterministic_resume():
    from research.vectorized_backtest.v18.lineage import session_order,balanced_shortlist
    rng=np.random.default_rng(41);pop=sample(rng,1024);p=Pedigree();ids=p.birth(pop,1,[(-1,-1)]*1024,['fresh']*1024)
    panel=rng.normal(size=(30,1024));eligible=np.ones_like(panel,dtype=bool)
    order,valid=session_order(panel,eligible,ids)
    shortlist=balanced_shortlist(order,valid,512,1)
    assert len(shortlist)==len(set(shortlist))==512
    assert set(order[:,0])<=set(shortlist)
    records=json.loads(json.dumps(p.records));state=rng.bit_generator.state
    left,new_ids,report=next_population(rng,pop,ids,p,[],2,session_values=panel,session_eligible=eligible,global_values=panel.sum(0))
    other=Pedigree(records=records);again=np.random.default_rng();again.bit_generator.state=state
    right,right_ids,report2=next_population(again,pop,ids,other,[],2,session_values=panel,session_eligible=eligible,global_values=panel.sum(0))
    assert [m.payload() for m in left]==[m.payload() for m in right] and report==report2
    np.testing.assert_array_equal(new_ids,right_ids)
    assert len(left)==len({program_key(m) for m in left})==1024
    assert report['session_parent_pool_width']==13 and report['global_slots']==report['local_slots']==512
    assert report['elites']+report['crossovers']+report['fresh_candidates']==1024
    assert report['mutation_requested_fields']==report['mutation_applied_fields']
    assert report['mutations']>0
    for i in new_ids:
        r=p.records[int(i)]
        if 'crossover' in r['operation']:
            a,b=r['parents'];assert not p.relatives(a,[b],2)[0]
            assert r['origin_generation']==p.records[a]['origin_generation']
            if r['selection_route']=='intrasession':assert r['source_session']==r['partner_session']
    for session in range(30):
        partners=[r['partner_session'] for r in p.records if r.get('selection_route')=='champion' and r['source_session']==session]
        assert len(partners)==len(set(partners))<=5


def test_no_eligible_mates_falls_back_without_relaxing_ancestry():
    pop=sample(np.random.default_rng(10),30);p=Pedigree();p.birth(pop[:1],1,[(-1,-1)],['fresh'])
    ids=p.birth(pop,2,[(0,-1)]*30,['mutation']*30)
    values=np.arange(90).reshape(3,30)
    result,new_ids,report=next_population(np.random.default_rng(2),pop,ids,p,[],3,
        session_values=values,session_eligible=np.ones((3,30),bool),global_values=values.sum(0))
    assert report['crossovers']==report['mutations']==0 and report['unrelated_mate_fallbacks']>0
    assert len(result)==30


def metrics(profits):
    return dict(net_pnl=[sum(p) for p in profits],episode_profits=profits,
        closed_positions=list(map(len,profits)),winning_positions=[sum(v>0 for v in p) for p in profits],
        terminal_valid=[True]*len(profits),drawdown=[0.]*len(profits),capital_dollar_seconds=[0.]*len(profits),
        stop_risk_dollar_seconds=[0.]*len(profits),worst_position_pnl=[min(p,default=0.) for p in profits])


def test_feature_worker_reuses_initialized_source_context(monkeypatch):
    from datetime import date
    import polars as pl
    from research.vectorized_backtest.v18 import extract_features as producer
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
    from research.vectorized_backtest.v18.opening_basis import selected_identities
    members=[{'listing_id':str(i)} for i in range(4)]
    top=np.array([[0,1],[2,0],[-1,3]],dtype=np.int32)
    assert selected_identities(top,members)==['0','1','2','3']
    assert selected_identities(np.full((2,2),-1),members)==[]
    for invalid in (np.array([[4]]),np.array([[-2]]),np.array([[1.5]])):
        with pytest.raises(ValueError):selected_identities(invalid,members)


def test_ranked_scope_binds_history_and_rejects_changed_ranking(tmp_path):
    from research.vectorized_backtest.v18.opening_basis import feature_scope
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
    from research.vectorized_backtest.v18.qualify_synthetic import exact_accounting
    expected={'net_pnl':torch.tensor([1.],dtype=torch.float64)}
    actual={'net_pnl':torch.tensor([1.+1e-13],dtype=torch.float64)}
    with pytest.raises(AssertionError):exact_accounting(expected,actual)
    exact_accounting(expected,expected)


def test_worker_preserves_nonpickleable_compiler_error(monkeypatch):
    from research.vectorized_backtest.v18 import session_lanes
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
    from research.vectorized_backtest.v18.prepare_validation import require_finished
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
    from research.vectorized_backtest.v18.episodes import Episodes
    ledger=Episodes(1,1,2,'cpu',max_listings=2);before=torch.zeros(1,1,2,9,dtype=torch.float64)
    before[...,0]=1;before[...,8]=torch.tensor([2.,4.])
    after=before.clone();after[...,0]=0;after[...,7]=torch.tensor([10.,1.])
    ledger.append(before,after,torch.tensor([True]))
    value=ledger.export()[0]
    assert value['episode_profits']==[[8.,-3.]] and value['episode_listings']==[[0,1]]


def test_compiled_episode_offsets_are_dynamic():
    from research.vectorized_backtest.v18.episodes import Episodes
    ledger=Episodes(1,1,4,'cpu',max_listings=2)
    before=torch.zeros(1,1,2,9,dtype=torch.float64);before[...,0]=1
    after=before.clone();after[...,0]=0;after[...,7]=torch.tensor([2.,3.])
    compiled=torch.compile(ledger.append,backend='eager',fullgraph=True)
    compiled(before,after,torch.tensor([True]));compiled(before,after,torch.tensor([True]))
    assert ledger.export()[0]['episode_profits']==[[2.,3.,2.,3.]]




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
    from research.vectorized_backtest.v18.terminal import render
    from research.vectorized_backtest.v18.run_search import ranking
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
    from research.vectorized_backtest.v18.run_search import ranking
    rng=np.random.default_rng(2);population=sample(rng,1);ped=Pedigree();ids=ped.birth(population,1,[(-1,-1)],['fresh'])
    _,rows=ranking([metrics([[]])],population,ids,ped,Objective(),selection_required=False)
    assert rows[0]['metrics']['closed_positions']==0 and not rows[0]['valid']


def test_validation_cannot_change_successors(monkeypatch,tmp_path):
    import research.vectorized_backtest.v18.run_search as search
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
        inputs='unused',history='unused',indicator_root='unused',enriched_root='unused',receipts={'day':'bound'}))
    for suffix,multiplier in [('a',1),('b',-100)]:
        validation_multiplier[0]=multiplier
        search.run('train','validation',tmp_path/suffix,population_size=10,generations=2,batch_size=4,device='cpu',backend='eager',capture_gib=8.)
    assert json.loads((tmp_path/'a'/'checkpoint.json').read_text())['contract']['capture_gib']==8.
    first=json.loads((tmp_path/'a'/'checkpoint.json').read_text())['state']
    second=json.loads((tmp_path/'b'/'checkpoint.json').read_text())['state']
    for key in ('population','ids','pedigree','rng'):assert first[key]==second[key]
    assert first['validation_ranking']!=second['validation_ranking']
    from research.vectorized_backtest.v18.wandb_metrics import packet
    summary=packet(tmp_path/'a',1)
    assert summary['generation']==1 and 'validation/score' in summary
    from research.vectorized_backtest.v18.ranking_display import RankingObserver,MODES
    write_json(tmp_path/'a/status.json',dict(validation_feedback=False,training_ranking_generation=2,validation_generation=2))
    observer=RankingObserver(tmp_path/'a')
    for split in ('training','validation'):
        for mode in MODES:
            observer.set_view(split,mode);observer.refresh()
            snapshot=observer.read()
            assert snapshot['_display_split']==split and snapshot['_display_sort']==mode
            assert len(snapshot['_display_rows'])>0
            from research.vectorized_backtest.v18.terminal import render
            for width,height in ((110,38),(310,85)):
                output=StringIO();console=Console(file=output,width=width,height=height,color_system=None)
                console.print(render(snapshot,width=width,height=height,view='validation' if split=='validation' else 'financial'))
                assert 'sort:' in output.getvalue() and max(map(len,output.getvalue().splitlines()))<=width
    assert all(r['operation']=='fresh' for r in first['pedigree'][:10])
    assert all('v7_source' not in r for r in first['pedigree'])
    assert (tmp_path/'a/initialization.json').is_file()
    # Completed observations remain integrity checked on resume, without selection feedback.
    assert search.run('train','validation',tmp_path/'a',population_size=10,generations=2,batch_size=4,device='cpu',backend='eager',capture_gib=8.)==0
    receipt=tmp_path/'a/validation/generation-0001/2026-09-01/receipt.json'
    receipt.write_text('{}',encoding='utf-8')
    with pytest.raises(ValueError,match='Sealed validation receipt changed'):
        search.run('train','validation',tmp_path/'a',population_size=10,generations=2,batch_size=4,device='cpu',backend='eager',capture_gib=8.)


def test_shared_rule_groups_expand_exactly():
    from research.vectorized_backtest.v18.rule_plan import RulePlan
    from research.vectorized_backtest.v18.features import CATALOG
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
    from research.vectorized_backtest.v18.input_authority import authorize_day
    from research.vectorized_backtest.v6.torch_backtest.runtime import write_json,file_hash
    sessions=tmp_path/'sessions.json';authorization=tmp_path/'authorization.json'
    write_json(sessions,dict(training=[dict(day='2026-08-01')],validation=[dict(day='2026-09-01')],validation_mode='display_only'))
    write_json(authorization,dict(version='v8-validation-display-authorization-v1',sessions_sha256=file_hash(sessions),selection_feedback=False))
    assert authorize_day(sessions,'2026-09-01',authorization)['day']=='2026-09-01'
    with pytest.raises(ValueError,match='explicitly'):authorize_day(sessions,'2026-08-01',authorization)


def test_real_session_worker_reuses_exact_results(tmp_path):
    from test_vector_v7_lightweight import fixture_files
    from research.vectorized_backtest.v18.materialize_history import prepare
    from research.vectorized_backtest.v18.session_lanes import Worker
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
    from research.vectorized_backtest.v18.materialize_history import prepare
    from research.vectorized_backtest.v18.data import SessionData
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
    from research.vectorized_backtest.v18.data import SessionData
    from research.vectorized_backtest.v7.rule_plan import RulePlan as Original
    from research.vectorized_backtest.v18.rule_plan import RulePlan as Optimized
    root,history=fixture_files(tmp_path);data=SessionData(root,history)
    from research.vectorized_backtest.v15.genome import sample as old_sample
    members=old_sample(np.random.default_rng(128),12);members=members+members+members[:4]
    left=Original(members,'cpu');right=Optimized(members,'cpu')
    try:
        expected=left.evaluate(data,members,17,1,.1)
        actual=right.evaluate(data,members,17,1,.1)
        torch.testing.assert_close(actual,expected,rtol=0,atol=0)
    finally:left.close();right.close();data.close()


def test_episode_compact_export_preserves_empty_and_ragged_candidates():
    from research.vectorized_backtest.v18.episodes import Episodes
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


def test_random_only_unique_reproducible_initialization():
    from research.vectorized_backtest.v18.initial_population import initialize
    a,report=initialize(np.random.default_rng(199),1024)
    b,other=initialize(np.random.default_rng(199),1024)
    assert report==other and [m.payload() for m in a]==[m.payload() for m in b]
    assert report['seed_count']==0 and report['random_count']==report['unique_count']==1024
    assert report['past_results_loaded'] is False
    from research.vectorized_backtest.v18.run_search import run
    with pytest.raises(ValueError,match='forbids imported'):
        run('unused','unused','unused',initial_population='past-checkpoint.json')


@pytest.mark.parametrize('budget',[0.,17.,float('nan'),float('inf')])
def test_search_rejects_unbounded_capture_cache_before_opening_data(budget):
    from research.vectorized_backtest.v18.run_search import run
    with pytest.raises(ValueError,match='Capture cache'):
        run('unused','unused','unused',capture_gib=budget)
