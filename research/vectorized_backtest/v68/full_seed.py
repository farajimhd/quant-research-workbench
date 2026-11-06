"""Fresh deterministic founders; cache only unevaluated V67 initialization."""
def global_seed(exports,job,probe,progress=None):
    from pathlib import Path
    import json
    from .campaign_seed import seed
    from .genome import Individual
    from research.vectorized_backtest.v68.publication import write_json
    if job.get('selection_restart'):
        from .founder_reuse import seed as reuse_seed
        return reuse_seed(job)
    if job.get('founder_restart'):raise ValueError('V67 requires fresh candidates')
    path=Path(job['campaign'])/'fresh-founders.json'
    if path.exists():
        state=json.loads(path.read_bytes())
        if state['source_sha256']!=job['source_sha256'] or state['validation_opened'] or len(state['population'])!=1024*job['initial_multiplier']:
            raise ValueError('Fresh founder cache authority differs')
        if any(v is not None for v in state.get('sources',[])):raise ValueError('Fresh initialization cannot import expert results')
        if job['initial_multiplier']==6:
            from .founder_pool import validate as validate_fresh
        else:
            from .initial_population import validate_fresh
        for p in state['population']:validate_fresh(Individual.restore(p))
        return state
    if job.get('founder_readmission'):
        from .founder_readmission import seed as readmit
        state=readmit(job)
    elif job['initial_multiplier']==6:
        if exports:raise ValueError('Fresh founders cannot inherit exports')
        from .founder_pool import seed as sixfold_seed
        state=sixfold_seed(job,probe,progress)
    else:state=seed(exports,dict(job,campaign_stage=job.get('campaign_stage','session')),probe,progress=progress)
    write_json(path,state)
    return state
