"""Explicit immutable predecessor authority; no rewriting sealed history."""
import json
from pathlib import Path
from .identity import digest
from research.vectorized_backtest.v65.distributed_assets import sha

def initial_score_transition(job,old):
    change=job.get('resume_transition',{}).get('score_change')
    if not change:return False
    expected=dict(from_version='log-profit-target-blocked-request-v2',
                  to_version='log-profit-target-blocked-request-no-dd-v3',initial_only=True)
    if (change!=expected or old.get('score_version')!=expected['from_version']
        or job.get('score_version')!=expected['to_version']
        or old.get('objective')!=dict(drawdown_weight=1.,loss_weight=0.)
        or job.get('objective')!=dict(drawdown_weight=0.,loss_weight=0.)):
        raise ValueError('Unapproved objective transition')
    return True

def global_predecessor(job):
    transition=job.get('resume_transition',{});path=Path(transition.get('predecessor_job',''))
    if not path.is_file() or sha(path)!=transition.get('predecessor_job_sha256'):raise ValueError('Global predecessor changed')
    old=json.loads(path.read_bytes())
    mutable={'source_sha256','code','commit','deployment','resume_transition','result_cache_transition'}
    if transition.get('generation_extension'):
        if (transition['generation_extension']!={'from':200,'to':300}
            or old.get('phase2_generations')!=200 or job.get('phase2_generations')!=300):
            raise ValueError('Unapproved generation extension')
        mutable.add('phase2_generations')
    if transition.get('agent_intervention_change'):
        from .agent_intervention import check_transition
        check_transition(old.get('agent_intervention'), job.get('agent_intervention'),
                         transition['agent_intervention_change'])
        mutable.add('agent_intervention')
    scheduling=transition.get('scheduling_change')
    if scheduling:
        from .performance import configuration,loading
        before=dict(replay=configuration(old),host=loading(old),publication_workers=old.get('publication_workers',1))
        after=dict(replay=configuration(job),host=loading(job),publication_workers=job.get('publication_workers',1))
        if after['publication_workers'] not in (1,2,4):raise ValueError('Unbounded publication workers')
        if scheduling!=dict(before=before,after=after):raise ValueError('Scheduling transition differs from pinned jobs')
        mutable.update(('global_session_workers','host_loading','replay_tuning','publication_workers'))
    if initial_score_transition(job,old):mutable.update(('objective','score_version'))
    def semantics(value):
        value=json.loads(json.dumps({k:v for k,v in value.items() if k not in mutable}))
        value['machines']['workstation']['code']=None
        if scheduling:value['machines']['workstation']['session_workers']=None
        return value
    if semantics(job)!=semantics(old):raise ValueError('Global continuation changed campaign semantics')
    return old

def predecessor(job):
    transition=job.get('resume_transition')
    if not transition:return None
    if job.get('version')=='v65-global-v1':return global_predecessor(job)
    path=Path(transition['predecessor_job'])
    if sha(path)!=transition['predecessor_job_sha256']:raise ValueError('Predecessor job changed')
    old=json.loads(path.read_bytes())
    if job.get('validation_schedule')!=old.get('validation_schedule'):
        if job.get('validation_schedule')!='concurrent-training-only-v1' or transition.get('validation_overlap') is not True:raise ValueError('Unapproved validation scheduling change')
    for key in ('training_sha256','validation_sha256','objective','execution','selection','population','initial_multiplier','seed','phase1_generations','phase2_generations','ticker_parent_fraction','batch_size','asset_sha256'):
        if job[key]!=old[key]:
            authorized=key=='selection' and isinstance(old['selection'],dict) and transition.get('profit_retention')==dict(from_fraction=old['selection'].get('profit_fresh_fraction',0.),to_fraction=.25) and job[key]==dict(old[key],profit_fresh_fraction=.25)
            if not authorized:raise ValueError('Resume changed campaign semantics: '+key)
    if job.get('campaign_stage')=='session':
        if len(job['training_days'])!=1 or job['training_days'][0] not in old['training_days'] or job['validation_days']:raise ValueError('Invalid session projection')
    elif job['training_days']!=old['training_days'] or job['validation_days']!=old['validation_days']:raise ValueError('Resume changed splits')
    return old

def source_allowed(job,source):
    return any(source==item['source_sha256'] for item in lineage_jobs(job))

def lineage_jobs(job):
    seen=set()
    while job is not None:
        identity=digest(job)
        if identity in seen:raise ValueError('Cyclic continuation authority')
        seen.add(identity);yield job;job=predecessor(job)

def accept_checkpoint(job,path,saved,expected):
    if saved['contract']==expected:return
    if job.get('version')=='v65-global-v1':
        old=global_predecessor(job)
        bound=job['resume_transition'].get('global_checkpoint',{})
        if sha(path)!=bound.get('checkpoint_sha256') or saved['state_sha256']!=bound.get('state_sha256') or digest(saved['contract'])!=bound.get('contract_sha256'):raise ValueError('Global checkpoint differs from bound receipt')
        actual=dict(saved['contract'],source_sha256=expected['source_sha256'])
        if job['resume_transition'].get('generation_extension'):
            if saved['contract'].get('generations')!=200 or not 0<=saved['state']['completed']<200:
                raise ValueError('Generation extension requires an unfinished 200-generation checkpoint')
            actual['generations']=300
        if job['resume_transition'].get('agent_intervention_change'):
            actual['agent_intervention']=job['agent_intervention']
        if job['resume_transition'].get('initial_reproduction_change'):
            from .founder_reproduction import VERSION
            if (job['resume_transition']['initial_reproduction_change']!=VERSION or saved['state']['completed']!=-1
                or saved['state']['seals'] or 'initial_reproduction' in saved['contract']):
                raise ValueError('Initial reproduction transition requires unselected founders')
            actual['initial_reproduction']=VERSION
        if initial_score_transition(job,old):
            if saved['state']['completed']!=-1 or saved['state']['seals']:
                raise ValueError('Objective transition requires unselected initial founders')
            actual.update(objective=job['objective'],score_version=job['score_version'])
        if actual!=expected:raise ValueError('Global continuation changed more than approved source/objective')
        return
    if job.get('version')=='v60-pilot-v2':
        transition=job.get('resume_transition',{});prior=Path(transition.get('predecessor_job',''))
        if not prior.is_file() or sha(prior)!=transition.get('predecessor_job_sha256'):raise ValueError('Pinned pilot predecessor changed')
        old=json.loads(prior.read_bytes())
        mutable={'source_sha256','code','commit','resume_transition','result_cache_transition'}
        if any(job.get(k)!=v for k,v in old.items() if k not in mutable):raise ValueError('Pilot continuation changed campaign semantics')
        bound=transition.get('pilot_checkpoint',{})
        if sha(path)!=bound.get('checkpoint_sha256') or saved['state_sha256']!=bound.get('state_sha256') or digest(saved['contract'])!=bound.get('contract_sha256'):raise ValueError('Pilot checkpoint differs from bound receipt')
        actual=dict(saved['contract'],source_sha256=expected['source_sha256'])
        change=transition.get('score_change')
        if change:
            if change!=dict(from_version=old.get('score_version'),to_version='profit-only-trim-v2') or job.get('score_version')!=change['to_version']:
                raise ValueError('Unapproved trimming score transition')
            actual['score_version']=job['score_version']
        if actual!=expected:raise ValueError('Pilot continuation changed more than implementation source')
        return
    old=predecessor(job);day=job['training_days'][0]
    if old is None or job.get('campaign_stage') not in ('session','global'):raise ValueError('Unapproved checkpoint source transition')
    bound=job['resume_transition'].get('global_checkpoint') if job.get('campaign_stage')=='global' else job['resume_transition']['checkpoints'].get(day)
    if not bound or sha(path)!=bound['checkpoint_sha256'] or saved['state_sha256']!=bound['state_sha256'] or digest(saved['contract'])!=bound['contract_sha256']:raise ValueError('Saved checkpoint differs from transition receipt')
    actual=dict(saved['contract']);actual['source_sha256']=expected['source_sha256'];actual['campaign_context']=dict(actual['campaign_context'],parent_job_sha256=expected['campaign_context']['parent_job_sha256'])
    if job['resume_transition'].get('validation_overlap') is True and job.get('validation_schedule')=='concurrent-training-only-v1':actual['validation']=expected['validation']
    if job['resume_transition'].get('profit_retention') and expected.get('profit_fresh_fraction')==.25:actual['profit_fresh_fraction']=.25
    if actual!=expected:raise ValueError('Resume transition changed more than source/parent campaign identity')

def export_job(job,day,meta):
    if meta['job_sha256']==digest(job):return job
    old=predecessor(job)
    allowed=job.get('resume_transition',{}).get('exports',{})
    if old is None or day not in allowed:raise ValueError('Unapproved predecessor export')
    if meta['job_sha256']==digest(old):return old
    if old.get('resume_transition',{}).get('exports',{}).get(day)!=allowed[day]:raise ValueError('Export continuation chain changed')
    return export_job(old,day,meta)

def accept_cache(config,path,record,expected):
    if record['identity']==expected:return
    bound=config.get('result_cache_transition',{}).get(sha(path))
    prior=dict(record['identity']);prior['source']=expected['source']
    if not bound or record['identity']['source']!=bound['source'] or prior!=expected:raise ValueError('Result cache contract changed')


def global_resume(job,bus):
    """The saved global state supersedes decoding phase-one populations again.

    Controller still validates the complete state, source transition and every
    generation seal before replay. This reads that same checkpoint only once.
    """
    root=Path(job['campaign']);saved=json.loads((root/'phase-2/global/checkpoint.json').read_bytes())
    context=saved['contract']['campaign_context'];exports=context.get('promotion_exports',{})
    if context.get('campaign_stage')!='global' or set(exports)!=set(job['training_days']):raise ValueError('Global checkpoint promotion scope changed')
    for day,expected in exports.items():
        if sha(Path(bus)/'sessions'/day/'export.json')!=expected:raise ValueError('Global checkpoint export binding changed')
    probe=Path(job['probe_root'])/'global.npz'
    if sha(probe)!=context['initialization_probe_sha256']:raise ValueError('Global resume probe changed')
    derived=dict(job,campaign_stage='global',initialization_probe=str(probe),initialization_probe_sha256=sha(probe),parent_job_sha256=digest(job),promotion_exports=exports)
    return derived,saved
