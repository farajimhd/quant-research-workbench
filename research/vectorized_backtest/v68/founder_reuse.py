"""Explicit read-only V63 founder authority for selection-only V67 restart."""
import json
from pathlib import Path
from .distributed_assets import sha
from .identity import digest


def authority(job):
    bound = job['selection_restart']
    source = Path(bound['job'])
    if sha(source) != bound['job_sha256']:
        raise ValueError('Founder source job changed')
    old = json.loads(source.read_bytes())
    for key in ('execution', 'execution_tail', 'strategy_contract', 'objective', 'score_version',
                'training_sha256', 'validation_sha256', 'asset_sha256', 'training_days',
                'validation_days', 'initialization_probe_sha256', 'seed', 'population', 'initial_multiplier'):
        if old[key] != job[key]:
            raise ValueError('Selection restart changed financial authority: '+key)
    folder = Path(old['campaign'])/'phase-2/global/generation-0000'
    if sha(folder/'complete.json') != bound['seal_sha256']:
        raise ValueError('Founder seal changed')
    seal = json.loads((folder/'complete.json').read_bytes())
    for name in ('training', 'validation', 'population'):
        if sha(folder/(name+'.json')) != seal[name+'_sha256']:
            raise ValueError('Founder sealed artifact changed: '+name)
    return old, folder


def seed(job):
    old, folder = authority(job)
    path = Path(old['campaign'])/'phase-2/global/initial-pool.json'
    if sha(path) != job['selection_restart']['initial_pool_sha256']:
        raise ValueError('Founder initial RNG/state changed')
    state = json.loads(path.read_bytes())
    population = json.loads((folder/'population.json').read_bytes())
    if state['population'] != population['population'] or state['ids'] != population['ids'] or state['completed'] != -1:
        raise ValueError('Founder initial state differs from replay')
    if len(state['population']) != 6144:
        raise ValueError('Complete founder population required')
    return state


def restore(job, role, days, token):
    _, folder = authority(job)
    record = json.loads((folder/(role+'.json')).read_bytes())
    if record['population_sha256'] != token or set(record['receipts']) != set(days):
        raise ValueError('Founder cohort/split mismatch')
    from .controller_metrics import compact
    metrics = []; refs = {}
    for day in days:
        ref = record['receipts'][day]; path = Path(ref['receipt'])
        if sha(path) != ref['receipt_sha256']:
            raise ValueError('Founder receipt changed: '+day)
        from .receipt_storage import read
        receipt = read(path)
        if (receipt['day'] != day or receipt['split'] != role or
            receipt['population_sha256'] != token or not receipt['full_session'] or
            receipt['execution'] != job['execution'] or receipt['validation_opened'] != (role == 'validation') or
            len(receipt['case_owners']) != 6144 or any(x is not None for x in receipt['case_owners'])):
            raise ValueError('Founder receipt identity mismatch')
        metrics.append(compact(receipt['metrics'])); refs[day] = ref
        del receipt
    return metrics, refs
