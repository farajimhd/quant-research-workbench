"""Fresh deterministic founders; cache only unevaluated V65 initialization."""
def global_seed(exports,job,probe,progress=None):
    from pathlib import Path
    import json
    from .campaign_seed import seed
    from .genome import Individual
    from research.vectorized_backtest.v65.publication import write_json
    if job.get('selection_restart'):
        from .founder_reuse import seed as reuse_seed
        return reuse_seed(job)
    if job.get('founder_restart'):raise ValueError('V65 requires fresh candidates')
    path=Path(job['campaign'])/'fresh-founders.json'
    if path.exists():
        state=json.loads(path.read_bytes())
        if state['source_sha256']!=job['source_sha256'] or state['validation_opened'] or len(state['population'])!=1024:
            raise ValueError('Fresh founder cache authority differs')
        if any(v is not None for v in state.get('sources',[])):raise ValueError('Fresh initialization cannot import expert results')
        from .initial_population import validate_fresh
        for p in state['population']:validate_fresh(Individual.restore(p))
        return state
    state=seed(exports,dict(job,campaign_stage=job.get('campaign_stage','session')),probe,progress=progress)
    write_json(path,state)
    return state
