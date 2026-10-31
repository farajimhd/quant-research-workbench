"""Fresh workstation-only global campaigns; no phase-one financial inheritance."""
import json
from pathlib import Path
from .identity import source_hash
from research.vectorized_backtest.v60.distributed_assets import sha
from research.vectorized_backtest.v60.publication import write_json

VERSION='v60-global-v1'


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
    if (job.get('ticker_target'),job.get('session_target'))!=(2000.,5000.):raise ValueError('Profit targets changed')
    from .robust_score import VERSION as SCORE_VERSION,COEFFICIENT
    if job['objective']!=dict(drawdown_weight=1.,loss_weight=0.) or job['score_version']!=SCORE_VERSION or job['concentration_coefficient']!=COEFFICIENT:raise ValueError('Concentration cost changed')
    if job.get('founder_restart'):raise ValueError('V60 requires fresh candidates')
    if job['ticker_parent_fraction']!=.25 or job['selection_risk_caps'] is not False:raise ValueError('Parent selection changed')
    if job.get('fresh_initial_replay')!='v60-fresh-founders-v1':raise ValueError('V60 requires a fresh founder replay')
    original=json.loads(Path(job['source_job']).read_bytes())
    expected=dict(original['execution'],risk_limits_enabled=False)
    if job.get('fresh_initial_replay'):
        if job['fresh_initial_replay']!='v60-fresh-founders-v1' or job.get('execution_restart'):raise ValueError('Fresh crossing/order-state initialization cannot inherit genomes')
        from .orders import VERSION as ORDER_VERSION
        expected.update(order_mode=ORDER_VERSION,maximum_quote_age_seconds=None)
        if not job.get('execution_tail'):raise ValueError('Certified quote-size execution pages required')
    elif job.get('execution_restart'):
        from .orders import VERSION as ORDER_VERSION
        expected['order_mode']=ORDER_VERSION
        expected['maximum_quote_age_seconds']=None
        from .execution_restart import verify
        verify(job)
    if job['execution']!=expected:raise ValueError('Execution changed')
    if any(job.get(k) for k in ('continuation','promotion_exports')):raise ValueError('Fresh campaign cannot inherit external fitness')
    if job.get('resume_transition'):
        from .resume_authority import global_predecessor
        global_predecessor(job)
    elif job.get('result_cache_transition'):raise ValueError('Cache transition requires a pinned predecessor')
    from .calibration import load_probe
    metadata=load_probe(job['initialization_probe'],job['training'])['metadata']
    if metadata['version']!='v60-training-boundary-probe-v1':raise ValueError('V60 requires its own execution-aligned calibration probe')
    if metadata['validation_opened'] or not set(metadata['days']).issubset(train) or len(metadata['days'])<2:raise ValueError('Training-only multi-session calibration required')


def prepare(source,root,code):
    from research.vectorized_backtest.v60.runtime import require_runtime
    from research.vectorized_backtest.v60.probes import global_probe
    root=require_runtime(root);code=Path(code);original=json.loads(Path(source).read_bytes())
    if (root/'global-job.json').exists():raise ValueError('Existing campaign must be resumed, not replaced')
    train=json.loads(Path(original['training']).read_bytes())['days'];valid=json.loads(Path(original['validation']).read_bytes())['days']
    probe=Path(original['initialization_probe'])
    job={k:original[k] for k in ('training','training_sha256','validation','validation_sha256','assets','asset_sha256','workstation_share')}
    job.update(version=VERSION,campaign_stage='fresh-global',score_version='log-profit-target-v1',concentration_coefficient=.1,ticker_target=2000.,session_target=5000.,source_sha256=source_hash(),source_job=str(source),source_job_sha256=sha(source),
        code=str(code),commit=json.loads((code/'deployment.json').read_bytes())['commit'],campaign=str(root),transport=str(root/'asset-bus'),
        training_days=train,validation_days=valid,initialization_probe=str(probe),initialization_probe_sha256=sha(probe),population=1024,initial_multiplier=6,seed=2021,
        phase2_generations=200,mutation_probe_size=32,global_session_workers=2,global_node='workstation',batch_size=1024,
        objective=dict(drawdown_weight=1.,loss_weight=0.),execution=dict(original['execution'],risk_limits_enabled=False),selection=dict(original['selection'],profit_fresh_fraction=0.),
        selection_risk_caps=False,ticker_parent_fraction=.25,validation_schedule='concurrent-training-only-v1',
        machines={'workstation':dict(original['machines']['workstation'],code=str(code),days=dict(training=train,validation=valid),session_days=[],session_workers=2)})
    from .orders import VERSION as ORDER_VERSION
    job.update(fresh_initial_replay='v60-fresh-founders-v1',execution_tail=original['execution_tail'])
    job['execution'].update(order_mode=ORDER_VERSION,maximum_quote_age_seconds=None)
    from .calibration_probe import prepare as prepare_probe
    probe=prepare_probe(job)
    job.update(initialization_probe=str(probe),initialization_probe_sha256=sha(probe))
    check(job);write_json(root/'global-job.json',job)
    return job
