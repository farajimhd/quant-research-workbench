"""V21 CPU contracts. No GPU profiling or optimizer campaign is started."""
import json
from dataclasses import asdict,replace
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pytest
from research.vectorized_backtest.v21 import archive,local_runner,completion_gate,phase2
from research.vectorized_backtest.v21.genome import sample,Individual,STAGES
from research.vectorized_backtest.v21.lineage import Pedigree,Selection,next_population,program_key,MateIndex
from research.vectorized_backtest.v21.run_search import digest,source_hash
from research.vectorized_backtest.v21.objectives import Objective,score,local_panel
from research.vectorized_backtest.v21.evaluator import Execution
from research.vectorized_backtest.v6.torch_backtest.runtime import write_json,file_hash
from test_vector_v17_regressions import metrics as old_metrics

def metrics(profits):
    m=old_metrics(profits);n=len(profits)
    m.update(ticker_pnl=[{str(i):float(v) for i,v in enumerate(row)} for row in profits],maximum_position_risk=[0.]*n,maximum_stop_distance=[0.]*n)
    return m


def test_scores_keep_full_ticker_profit_losses_and_score_only_eligibility():
    one=metrics([[1000.,-100.],[],[-50.]])
    one['drawdown']=[40.,0.,10.]
    result=score([one],[100,0,5])
    # Two traded tickers: fractionally remove .2 of the winner, no trade trim.
    np.testing.assert_allclose(result['score'],[848.,0.,-50.5])
    assert result['global_eligible'].tolist()==[True,True,True]
    np.testing.assert_array_equal(result['score'],result['_local']['values'][0])
    # Trade-ledger rearrangement cannot affect this objective.
    one['episode_profits']=[[900.,100.,-100.],[],[-20.,-30.]]
    np.testing.assert_array_equal(score([one],[0,0,0])['score'],result['score'])
    one['maximum_position_risk'][0]=301
    assert not score([one],[0,0,0])['global_eligible'][0]


def test_global_concentration_uses_maximum_not_double_penalty():
    first=metrics([[1000.]]);second=metrics([[-100.]])
    first['drawdown']=[0.];second['drawdown']=[0.]
    result=score([first,second],[200])
    # Session trim .2*1000 beats ticker trim .1*1000. Loss remains intact.
    assert result['score'][0]==850.
    assert result['components']['concentration_penalty'][0]==50.


def test_archive_profit_bands_and_history_bound():
    records=[dict(canonical_sha256=f'{i:064x}',serial=i,net_profit=1000.-i,score=-i) for i in range(1000)]
    selected=archive.select_archive(records,64,1024,seed=42)
    serials=np.asarray([r['serial'] for r in selected])
    assert np.histogram(serials,bins=[0,100,300,600,1000])[0].tolist()==[32,16,10,6]
    assert 0 in serials and len(set(serials))==64
    assert selected==archive.select_archive(records,64,1024,seed=42)
    retained=archive.reservoir(records,100)
    assert len(retained)==100 and retained[0]['serial']==0


def test_expanded_parent_pool_shrinks_and_mutates_fresh_candidates():
    rng=np.random.default_rng(2121);members=sample(rng,96);p=Pedigree();ids=p.birth(members,1,[(-1,-1)]*96,['fresh']*96)
    children,newids,report=next_population(rng,members,ids,p,[],2,Selection(mutation_min=1.,mutation_max=1.),
        session_values=None,session_eligible=None,global_values=np.arange(96.)-100,global_eligible=np.ones(96,bool),target_size=16)
    assert len(children)==16 and newids[0]==95 and report['source_population']==96
    assert report['mutation_eligible']==report['mutations']==15
    assert any(r['operation']=='fresh_mutation' for r in p.records[96:])
    for r in p.records[96:]:
        a,b=r['parents']
        if a>=0:assert not p.relatives(a,[b],2)[0]


def test_validation_shortlist_is_training_only_score_and_profit():
    from research.vectorized_backtest.v21.run_search import validation_shortlist
    chosen=validation_shortlist(np.arange(10.),-np.arange(10.),np.ones(10,bool),np.arange(10),6)
    assert set(chosen)=={9,8,7,0,1,2}


def test_sampling_reports_size_coverage_and_twenty_adds():
    from research.vectorized_backtest.v21.initial_population import initialize
    members,report=initialize(np.random.default_rng(2121),128)
    assert max(m.policy.maximum_adds for m in members)==20
    assert min(m.policy.maximum_adds for m in members)==0
    assert report['coverage']['features'] and report['coverage']['windows']
    assert report['sampling'] and report['unique_count']==128
    sizes=list(map(int,report['coverage']['effective_sizes']))
    assert any(3<=s<=9 for s in sizes) and any(10<=s<=19 for s in sizes) and any(20<=s<=32 for s in sizes)


def test_temporal_ratio_is_uncentered_and_typed():
    from research.vectorized_backtest.v21.expressions import temporal_ratio
    from research.vectorized_backtest.v21.features import CATALOG
    from research.vectorized_backtest.v21.program import Op
    index=next(i for i,f in enumerate(CATALOG) if f.name=='market.close')
    rule=temporal_ratio(np.random.default_rng(1),index,5)
    assert [n.op for n in rule.nodes]==[Op.FEATURE,Op.LAG,Op.DIVIDE]
    assert rule.validate(CATALOG,output_unit='ratio')['units'][-1]=='ratio'


def test_launch_requires_new_explicit_authorization(monkeypatch,tmp_path):
    import sys
    from research.vectorized_backtest.v21 import run_train
    output=tmp_path/'not-launched'
    monkeypatch.setattr(sys,'argv',['run_train','--training','unused','--validation','unused','--output',str(output),
        '--predecessor-job','unused','--commit','test'])
    with pytest.raises(SystemExit) as result:run_train.main()
    assert result.value.code==2 and not output.exists()


def test_movement_projection_changes_only_magnitude_semantics():
    import torch
    from research.vectorized_backtest.v21.resident_features import ResidentFeatures
    from research.vectorized_backtest.v7.resident_features import ResidentFeatures as OldResident
    from research.vectorized_backtest.v21.features import CATALOG
    obj=object.__new__(ResidentFeatures);obj.device=torch.device('cpu');obj.column_plans={}
    history=torch.zeros((2,332),dtype=torch.float32);history[:,180]=torch.tensor([.5,1.]);history[:,0]=9.
    obj.tensors=dict(history=history,feature_row=torch.tensor([0,1]),mark=torch.tensor([10.,20.],dtype=torch.float64))
    columns=[next(i for i,f in enumerate(CATALOG) if f.name.startswith('history.average_move.') and f.name.endswith('fraction_of_close')),
             next(i for i,f in enumerate(CATALOG) if f.name.startswith('history.recent_high.') and f.name.endswith('relative_to_close'))]
    rows=torch.tensor([[0,1,-1]]);ids=torch.tensor([[0,1,0]]);clock=torch.zeros_like(rows)
    old,oldmask=OldResident._values(obj,rows,ids,clock,columns)
    actual,mask=obj._values_reference(rows,ids,clock,columns)
    torch.testing.assert_close(actual[0,:2,0],torch.tensor([.05,.05]),rtol=0,atol=0)
    torch.testing.assert_close(actual[...,1],old[...,1],rtol=0,atol=0)
    assert torch.equal(mask,oldmask) and not mask[0,2].any()


def test_global_only_selection_ignores_local_champions():
    rng=np.random.default_rng(20);members=sample(rng,32);p=Pedigree();ids=p.birth(members,1,[(-1,-1)]*32,['fresh']*32)
    offspring,newids,report=next_population(rng,members,ids,p,list(range(32)),2,Selection(global_fraction=1,champion_partners=0),
        session_values=np.ones((24,32)),session_eligible=np.ones((24,32),bool),global_values=np.arange(32.),global_eligible=np.zeros(32,bool))
    assert len(offspring)==32 and report['fresh_candidates']==32
    assert report['local_slots']==report['local_champions']==report['champion_crossovers']==0
    assert len({program_key(m) for m in offspring})==32


def test_disabled_genes_do_not_create_parameter_distance():
    m=sample(np.random.default_rng(10),1)[0]
    from research.vectorized_backtest.v21.program import Node,Program,Op
    from research.vectorized_backtest.v21.genome import assemble,effective_rules
    rules=effective_rules(m);rules['add']=Program((Node(Op.CONSTANT,value=0,unit='bool'),),0)
    one=assemble(rules,replace(m.policy,maximum_adds=0,add_minimum_profit=.01),{})
    two=replace(one,policy=replace(one.policy,maximum_adds=0,add_minimum_profit=.9))
    assert program_key(one)==program_key(two)
    np.testing.assert_array_equal(archive.descriptor(one),archive.descriptor(two))


def test_round_robin_archives_share_global_slots():
    r=lambda key:dict(canonical_sha256=key)
    selected=archive.balanced_experts({'2026-08-02':[r('a'),r('b')],'2026-08-01':[r('a'),r('c'),r('d')]},4)
    assert [x['canonical_sha256'] for x in selected]==['a','b','c','d']


class FakeWorker:
    instances=[];stop_once=False
    def __init__(self,config,events,lane):
        self.config=config;self.loads=0;self.uploads=0;self.item=None;self.closed=False;self.instances.append(self)
    def load(self,day):
        if self.item is None:
            self.loads+=1;self.uploads+=1
            self.item=SimpleNamespace(identity=dict(input_sha256='input',history_sha256='history',indicator_sha256='indicator',enriched_sha256='enriched'),listing_ids=[0])
        return self.item
    def run(self,day,next_day,payload,generation,token,width):
        assert next_day is None and self.config['retain_session_residency']
        profits=[[10+int(program_key(Individual.restore(p))[:4],16)%50,20.,30.] for p in payload]
        folder=Path(self.config['output'])/f'generation-{generation:04d}'/day;folder.mkdir(parents=True,exist_ok=True)
        write_json(folder/'receipt.json',dict(population_sha256=token,metrics=metrics(profits),full_session=True,execution=self.config['execution'],validation_opened=False))
        if self.stop_once:
            (Path(self.config['output'])/'STOP').touch();FakeWorker.stop_once=False
        return dict(wall_seconds=.01)
    def close(self):self.closed=True


@pytest.fixture
def local_config(monkeypatch):
    monkeypatch.setattr(completion_gate,'require_complete',lambda *args: {'status':'ready'})
    FakeWorker.instances=[];FakeWorker.stop_once=False
    return dict(source_sha256=source_hash(),predecessor_job='test-only',inputs='input-root',history='history-root',indicator_root='indicator-root',enriched_root='enriched-root',
                input_receipts={'2026-08-01':{'inputs':'input','history':'history'}},indicator_receipts={'2026-08-01':'indicator'},enriched_receipts={'2026-08-01':'enriched'},
                execution=asdict(Execution(fee_per_share=.005,minimum_order_fee=1)),backend='eager',device='cpu',batch_size=16)


def test_local_session_restart_preserves_rng_population_archive_and_load_once(tmp_path,local_config):
    options=dict(generations=3,population_size=16,seed=20,archive_capacity=8,archive_shortlist=16,worker_factory=FakeWorker)
    full=tmp_path/'full';resumed=tmp_path/'resumed'
    assert local_runner.run_session(local_config,'2026-08-01',full,**options)['status']=='complete'
    assert FakeWorker.instances[-1].loads==1 and FakeWorker.instances[-1].uploads==1
    assert FakeWorker.instances[-1].config['plan_limit']==1
    FakeWorker.stop_once=True
    assert local_runner.run_session(local_config,'2026-08-01',resumed,**options)['completed']==1
    (resumed/'STOP').unlink()
    assert local_runner.run_session(local_config,'2026-08-01',resumed,**options)['status']=='complete'
    a=json.loads((full/'checkpoint.json').read_bytes())['state'];b=json.loads((resumed/'checkpoint.json').read_bytes())['state']
    for key in ('population','ids','pedigree','next_serial','rng','archive','archive_pool'):assert a[key]==b[key]
    assert all(w.closed for w in FakeWorker.instances)
    assert len(a['pedigree'])==16 and len(a['archive'])<=8
    assert json.loads((full/'complete.json').read_bytes())['validation_opened'] is False
    record=json.loads((full/'generation-0003'/'complete.json').read_bytes())
    assert record['cumulative_host_loads']==record['cumulative_device_uploads']==1


def test_local_rejects_changed_seal_and_source(tmp_path,local_config):
    root=tmp_path/'local';options=dict(generations=1,population_size=16,archive_capacity=8,archive_shortlist=16,worker_factory=FakeWorker)
    local_runner.run_session(local_config,'2026-08-01',root,**options)
    (root/'generation-0001'/'scores.json').write_text('{}')
    with pytest.raises(ValueError,match='sealed evidence'):local_runner.run_session(local_config,'2026-08-01',root,**options)
    with pytest.raises(ValueError,match='source changed'):local_runner.run_session(dict(local_config,source_sha256='changed'),'2026-08-01',tmp_path/'bad',**options)


def test_sealed_archives_seed_pool_and_source_ancestry(tmp_path,local_config):
    root=tmp_path/'local';day='2026-08-01'
    local_runner.run_session(local_config,day,root/day,generations=2,population_size=16,archive_capacity=8,archive_shortlist=16,worker_factory=FakeWorker)
    pool=phase2.seed_pool(root,[day],population_size=16)
    phase2.verify_pool(pool,[day]);assert pool['expert_count']<=12 and pool['fresh_count']>=4
    p=Pedigree();members=[Individual.restore(v) for v in pool['population']];ids=p.birth(members,1,[(-1,-1)]*16,['fresh']*16)
    phase2.attach_source_ancestry(p,pool['sources'])
    assert all(a<0 for r in p.records for a,_ in r['ancestors'])
    a=next(i for i,s in enumerate(pool['sources']) if s)
    assert not MateIndex(p,ids).unrelated(a)[a]
    broken=dict(pool,expert_count=15);broken['pool_sha256']=digest({k:v for k,v in broken.items() if k!='pool_sha256'})
    with pytest.raises(ValueError,match='counts'):phase2.verify_pool(broken,[day])
    (root/day/'archive.json').write_text('[]')
    with pytest.raises(ValueError,match='seal'):phase2.verify_pool(pool,[day])


def predecessor_fixture(root,completed=50,exit_code=0):
    root.mkdir(parents=True);job=root.parent/'job.json';source='v19-test-source';days=['2026-08-01']
    state=dict(completed=completed);contract=dict(version='v19-test-fixture',generations=50,source_sha256=source,training=dict(days=days),validation=dict(days=days))
    write_json(root/'checkpoint.json',dict(contract=contract,state=state,state_sha256=digest(state)))
    write_json(root/'exit.json',dict(exit_code=exit_code));write_json(root/'active.json',dict(pid=1,creation_time=0,source_sha256=source))
    write_json(root/'status.json',dict(status='complete',completed_generations=completed));write_json(job,dict(campaign=str(root),source_sha256=source))
    for role in ('training','validation'):
        folder=root/role/'generation-0050';(folder/days[0]).mkdir(parents=True)
        write_json(folder/days[0]/'receipt.json',dict(test='fixture'))
        (folder/'session-panel.npz').write_bytes(b'fixture')
        write_json(folder/'complete.json',dict(validation_feedback=False,session_receipts={days[0]:file_hash(folder/days[0]/'receipt.json')},session_panel_sha256=file_hash(folder/'session-panel.npz')))
    return job


@pytest.mark.parametrize('completed,exit_code',[(5,0),(49,0),(50,130),(50,1)])
def test_predecessor_gate_rejects_partial_or_failed_runs(tmp_path,monkeypatch,completed,exit_code):
    monkeypatch.setattr(completion_gate,'alive',lambda owner:False)
    job=predecessor_fixture(tmp_path/'v19',completed,exit_code)
    assert completion_gate.completion(job)['status']=='waiting'
    with pytest.raises(RuntimeError):completion_gate.require_complete(job)


def test_completed_gate_binds_proof_and_rejects_tampering(tmp_path,monkeypatch):
    monkeypatch.setattr(completion_gate,'alive',lambda owner:False)
    job=predecessor_fixture(tmp_path/'v19');proof=completion_gate.require_complete(job)
    assert completion_gate.require_complete(job,proof)==proof
    (tmp_path/'v19'/'owner.lock').touch()
    with pytest.raises(RuntimeError):completion_gate.require_complete(job,proof)
    (tmp_path/'v19'/'owner.lock').unlink()
    (tmp_path/'v19'/'training'/'generation-0050'/'2026-08-01'/'receipt.json').write_text('{}')
    with pytest.raises(ValueError,match='receipt changed'):completion_gate.completion(job)


def test_waiting_launcher_has_no_compute_side_effects(tmp_path,monkeypatch,capsys):
    from research.vectorized_backtest.v21 import run_train
    job=predecessor_fixture(tmp_path/'v19',completed=5)
    monkeypatch.setattr(run_train,'ROOT',tmp_path)
    monkeypatch.setattr(run_train,'local_phase',lambda *a,**k:pytest.fail('Local compute started while V19 incomplete'))
    args=SimpleNamespace(output=tmp_path/'waiting',predecessor_job=job,check_only=True,poll_seconds=30)
    assert run_train.execute(args)==0
    out=capsys.readouterr().out
    assert 'V21 waiting' in out and 'V19 5/50' in out and '\x1b' not in out
    assert not (args.output/'active.json').exists() and not (args.output/'manifest.json').exists()


def test_phase2_and_local_runner_block_direct_early_calls(tmp_path):
    with pytest.raises(ValueError,match='current V19 job'):phase2.run_global(None,None,None,tmp_path)
    with pytest.raises(ValueError,match='current V19 job'):local_runner.run_session({},'2026-08-01',tmp_path)


def test_financial_cpu_transition_matches_v19():
    import torch
    from research.vectorized_backtest.v21.evaluator import step
    from research.vectorized_backtest.v19.evaluator import step as previous
    p=torch.zeros((1,4,2,9),dtype=torch.float64);a=torch.zeros((1,4,16),dtype=torch.float64);a[...,8]=float('inf')
    oldp=p.clone();olda=a.clone();price=torch.full((1,1,2),10.,dtype=torch.float64);flags=price>0
    policy=torch.tensor([.15,.05,.25,.10,.01,.02,5.,1.,0.],dtype=torch.float64).expand(1,4,2,9)
    for clock in range(1,10):
        previous_price=price;price=price*1.01;signals=torch.full((1,4,2),1 if clock==1 else 4,dtype=torch.uint8)
        args=(price,previous_price,flags,flags,signals,price*.85,policy,torch.tensor(float(clock)),torch.full((1,1,1),clock==9),1000.,200.,0.,.005,1.,True,.20,300.)
        p,a=step(p,a,*args);oldp,olda=previous(oldp,olda,*args)
        torch.testing.assert_close(p,oldp,rtol=0,atol=0);torch.testing.assert_close(a,olda,rtol=0,atol=0)
    assert torch.count_nonzero(p[...,0])==0


def test_real_session_worker_cpu_replay_publication_and_result_reuse(tmp_path,monkeypatch):
    """Exercise the real worker/replay/publisher; only fixture loading/rules stubbed."""
    import torch,queue
    from research.vectorized_backtest.v21 import session_lanes as lanes
    from research.vectorized_backtest.v7.qualify import synthetic
    day='2026-08-01';data=synthetic('cpu');data.listing_ids=np.array([0]);data.identity={'input_sha256':'fixture'}
    data.receipt=dict(validation_opened=False,listings=[dict(listing_id='fixture-listing')])
    data.activate=lambda device:None;data.deactivate=lambda:None;data.close=lambda:None
    monkeypatch.setattr(lanes,'SessionData',lambda *a,**kw:data)
    monkeypatch.setattr(lanes,'prepare_host',lambda data,**kw:data)
    class Programs:
        def __init__(self,members,device):self.members=members;self.plan=None
        def evaluate(self,item,**kw):
            gates=torch.zeros((len(self.members),item.clocks,1),dtype=torch.uint8);gates[:,0]=1;return gates
        def close(self):pass
    monkeypatch.setattr(lanes,'PopulationPrograms',Programs)
    config=dict(inputs='fixture',history='fixture',feature_cache=None,indicator_root=None,enriched_root=None,output=str(tmp_path/'worker'),
                maximum_host_sessions=2,maximum_data_gib=4,maximum_gate_gib=8,device='cpu',backend='eager',batch_size=4,
                source_sha256=source_hash(),split='training',execution=asdict(Execution(fee_per_share=.005,minimum_order_fee=1)),
                episode_capacity=16,retain_session_residency=True)
    members=[replace(m,policy=replace(m.policy,swing_left=0,swing_right=0,stop_fraction=.1)) for m in sample(np.random.default_rng(20),3)]
    payload=[m.payload() for m in members];token=digest(payload);worker=lanes.Worker(config,queue.Queue(maxsize=1),0)
    try:
        worker.run(day,None,payload,1,token,1)
        original_read=Path.read_text
        def guarded_read(path,*args,**kwargs):
            assert path.parent.name!='result-cache','Resident generation reread its disk cache'
            return original_read(path,*args,**kwargs)
        with monkeypatch.context() as patch:
            patch.setattr(Path,'read_text',guarded_read)
            worker.run(day,None,payload,2,token,1)
        first=json.loads((tmp_path/'worker'/'generation-0001'/day/'receipt.json').read_bytes())
        second=json.loads((tmp_path/'worker'/'generation-0002'/day/'receipt.json').read_bytes())
        assert worker.loads==1 and second['reused_candidates']==3 and second['evaluated_candidates']==0
        assert first['metrics']['net_pnl']==second['metrics']['net_pnl']
        assert all(second['metrics']['terminal_valid']) and all(x<=300+1e-7 for x in second['metrics']['maximum_position_risk'])
        assert first['validation_opened']==second['validation_opened']==False
        assert local_panel([first['metrics']])['eligible'].all()
    finally:worker.close()
    # A fresh worker must revalidate the durable copy rather than trust memory.
    cache=tmp_path/'worker'/'result-cache'/f'{day}.json';saved=json.loads(cache.read_text());saved['results_sha256']='corrupt';write_json(cache,saved)
    worker=lanes.Worker(config,queue.Queue(maxsize=1),0)
    try:
        with pytest.raises(ValueError,match='integrity'):worker.run(day,None,payload,3,token,1)
    finally:worker.close()


def test_bounded_simplification_cache_preserves_literal_identity():
    from research.vectorized_backtest.v21.complexity import simplify,_simplify
    from research.vectorized_backtest.v21.program import Program,Node,Op
    _simplify.cache_clear()
    for value in (0,0.,-0.,1,1.):
        p=Program((Node(Op.CONSTANT,value=value,unit='bool'),),0)
        assert json.dumps(simplify(p).payload())==json.dumps(_simplify.__wrapped__(p,repr(p)).payload())
        assert simplify(p) is simplify(p)
    assert _simplify.cache_info().maxsize==8192 and _simplify.cache_info().hits>=10


def test_lightweight_deferred_queue_does_not_import_torch_or_launch(tmp_path,monkeypatch,capsys):
    import sys
    from research.vectorized_backtest.v21 import run_deferred
    job=predecessor_fixture(tmp_path/'v19',completed=5);queue=tmp_path/'queue';queue.mkdir()
    record=dict(predecessor_job=str(job),mode='profile',code=str(Path(run_deferred.__file__).resolve().parents[3]))
    write_json(queue/'job.json',record)
    monkeypatch.setattr(sys,'argv',['deferred','--job',str(queue/'job.json'),'--once'])
    assert run_deferred.main()==0
    assert json.loads((queue/'status.json').read_bytes())['status']=='waiting'
    assert 'no V21 compute started' in capsys.readouterr().out
    assert json.loads((queue/'waiting-exit.json').read_bytes())['exit_code']==0


def test_global_parent_routes_and_mutation_remain_active():
    rng=np.random.default_rng(2020);members=sample(rng,64);p=Pedigree();ids=p.birth(members,1,[(-1,-1)]*64,['fresh']*64)
    result,newids,report=next_population(rng,members,ids,p,list(range(64)),2,Selection(global_fraction=1.,champion_partners=0),
        session_values=np.zeros((24,64)),session_eligible=np.zeros((24,64),bool),global_values=np.arange(64.),global_eligible=np.ones(64,bool))
    assert report['crossovers']>0 and report['mutations']>0 and report['global_elites']==6
    assert report['local_slots']==report['local_champions']==0
    assert all(r.get('path','global')=='global' for r in p.records if r['generation']==2)
    assert len({program_key(m) for m in result})==64


def test_financial_kernels_and_certified_math_unchanged():
    root=Path(__file__).resolve().parents[1]/'research/vectorized_backtest'
    for name in ('evaluator.py','protected_transition.py','sparse_replay.py','replay_graph.py','program.py','enriched_math.py','indicator_math.py','indicator_identity.py'):
        old=(root/'v20'/name).read_bytes().replace(b'\r\n',b'\n').replace(b'v20',b'v21').replace(b'V20',b'V21')
        if name=='program.py':old=old.replace(b'MAX_NODES=32',b'MAX_NODES=128')
        assert (root/'v21'/name).read_bytes().replace(b'\r\n',b'\n')==old


def test_legacy_optimizer_pointer_uses_campaign_process_authority(tmp_path,monkeypatch):
    campaign=tmp_path/'legacy';campaign.mkdir();identity=dict(pid=123,creation_time=456)
    write_json(campaign/'active.json',identity)
    assert completion_gate.process_identity(dict(campaign=str(campaign)))==identity
    (campaign/'owner.lock').touch()
    with pytest.raises(RuntimeError,match='owns'):completion_gate.process_identity(dict(campaign=str(campaign)))
    with pytest.raises(ValueError,match='authority'):completion_gate.process_identity({})


def test_validation_cannot_change_successors(monkeypatch,tmp_path,local_config):
    from io import StringIO
    from rich.console import Console
    local_runner.run_session(local_config,"2026-08-01",tmp_path/"local"/"2026-08-01",generations=1,population_size=10,worker_factory=FakeWorker)
    pool=phase2.seed_pool(tmp_path/"local",["2026-08-01"],population_size=10)
    import research.vectorized_backtest.v21.run_search as search
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
        search.run('train','validation',tmp_path/suffix,population_size=10,generations=2,batch_size=4,device='cpu',backend='eager',capture_gib=8.,initial_population=pool)
    assert json.loads((tmp_path/'a'/'checkpoint.json').read_text())['contract']['capture_gib']==8.
    first=json.loads((tmp_path/'a'/'checkpoint.json').read_text())['state']
    second=json.loads((tmp_path/'b'/'checkpoint.json').read_text())['state']
    for key in ('population','ids','pedigree','rng'):assert first[key]==second[key]
    assert first['validation_ranking']!=second['validation_ranking']
    from research.vectorized_backtest.v21.wandb_metrics import packet
    summary=packet(tmp_path/'a',1)
    assert summary['generation']==1 and 'validation/score' in summary
    from research.vectorized_backtest.v21.ranking_display import RankingObserver,MODES
    write_json(tmp_path/'a/status.json',dict(validation_feedback=False,training_ranking_generation=2,validation_generation=2))
    observer=RankingObserver(tmp_path/'a')
    for split in ('training','validation'):
        for mode in MODES:
            observer.set_view(split,mode);observer.refresh()
            snapshot=observer.read()
            assert snapshot['_display_split']==split and snapshot['_display_sort']==mode
            assert len(snapshot['_display_rows'])>0
            from research.vectorized_backtest.v21.terminal import render
            for width,height in ((110,38),(310,85)):
                output=StringIO();console=Console(file=output,width=width,height=height,color_system=None)
                console.print(render(snapshot,width=width,height=height,view='validation' if split=='validation' else 'financial'))
                assert 'sort:' in output.getvalue() and max(map(len,output.getvalue().splitlines()))<=width
    assert all(r['operation']=='fresh' for r in first['pedigree'][:10])
    assert all('v7_source' not in r for r in first['pedigree'])
    assert (tmp_path/'a/initialization.json').is_file()
    # Completed observations remain integrity checked on resume, without selection feedback.
    assert search.run('train','validation',tmp_path/'a',population_size=10,generations=2,batch_size=4,device='cpu',backend='eager',capture_gib=8.,initial_population=pool)==0
    receipt=tmp_path/'a/validation/generation-0001/2026-09-01/receipt.json'
    receipt.write_text('{}',encoding='utf-8')
    with pytest.raises(ValueError,match='Sealed validation receipt changed'):
        search.run('train','validation',tmp_path/'a',population_size=10,generations=2,batch_size=4,device='cpu',backend='eager',capture_gib=8.,initial_population=pool)
