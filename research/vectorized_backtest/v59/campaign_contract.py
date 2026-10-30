"""Immutable 24/12, two-host session stage and workstation-only global stage."""
import json
from pathlib import Path
from .identity import source_hash,digest
from .evaluator import Execution
from research.vectorized_backtest.v42.distributed_assets import sha
from research.vectorized_backtest.v42.publication import write_json

def check_structure(job,train,valid):
    if job.get('validation_schedule') not in (None,'concurrent-training-only-v1'):raise ValueError('Unsupported validation schedule')
    if len(train)!=24 or len(set(train))!=24 or len(valid)!=12 or len(set(valid))!=12 or set(train)&set(valid) or max(train)>=min(valid):raise ValueError('Chronological disjoint certified 24/12 required')
    if job['training_days']!=train or job['validation_days']!=valid:raise ValueError('Split membership changed')
    if set(job['machines'])!={'laptop','workstation'}:raise ValueError('Both hosts required for session stage')
    assigned=[d for n in job['machines'] for d in job['machines'][n]['session_days']]
    if sorted(assigned)!=sorted(train) or len(assigned)!=len(set(assigned)):raise ValueError('Each training session needs exactly one owner')
    if any(not job['machines'][n]['session_days'] for n in job['machines']):raise ValueError('Both hosts must participate')
    if job['global_node']!='workstation' or job['global_session_workers']!=2:raise ValueError('Global replay requires two workstation session lanes')
    if (job['phase1_generations'],job['phase2_generations'],job['population'],job['initial_multiplier'],job['seed'],job['batch_size'])!=(50,50,1024,6,2021,512):raise ValueError('Approved 50/50 campaign dimensions changed')
    if job['ticker_parent_fraction']!=.25 or job['selection_risk_caps'] is not False:raise ValueError('V59 selection contract changed')
    if job['objective']!=dict(drawdown_weight=1.,loss_weight=1.):raise ValueError('V59 cost changed')
    e=Execution(**job['execution']).validate()
    if e.risk_limits_enabled or not e.risk_protection:raise ValueError('Admission limits off; financial diagnostics retained')
    if job.get('global_restart') and (job.get('immigration_policy')!='score75-net25-v1' or job.get('resume_transition',{}).get('global_checkpoint')):raise ValueError('Global restart must use a fresh score/profit pool')
    if job.get('immigration_policy','score-only') not in ('score-only','score75-net25-v1'):raise ValueError('Unknown immigration policy')
    if job['expert_capacity_per_session']!=128 or job['mutation_probe_size']!=32:raise ValueError('Expert/diagnostic envelope changed')
    if set(job['session_probe_sha256'])!=set(train):raise ValueError('Every training session needs a pinned probe')
    if 'founder_reuse' in job:raise ValueError('Full run must not import pilot financial results')

def check_full_job(job):
    if job['version'] not in ('v59-full-v1','v59-full-v2') or source_hash()!=job['source_sha256']:raise ValueError('Pinned full source changed')
    for k in ('source_job','training','validation'):
        if sha(job[k])!=job[k+'_sha256']:raise ValueError('Pinned input changed: '+k)
    train=json.loads(Path(job['training']).read_bytes());valid=json.loads(Path(job['validation']).read_bytes())
    check_structure(job,train['days'],valid['days'])
    original=json.loads(Path(job['source_job']).read_bytes())
    if job['execution']!=dict(original['execution'],risk_limits_enabled=False):raise ValueError('Unauthorized execution deviation')
    if job['assets']!=original['assets'] or job['asset_sha256']!=original['asset_sha256']:raise ValueError('Certified asset inventory changed')
    for role,days in (('training',train['days']),('validation',valid['days'])):
        if set(job['assets'][role])!=set(days):raise ValueError('Asset session membership changed')
    for n,m in job['machines'].items():
        expected=2 if n=='workstation' and job['version']=='v59-full-v2' else 1
        if m['session_workers']!=expected:raise ValueError('Independent session concurrency changed')
    if job['version']=='v59-full-v2':
        if job.get('global_initialization')!=dict(version='v57-blind-mix-v1',experts=3072,offspring=3072,group_quota=512,require_surviving_contributions=True):raise ValueError('Blind initialization policy changed')
        from .resume_authority import predecessor
        if job.get('resume_transition'):predecessor(job)
    return job

def prepare_full(source,root,laptop_code,workstation_code):
    from research.vectorized_backtest.v6.torch_backtest.runtime import require_runtime
    root=require_runtime(root);source=Path(source);original=json.loads(source.read_bytes())
    if (root/'full-job.json').exists():raise ValueError('Existing full job must not be overwritten')
    train=json.loads(Path(original['training']).read_bytes())['days'];valid=json.loads(Path(original['validation']).read_bytes())['days']
    machines={n:dict(original['machines'][n],code=str(code),session_days=original['machines'][n]['days']['training'],session_workers=1) for n,code in (('laptop',laptop_code),('workstation',workstation_code))}
    import platform
    machines['laptop']['hostname']=platform.node();machines['workstation']['hostname']='DESKTOP-SAAI85T'
    for m in machines.values():m.pop('days',None)
    job=dict(version='v59-full-v1',source_sha256=source_hash(),commit=json.loads((Path(laptop_code)/'deployment.json').read_bytes())['commit'],
        campaign=str(root),transport=str(root/'exchange'),workstation_share=original['workstation_share'],source_job=str(source),source_job_sha256=sha(source),
        training=original['training'],validation=original['validation'],training_sha256=original['training_sha256'],validation_sha256=original['validation_sha256'],
        training_days=train,validation_days=valid,assets=original['assets'],asset_sha256=original['asset_sha256'],source_probe_root=original['probe_root'],probe_root=str(root/'calibration'),machines=machines,
        population=1024,initial_multiplier=6,seed=2021,batch_size=512,phase1_generations=50,phase2_generations=50,global_node='workstation',global_session_workers=2,
        expert_capacity_per_session=128,objective=dict(drawdown_weight=1.,loss_weight=1.),execution=dict(original['execution'],risk_limits_enabled=False),
        selection=original['selection'],selection_risk_caps=False,ticker_parent_fraction=.25,mutation_probe_size=32,
        affinity='inherited certified-source allocation; not a new V59 throughput measurement')
    from research.vectorized_backtest.v42.distributed_assets import remote
    job['session_probe_sha256']={}
    for day in train:
        p=Path(original['probe_root'])/(day+'.npz')
        if not p.exists():p=remote(p,job['workstation_share'])
        job['session_probe_sha256'][day]=sha(p)
    check_full_job(job);write_json(root/'full-job.json',job)
    write_json(root/'manifest.json',dict(version=job['version'],job_sha256=sha(root/'full-job.json'),commit=job['commit'],training_days=train,validation_days=valid,
        session_generations=50,global_generations=50,global_session_workers=2,global_initial_candidates=6144,maximum_session_experts=3072,
        validation='display only after successor/RNG freeze',status='prepared; not launched'))
    return job
