"""Fresh workstation-only global campaigns; no phase-one financial inheritance."""
import json
from pathlib import Path
from .identity import source_hash
from research.vectorized_backtest.v42.distributed_assets import sha
from research.vectorized_backtest.v42.publication import write_json

VERSION='v58-global-v1'


def check(job):
    if job['version']!=VERSION or job['source_sha256']!=source_hash():raise ValueError('Global source identity differs')
    if set(job['machines'])!={'workstation'} or job['campaign_stage']!='fresh-global':raise ValueError('Fresh workstation global ownership required')
    if (job['population'],job['initial_multiplier'],job['seed'],job['phase2_generations'],job['mutation_probe_size'])!=(1024,6,2021,200,32):raise ValueError('Global campaign dimensions changed')
    if job['global_session_workers']!=2:raise ValueError('Two concurrent replay lanes required')
    if job.get('neural_tile',64)!=64:raise ValueError('Global campaigns require the parity-verified 64-lane neural tile')
    for key in ('training','validation','source_job','initialization_probe'):
        if sha(job[key])!=job[key+'_sha256']:raise ValueError(key+' changed')
    train=json.loads(Path(job['training']).read_bytes())['days'];valid=json.loads(Path(job['validation']).read_bytes())['days']
    if len(set(train))!=24 or len(set(valid))!=12 or max(train)>=min(valid):raise ValueError('Certified chronological 24/12 split required')
    if job['training_days']!=train or job['validation_days']!=valid:raise ValueError('Session split changed')
    if job['machines']['workstation']['days']!=dict(training=train,validation=valid):raise ValueError('Session ownership changed')
    from .robust_score import VERSION as SCORE_VERSION,COEFFICIENT
    if job['objective']!=dict(drawdown_weight=1.,loss_weight=1.) or job['score_version']!=SCORE_VERSION or job['concentration_coefficient']!=COEFFICIENT:raise ValueError('Concentration cost changed')
    from .founder_restart import verify_binding
    verify_binding(job)
    if job['ticker_parent_fraction']!=.25 or job['selection_risk_caps'] is not False:raise ValueError('Parent selection changed')
    original=json.loads(Path(job['source_job']).read_bytes())
    if job['execution']!=dict(original['execution'],risk_limits_enabled=False):raise ValueError('Execution changed')
    if any(job.get(k) for k in ('continuation','promotion_exports')):raise ValueError('Fresh campaign cannot inherit external fitness')
    if job.get('resume_transition'):
        from .resume_authority import global_predecessor
        global_predecessor(job)
    elif job.get('result_cache_transition'):raise ValueError('Cache transition requires a pinned predecessor')
    from .calibration import load_probe
    metadata=load_probe(job['initialization_probe'],job['training'])['metadata']
    if metadata['validation_opened'] or not set(metadata['days']).issubset(train) or len(metadata['days'])<2:raise ValueError('Training-only multi-session calibration required')


def prepare(source,root,code):
    from .founder_restart import prepare as restart
    return restart(source,root,code)
