"""V20 CPU contracts. No GPU profiling or optimizer campaign is started."""
import json
from dataclasses import asdict,replace
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pytest
from research.vectorized_backtest.v20 import archive,local_runner,completion_gate,phase2
from research.vectorized_backtest.v20.genome import sample,Individual,STAGES
from research.vectorized_backtest.v20.lineage import Pedigree,Selection,next_population,program_key,MateIndex
from research.vectorized_backtest.v20.run_search import digest,source_hash
from research.vectorized_backtest.v20.objectives import Objective,score,local_panel
from research.vectorized_backtest.v20.evaluator import Execution
from research.vectorized_backtest.v6.torch_backtest.runtime import write_json,file_hash
from test_vector_v17_regressions import metrics


def test_v19_cost_and_risk_arithmetic_unchanged():
    from research.vectorized_backtest.v19.objectives import score as reference
    ordered=[metrics([[10.,15.,20.],[-1.,-3.,-2.],[100.,0.,-5.]]) for _ in range(24)]
    actual=score(ordered,[0]*3);expected=reference(ordered,[0]*3)
    def equal(a,b):
        if isinstance(a,dict):
            assert set(a)==set(b)
            for k in a:equal(a[k],b[k])
        else:np.testing.assert_array_equal(a,b)
    equal(actual,expected)
    local=local_panel([ordered[0]],Objective())
    assert not local['other_loss'].any()
    assert local['eligible'].tolist()==[[True,False,True]]


def test_global_only_selection_ignores_local_champions():
    rng=np.random.default_rng(20);members=sample(rng,32);p=Pedigree();ids=p.birth(members,1,[(-1,-1)]*32,['fresh']*32)
    offspring,newids,report=next_population(rng,members,ids,p,list(range(32)),2,Selection(global_fraction=1,champion_partners=0),
        session_values=np.ones((24,32)),session_eligible=np.ones((24,32),bool),global_values=np.arange(32.),global_eligible=np.zeros(32,bool))
    assert len(offspring)==32 and report['fresh_candidates']==32
    assert report['local_slots']==report['local_champions']==report['champion_crossovers']==0
    assert len({program_key(m) for m in offspring})==32


def test_archive_distance_selects_novelty_and_canonical_dedup():
    records=[dict(canonical_sha256=str(i),serial=i,score=100-i,descriptor=[0.,0.]) for i in range(10)]
    records[-1]['descriptor']=[10.,10.]
    records.append(dict(records[0],serial=12,score=99))
    selected=archive.select_archive(records,capacity=2,shortlist=10,quality_weight=.1)
    assert [r['canonical_sha256'] for r in selected]==['0','9']
    assert selected==archive.select_archive(records,capacity=2,shortlist=10,quality_weight=.1)
    with pytest.raises(ValueError):archive.select_archive(records,capacity=100,shortlist=10)


def test_disabled_genes_do_not_create_parameter_distance():
    m=sample(np.random.default_rng(10),1)[0]
    from research.vectorized_backtest.v20.program import Node,Program,Op
    from research.vectorized_backtest.v20.genome import assemble,effective_rules
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
    FakeWorker.stop_once=True
    assert local_runner.run_session(local_config,'2026-08-01',resumed,**options)['completed']==1
    (resumed/'STOP').unlink()
    assert local_runner.run_session(local_config,'2026-08-01',resumed,**options)['status']=='complete'
    a=json.loads((full/'checkpoint.json').read_bytes())['state'];b=json.loads((resumed/'checkpoint.json').read_bytes())['state']
    for key in ('population','ids','pedigree','next_serial','rng','archive'):assert a[key]==b[key]
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
    from research.vectorized_backtest.v20 import run_train
    job=predecessor_fixture(tmp_path/'v19',completed=5)
    monkeypatch.setattr(run_train,'ROOT',tmp_path)
    monkeypatch.setattr(run_train,'local_phase',lambda *a,**k:pytest.fail('Local compute started while V19 incomplete'))
    args=SimpleNamespace(output=tmp_path/'waiting',predecessor_job=job,check_only=True,poll_seconds=30)
    assert run_train.execute(args)==0
    out=capsys.readouterr().out
    assert 'V20 waiting' in out and 'V19 5/50' in out and '\x1b' not in out
    assert not (args.output/'active.json').exists() and not (args.output/'manifest.json').exists()


def test_phase2_and_local_runner_block_direct_early_calls(tmp_path):
    with pytest.raises(ValueError,match='current V19 job'):phase2.run_global(None,None,None,tmp_path)
    with pytest.raises(ValueError,match='current V19 job'):local_runner.run_session({},'2026-08-01',tmp_path)


def test_financial_cpu_transition_matches_v19():
    import torch
    from research.vectorized_backtest.v20.evaluator import step
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
    from research.vectorized_backtest.v20 import session_lanes as lanes
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
        worker.run(day,None,payload,1,token,1);worker.run(day,None,payload,2,token,1)
        first=json.loads((tmp_path/'worker'/'generation-0001'/day/'receipt.json').read_bytes())
        second=json.loads((tmp_path/'worker'/'generation-0002'/day/'receipt.json').read_bytes())
        assert worker.loads==1 and second['reused_candidates']==3 and second['evaluated_candidates']==0
        assert first['metrics']['net_pnl']==second['metrics']['net_pnl']
        assert all(second['metrics']['terminal_valid']) and all(x<=300+1e-7 for x in second['metrics']['maximum_position_risk'])
        assert first['validation_opened']==second['validation_opened']==False
    finally:worker.close()


def test_lightweight_deferred_queue_does_not_import_torch_or_launch(tmp_path,monkeypatch,capsys):
    import sys
    from research.vectorized_backtest.v20 import run_deferred
    job=predecessor_fixture(tmp_path/'v19',completed=5);queue=tmp_path/'queue';queue.mkdir()
    record=dict(predecessor_job=str(job),mode='profile',code=str(Path(run_deferred.__file__).resolve().parents[3]))
    write_json(queue/'job.json',record)
    monkeypatch.setattr(sys,'argv',['deferred','--job',str(queue/'job.json'),'--once'])
    assert run_deferred.main()==0
    assert json.loads((queue/'status.json').read_bytes())['status']=='waiting'
    assert 'no V20 compute started' in capsys.readouterr().out
    assert json.loads((queue/'waiting-exit.json').read_bytes())['exit_code']==0


def test_global_parent_routes_and_mutation_remain_active():
    rng=np.random.default_rng(2020);members=sample(rng,64);p=Pedigree();ids=p.birth(members,1,[(-1,-1)]*64,['fresh']*64)
    result,newids,report=next_population(rng,members,ids,p,list(range(64)),2,Selection(global_fraction=1.,champion_partners=0),
        session_values=np.zeros((24,64)),session_eligible=np.zeros((24,64),bool),global_values=np.arange(64.),global_eligible=np.ones(64,bool))
    assert report['crossovers']>0 and report['mutations']>0 and report['global_elites']==6
    assert report['local_slots']==report['local_champions']==0
    assert all(r.get('path','global')=='global' for r in p.records if r['generation']==2)
    assert len({program_key(m) for m in result})==64


def test_v20_preserves_copied_financial_and_feature_sources():
    root=Path(__file__).resolve().parents[1]/'research'/'vectorized_backtest'
    for name in ('evaluator.py','protected_transition.py','sparse_replay.py','replay_graph.py','features.py','feature_gather.py',
                 'genome.py','program.py','variation.py','semantics.py','objectives.py','enriched_math.py','indicator_math.py','indicator_identity.py'):
        old=(root/'v19'/name).read_bytes().replace(b'\r\n',b'\n').replace(b'research.vectorized_backtest.v19',b'research.vectorized_backtest.v20')
        new=(root/'v20'/name).read_bytes().replace(b'\r\n',b'\n')
        assert old==new,name


def test_legacy_optimizer_pointer_uses_campaign_process_authority(tmp_path,monkeypatch):
    campaign=tmp_path/'legacy';campaign.mkdir();identity=dict(pid=123,creation_time=456)
    write_json(campaign/'active.json',identity)
    assert completion_gate.process_identity(dict(campaign=str(campaign)))==identity
    (campaign/'owner.lock').touch()
    with pytest.raises(RuntimeError,match='owns'):completion_gate.process_identity(dict(campaign=str(campaign)))
    with pytest.raises(ValueError,match='authority'):completion_gate.process_identity({})
