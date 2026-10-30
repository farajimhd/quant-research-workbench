"""Explicit genome/RNG continuation with no inherited financial results."""
import json
from pathlib import Path
from .identity import digest
from .orders import VERSION
from research.vectorized_backtest.v42.distributed_assets import sha

def initial_replay(job):
    return job.get('execution_restart',{}).get('mode')=='initial-founders'


def verify(job):
    proof=job['execution_restart']
    old_path=Path(proof['job'])
    field='initial_pool' if initial_replay(job) else 'checkpoint'
    checkpoint=Path(proof[field])
    if sha(old_path)!=proof['job_sha256'] or sha(checkpoint)!=proof[field+'_sha256']:
        raise ValueError('Execution restart predecessor changed')
    old=json.loads(old_path.read_bytes())
    if Path(job['campaign'])==Path(old['campaign']):raise ValueError('Execution restart must preserve the old campaign')
    if job.get('result_cache_transition') or job.get('resume_transition'):
        # An implementation-only repair can reuse receipts already produced by
        # this execution contract, never the original execution's financials.
        from .resume_authority import global_predecessor
        prior=global_predecessor(job)
        if prior.get('execution_restart')!=proof or prior['execution']!=job['execution']:
            raise ValueError('Old financial caches cannot cross execution contracts')
    allowed={'source_sha256','code','commit','campaign','transport','execution','execution_restart','execution_tail','resume_transition','result_cache_transition'}
    def semantics(value):
        value=json.loads(json.dumps({k:v for k,v in value.items() if k not in allowed}))
        value['machines']['workstation']['code']=None
        return value
    if semantics(old)!=semantics(job):raise ValueError('Execution repair changed unrelated campaign settings')
    if job['execution']!=dict(old['execution'],order_mode=VERSION,maximum_quote_age_seconds=None):raise ValueError('Execution repair changed sizing or costs')
    saved=json.loads(checkpoint.read_bytes())
    if initial_replay(job):
        expected=Path(old['campaign'])/'phase-2/global/initial-pool.json'
        if checkpoint!=expected or len(saved['population'])!=6144 or len(saved['ids'])!=6144 or len(set(saved['ids']))!=6144:
            raise ValueError('The complete original 6144-founder pool is required')
        if saved.get('completed')!=0 or saved.get('seals'):
            raise ValueError('Initial replay cannot use an evolved population')
        saved={'state':saved}
    else:
        if digest(saved['state'])!=saved['state_sha256'] or saved['state_sha256']!=proof['state_sha256']:
            raise ValueError('Saved execution restart state changed')
        if saved['contract']['execution']!=old['execution']:raise ValueError('Predecessor checkpoint execution differs')
    # All 36 sidecars must be complete before launching optimization.
    for role in ('training','validation'):
        for day in job[role+'_days']:
            root=Path(job['execution_tail'])/role/day
            receipt=json.loads((root/'complete.json').read_bytes())
            if receipt['status']!='complete' or receipt['identity']['validation_opened']!=(role=='validation'):
                raise ValueError('Execution tail split/coverage differs')
    return saved


def seed(job):
    from .species import Species
    saved=verify(job);state=dict(saved['state'])
    # Strategy identity, pedigree and RNG remain exact. Old fitness, behavioral
    # species descriptors and generation seals remain solely in the old run.
    state['species']=Species().state;state['seals']={}
    if initial_replay(job):
        # Evaluate every unevolved founder at generation zero, then create the
        # first 1024-candidate generation without importing old financial scores.
        state['completed']=-1
        state['pedigree']=[dict(r,generation=0) for r in state['pedigree']]
    state['execution_predecessor']=job['execution_restart']
    return state
