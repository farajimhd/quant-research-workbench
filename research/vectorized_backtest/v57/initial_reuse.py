"""Restore original V53 founder genomes/RNG; require fresh financial replays."""
import hashlib,json
from pathlib import Path
from .identity import digest

MATH=('data.py','evaluator.py','network.py','network_cuda.py','network_pack.py','operators.py','protected_transition.py','replay_graph.py')
def read(p):return json.loads(Path(p).read_bytes())
def sha(p):
    h=hashlib.sha256()
    with Path(p).open('rb') as f:
        for chunk in iter(lambda:f.read(1024**2),b''):h.update(chunk)
    return h.hexdigest()
def numerical_hashes(code,version):
    root=Path(code)/'research/vectorized_backtest'/version
    return {name:hashlib.sha256((root/name).read_text(encoding='utf-8-sig').replace('v53','v57').replace('V53','V57').replace('from research.vectorized_backtest.v42.config import','from .config import').encode()).hexdigest() for name in MATH}

def bind(source,job):
    source=Path(source);prior=read(source/'pilot-job.json')
    if prior['version']!='v53-pilot-v2' or not (source/'complete.json').exists():raise ValueError('Completed V53 risk-cost founder source required')
    for key in ('seed','population','initial_multiplier','training_days','validation_days','training_sha256','initialization_probe_sha256','objective'):
        if prior[key]!=job[key]:raise ValueError('Founder source contract differs: '+key)
    folder=source/'global/generation-0001';seal=read(folder/'complete.json');training=read(folder/'training.json');population=read(folder/'population.json');initial=read(source/'global/initial-pool.json')
    if sha(folder/'population.json')!=seal['population_sha256'] or sha(folder/'training.json')!=seal['training_sha256']:raise ValueError('Founder seal changed')
    if digest(initial['population'])!=population['population_sha256'] or training['population_sha256']!=population['population_sha256'] or initial['ids']!=training['ids']:raise ValueError('Founder population alignment changed')
    if len(initial['population'])!=6144 or initial['next_serial']!=6144:raise ValueError('Exactly the original 6x founders required')
    refs=training['receipts']
    if set(refs)!={'2026-08-13'}:raise ValueError('Only Aug13 founder receipts may be reused')
    # Genomes/RNG only: execution changed, so every founder is replayed.
    math=numerical_hashes(job['code'],'v57')
    # Shared normalization/feature/financial dependencies must be byte-identical.
    deployment=read(Path(prior['code'])/'deployment.json');dependencies={}
    prefixes=tuple(f'research/vectorized_backtest/v{v}/' for v in (6,7,27,42))
    for name,expected in deployment['files'].items():
        if name.startswith(prefixes) and name.endswith('.py'):
            if sha(Path(prior['code'])/name)!=expected or sha(Path(job['code'])/name)!=expected:raise ValueError('Founder dependency differs: '+name)
            dependencies[name]=expected
    bound=dict(source=str(source),source_job_sha256=sha(source/'pilot-job.json'),initial_pool=str(source/'global/initial-pool.json'),initial_pool_sha256=sha(source/'global/initial-pool.json'),
        population_file=str(folder/'population.json'),population_file_sha256=sha(folder/'population.json'),training_file=str(folder/'training.json'),training_file_sha256=sha(folder/'training.json'),
        seal_file=str(folder/'complete.json'),seal_file_sha256=sha(folder/'complete.json'),population_sha256=population['population_sha256'],rng_sha256=digest(initial['rng']),
        receipts=refs,numerical=math,dependencies=dependencies,founders=6144,source_model_version='v53',market_replays_required=6144,financial_receipt_reuse=False)
    job['founder_reuse']=bound;verify(job);return bound

def verify(job):
    b=job.get('founder_reuse')
    if not b or b['founders']!=6144 or b['market_replays_required']!=6144:raise ValueError('Authorized 6144-founder replay binding required')
    for key in ('initial_pool','population_file','training_file','seal_file'):
        if sha(b[key])!=b[key+'_sha256']:raise ValueError('Bound founder evidence changed: '+key)
    prior=read(Path(b['source'])/'pilot-job.json')
    if sha(Path(b['source'])/'pilot-job.json')!=b['source_job_sha256']:raise ValueError('Founder source job changed')
    if dict(prior['execution'],risk_limits_enabled=False)!=job['execution'] or prior['objective']!=job['objective']:raise ValueError('Founder execution or objective differs')
    if numerical_hashes(job['code'],'v57')!=b['numerical']:raise ValueError('Founder reuse numerical engine changed')
    for name,expected in b['dependencies'].items():
        if sha(Path(job['code'])/name)!=expected:raise ValueError('Founder dependency changed: '+name)
    for ref in b['receipts'].values():
        if sha(ref['receipt'])!=ref['receipt_sha256']:raise ValueError('Founder financial receipt changed')
    return b

def import_receipt(*args,**kwargs):
    raise ValueError('V57 changed execution: prior financial receipts cannot be reused')
