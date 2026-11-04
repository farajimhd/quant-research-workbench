"""Fresh workstation-only global campaigns; no phase-one financial inheritance."""
import json
from pathlib import Path
from .identity import source_hash
from research.vectorized_backtest.v66.distributed_assets import sha
from research.vectorized_backtest.v66.publication import write_json

VERSION='v66-global-v1'
STRATEGY_CONTRACT=dict(branch_ownership='entry-order-through-close-v1',maximum_spread_bps=500.,initial_stop_bid_margin=.03,uniform_parent_fraction=.2,canonicalization='v66-effective-graph-v1',top_k=5,price_program='explicit-prices-v1',rank_loss_exit=True,
    position_feedback=False,blocked_request_penalty=5.,reentry_wait_seconds=3,
    entry_sizing='fixed-1000-floor-shares',add_sizing='fixed-200-floor-shares',whole_shares=True,maximum_filled_add_orders=5,
    feature_data='arte-premarket-shards-v1; completed multiscale windows; top5 trade count')


def check(job):
    from .agent_intervention import check_policy
    check_policy(job)
    from .fresh_design import SETTINGS
    if job.get('fresh_design')!=SETTINGS:raise ValueError('Fresh candidate design differs')
    if job.get('strategy_contract')!=STRATEGY_CONTRACT:raise ValueError('V66 strategy/execution contract differs')
    if job['version']!=VERSION or job['source_sha256']!=source_hash():raise ValueError('Global source identity differs')
    if set(job['machines'])!={'workstation'} or job['campaign_stage']!='fresh-global':raise ValueError('Fresh workstation global ownership required')
    if (job['population'],job['seed'],job['mutation_probe_size'])!=(1024,2021,32) or job['initial_multiplier'] not in (1,6):raise ValueError('Global campaign dimensions changed')
    if job['phase2_generations'] not in (200,300):raise ValueError('Bounded 200/300-generation campaign required')
    if job['initial_multiplier']==6:
        from .founder_pool import SETTINGS as POOL_SETTINGS
        if job.get('founder_pool')!=POOL_SETTINGS:raise ValueError('Sixfold founder contract differs')
    from .performance import configuration,loading
    performance=configuration(job)
    loading(job)
    if job.get('publication_workers',1) not in (1,2,4):raise ValueError('Bounded publication workers required')
    if job['machines']['workstation']['session_workers']!=performance['session_workers']:raise ValueError('Replay worker metadata differs')
    if job['machines']['workstation'].get('hostname')!='DESKTOP-SAAI85T':raise ValueError('Pinned workstation hostname required')
    if job.get('neural_tile',64)!=64:raise ValueError('Global campaigns require the parity-verified 64-lane neural tile')
    for key in ('training','validation','source_job','initialization_probe'):
        if sha(job[key])!=job[key+'_sha256']:raise ValueError(key+' changed')
    train=json.loads(Path(job['training']).read_bytes())['days'];valid=json.loads(Path(job['validation']).read_bytes())['days']
    if len(set(train))!=150 or len(set(valid))!=38 or max(train)>=min(valid):raise ValueError('Certified chronological 150/38 split required')
    if job['training_days']!=train or job['validation_days']!=valid:raise ValueError('Session split changed')
    if job['machines']['workstation']['days']!=dict(training=train,validation=valid):raise ValueError('Session ownership changed')
    if (job.get('ticker_target'),job.get('session_target'))!=(2000.,5000.):raise ValueError('Profit targets changed')
    from .robust_score import VERSION as SCORE_VERSION,COEFFICIENT
    if job['objective']!=dict(drawdown_weight=0.,loss_weight=0.) or job['score_version']!=SCORE_VERSION or job['concentration_coefficient']!=COEFFICIENT:raise ValueError('Concentration cost changed')
    if job.get('selection_restart'):raise ValueError('New data cannot inherit V64 fitness')
    if job.get('founder_restart'):raise ValueError('V60 requires fresh candidates')
    if job['ticker_parent_fraction']!=.25 or job['selection_risk_caps'] is not False:raise ValueError('Parent selection changed')
    if job.get('fresh_initial_replay')!='v66-fresh-founders-v1':raise ValueError('V60 requires a fresh founder replay')
    original=json.loads(Path(job['source_job']).read_bytes())
    expected=dict(original['execution'],risk_limits_enabled=False)
    if job.get('fresh_initial_replay'):
        if job['fresh_initial_replay']!='v66-fresh-founders-v1' or job.get('execution_restart'):raise ValueError('Fresh crossing/order-state initialization cannot inherit genomes')
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
    elif job.get('result_cache_transition') and not job.get('founder_readmission'):raise ValueError('Cache transition requires a pinned predecessor')
    if job.get('founder_readmission'):
        from .founder_readmission import verify
        verify(job)
    from .calibration import load_probe
    metadata=json.loads(Path(job['initialization_probe']).read_bytes())
    if metadata['version']!='v66-top5-in-memory-probe-v1':raise ValueError('V60 requires its own execution-aligned calibration probe')
    if metadata['validation_opened'] or not set(metadata['days']).issubset(train) or len(metadata['days'])<2:raise ValueError('Training-only multi-session calibration required')


def prepare(source,root,code,*,deployment=None,reuse_training_probe=False):
    if reuse_training_probe:raise ValueError('V66 must calibrate on its new training data')
    from .prepare_shard_campaign import prepare as build
    return build(source,Path('D:/TradingML/runtimes/market_shards/datasets/2026-top5-v1'),root,code)
