"""Explicit immutable predecessor authority; no rewriting sealed history."""
import json
from pathlib import Path
from .identity import digest
from research.vectorized_backtest.v42.distributed_assets import sha

def predecessor(job):
    transition=job.get('resume_transition')
    if not transition:return None
    path=Path(transition['predecessor_job'])
    if sha(path)!=transition['predecessor_job_sha256']:raise ValueError('Predecessor job changed')
    old=json.loads(path.read_bytes())
    for key in ('training_sha256','validation_sha256','objective','execution','selection','population','initial_multiplier','seed','phase1_generations','phase2_generations','ticker_parent_fraction','batch_size','asset_sha256'):
        if job[key]!=old[key]:raise ValueError('Resume changed campaign semantics: '+key)
    if job.get('campaign_stage')=='session':
        if len(job['training_days'])!=1 or job['training_days'][0] not in old['training_days'] or job['validation_days']:raise ValueError('Invalid session projection')
    elif job['training_days']!=old['training_days'] or job['validation_days']!=old['validation_days']:raise ValueError('Resume changed splits')
    return old

def source_allowed(job,source):
    if source==job['source_sha256']:return True
    old=predecessor(job)
    return old is not None and source==old['source_sha256']

def accept_checkpoint(job,path,saved,expected):
    if saved['contract']==expected:return
    old=predecessor(job);day=job['training_days'][0]
    if old is None or job.get('campaign_stage')!='session':raise ValueError('Unapproved checkpoint source transition')
    bound=job['resume_transition']['checkpoints'].get(day)
    if not bound or sha(path)!=bound['checkpoint_sha256'] or saved['state_sha256']!=bound['state_sha256'] or digest(saved['contract'])!=bound['contract_sha256']:raise ValueError('Saved checkpoint differs from transition receipt')
    actual=dict(saved['contract']);actual['source_sha256']=expected['source_sha256'];actual['campaign_context']=dict(actual['campaign_context'],parent_job_sha256=expected['campaign_context']['parent_job_sha256'])
    if actual!=expected:raise ValueError('Resume transition changed more than source/parent campaign identity')

def export_job(job,day,meta):
    if meta['job_sha256']==digest(job):return job
    old=predecessor(job)
    allowed=job.get('resume_transition',{}).get('exports',{})
    if old is None or meta['job_sha256']!=digest(old) or day not in allowed:raise ValueError('Unapproved predecessor export')
    return old

def accept_cache(config,path,record,expected):
    if record['identity']==expected:return
    bound=config.get('result_cache_transition',{}).get(sha(path))
    prior=dict(record['identity']);prior['source']=expected['source']
    if not bound or record['identity']['source']!=bound['source'] or prior!=expected:raise ValueError('Result cache contract changed')
