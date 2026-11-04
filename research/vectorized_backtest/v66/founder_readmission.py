"""Explicit immutable V66 training-receipt reuse for a new founder admission."""
import json
from pathlib import Path
from .distributed_assets import sha


def verify_cache_bindings(job,bound):
    original=bound.get('result_caches',{});current=job.get('result_cache_transition',{})
    if current==original:return
    # Later source-only resumes add current replay indexes; original founder
    # authority stays immutable, and new indexes must belong to the pinned source.
    from .resume_authority import global_predecessor
    previous=global_predecessor(job)
    prior=previous.get('result_cache_transition',{})
    if previous.get('founder_readmission')!=bound:
        raise ValueError('Founder readmission authority changed on resume')
    if any(current.get(k)!=v for mapping in (original,prior) for k,v in mapping.items()):
        raise ValueError('Founder/prior cache bindings changed')
    if any(v.get('source')!=previous['source_sha256'] for k,v in current.items() if k not in prior):
        raise ValueError('New cache binding is not from the pinned predecessor source')


def verify(job):
    bound=job['founder_readmission']
    if bound.get('version')!='v66-training-founder-readmission-v1':raise ValueError('Unknown founder readmission')
    path=Path(bound['predecessor_job'])
    if sha(path)!=bound['predecessor_job_sha256']:raise ValueError('Founder predecessor changed')
    old=json.loads(path.read_bytes())
    if old['version']!='v66-global-v1' or old['initial_multiplier']!=6:raise ValueError('Sixfold V66 founders required')
    for key in ('training_sha256','validation_sha256','training_days','validation_days','asset_sha256',
                'execution','objective','score_version','concentration_coefficient','selection','founder_pool',
                'initialization_probe_sha256','strategy_contract','seed','population','initial_multiplier'):
        if job[key]!=old[key]:raise ValueError('Founder readmission changes '+key)
    founders=Path(old['campaign'])/'fresh-founders.json'
    if str(founders)!=bound['founders'] or sha(founders)!=bound['founders_sha256']:raise ValueError('Founder genomes changed')
    expected={f'training/0/{day}' for day in old['training_days']}
    if set(bound['completed_replays'])!=expected:raise ValueError('Complete training-only founder receipts required')
    for day in old['training_days']:
        item=bound['completed_replays'][f'training/0/{day}']
        if Path(item['path'])!=Path(old['campaign'])/'phase-2/replays/training/generation-0000'/day/'receipt.json':
            raise ValueError('Founder receipt path changed')
    verify_cache_bindings(job,bound)
    # Scheduling/scoring may change; the replay and input implementation may not.
    for name in ('compact_execution.py','compact_inference.py','compact_replay.py','network_cuda.py',
                 'price_cuda.py','orders.py','feature_policy.py','features.py','shard_data.py'):
        a=Path(old['code'])/'research/vectorized_backtest/v66'/name
        b=Path(__file__).parent/name
        if a.read_bytes().replace(b'\r\n',b'\n')!=b.read_bytes().replace(b'\r\n',b'\n'):
            raise ValueError('Founder replay implementation changed: '+name)
    return old


def seed(job):
    old=verify(job);state=json.loads(Path(job['founder_readmission']['founders']).read_bytes())
    if state['source_sha256']!=old['source_sha256'] or state['validation_opened'] or len(state['population'])!=6144:
        raise ValueError('Founder state authority changed')
    if any(v is not None for v in state['sources']):raise ValueError('Imported expert founders forbidden')
    from .genome import Individual
    from .founder_pool import validate
    for p in state['population']:validate(Individual.restore(p))
    return dict(state,source_sha256=job['source_sha256'],readmission=job['founder_readmission'])
