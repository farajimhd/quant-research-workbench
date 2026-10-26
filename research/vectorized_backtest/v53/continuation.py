"""Receipt-only terminal-boundary reproduction and immutable budget extension."""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import argparse
import hashlib
import inspect
import json
from pathlib import Path
import shutil
import subprocess
import sys

EVIDENCE = ('training', 'validation', 'population', 'successor', 'reproduction',
            'births', 'evaluated-pedigree', 'offspring-audit', 'mutation-probe')
ENGINE = ('allocation.py', 'composition.py', 'cpu_work.py', 'data.py', 'evaluator.py',
          'financial.py', 'genome.py', 'identity.py', 'identity_cache.py', 'initial_population.py',
          'full_seed.py', 'offspring_audit.py', 'pilot.py',
          'lineage.py', 'local_runner.py', 'mask_genome.py', 'mutation_probe.py',
          'network.py', 'network_cuda.py', 'network_pack.py', 'objectives.py',
          'operators.py', 'ownership.py', 'protected_transition.py', 'replay_graph.py',
          'session_lanes.py', 'species.py', 'validation_cache.py', 'whole_network.py')


def read(path):
    return json.loads(Path(path).read_bytes())


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(16 * 1024**2), b''):
            h.update(chunk)
    return h.hexdigest()


def engine_hashes(code):
    folder = Path(code) / 'research/vectorized_backtest/v53'
    return {name: hashlib.sha256((folder/name).read_bytes().replace(b'\r\n', b'\n')).hexdigest()
            for name in ENGINE}


def financial_hashes():
    from research.vectorized_backtest.v53 import full_common
    return {name: hashlib.sha256(inspect.getsource(getattr(full_common, name)).replace('\r\n','\n').encode()).hexdigest()
            for name in ('aggregate', 'config', 'ranking', 'serializable')}


def boundary_state(job, state, ordered, panel, probe):
    """Produce the omitted successor once, using the caller's pinned engine."""
    import numpy as np
    from research.vectorized_backtest.v53.genome import Individual
    from research.vectorized_backtest.v53.local_runner import CompactPedigree
    from research.vectorized_backtest.v53.lineage import next_population, Selection
    from research.vectorized_backtest.v53.species import Species
    from research.vectorized_backtest.v53.validation_cache import validation_scope
    state = json.loads(json.dumps(state))
    members = [Individual.restore(p) for p in state['population']]
    ids = np.asarray(state['ids'])
    pedigree = CompactPedigree(3, state['pedigree'], state['next_serial'])
    species = Species(state['species'])
    rng = np.random.default_rng()
    rng.bit_generator.state = state['rng']
    generation = state['completed'] + 1
    with validation_scope():
        children, newids, telemetry = next_population(rng, members, ids, pedigree, [], generation,
            Selection(**job['selection']), session_values=panel['score'][None],
            session_eligible=panel['eligible'][None], global_values=panel['score'],
            global_eligible=panel['eligible'], target_size=job['population'],
            species=species, ordered=ordered, case_values=None, case_ids=None, probe=probe)
    births = [pedigree.records[int(i)] for i in newids
              if pedigree.records[int(i)]['generation'] == generation]
    pedigree.retain(newids)
    state.update(population=[m.payload() for m in children], ids=newids.tolist(),
        pedigree=list(pedigree.records.values()), next_serial=pedigree.next_serial,
        rng=rng.bit_generator.state, species=species.state)
    return state, births, telemetry


def export_context(source, output):
    """Invoked as a file in a fresh process, before importing either V53 package."""
    source = Path(source)
    job = read(source/'pilot-job.json')
    code = Path(job['code'])
    package = Path(__file__).resolve().parent
    sys.path[:] = [p for p in sys.path if Path(p or os.curdir).resolve() != package]
    sys.path.insert(0, str(code))
    os.chdir(code)
    import psutil
    from research.vectorized_backtest.v53.identity import digest, source_hash
    from research.vectorized_backtest.v53.full_common import aggregate, serializable, check_job
    from research.vectorized_backtest.v53.objectives import Objective
    from research.vectorized_backtest.v42.calibration import load_probe
    from research.vectorized_backtest.v42.publication import write_json
    from research.vectorized_backtest.v42.run_search import source_hash as base_hash
    if (source/'owner.lock').exists():
        raise ValueError('Source owner lock remains held')
    owner = read(source/'owner.json')
    try:
        if abs(psutil.Process(owner['pid']).create_time()-owner['creation_time']) < .01:
            raise ValueError('Exact source optimizer is still alive')
    except psutil.NoSuchProcess:
        pass
    if job['version'] != 'v53-pilot-v2' or job['phase2_generations'] != 10:
        raise ValueError('Only the completed risk-cost generation-10 pilot can extend')
    check_job(job)
    deployment = read(code/'deployment.json')
    for name, expected in deployment['files'].items():
        if sha(code/name) != expected:
            raise ValueError('Pinned parent deployment changed: '+name)
    cp_path = source/'global/checkpoint.json'
    cp = read(cp_path)
    if cp['state']['completed'] != 10 or digest(cp['state']) != cp['state_sha256']:
        raise ValueError('Source terminal checkpoint changed')
    if cp['contract']['source_sha256'] != source_hash() or cp['contract']['objective'] != job['objective']:
        raise ValueError('Parent checkpoint source/cost contract differs')
    if (sha(cp_path) != read(source/'global/complete.json')['checkpoint_sha256']
        or sha(source/'global/complete.json') != read(source/'complete.json')['global_complete_sha256']):
        raise ValueError('Source completion linkage changed')
    for g, expected in cp['state']['seals'].items():
        folder = source/'global'/f'generation-{int(g):04d}'
        if sha(folder/'complete.json') != expected:
            raise ValueError('Parent generation seal changed')
        seal = read(folder/'complete.json')
        for name in EVIDENCE:
            if sha(folder/(name+'.json')) != seal[name.replace('-', '_')+'_sha256']:
                raise ValueError('Parent evidence changed: '+name)
        for name in ('training.json', 'mutation-probe.json'):
            for ref in read(folder/name)['receipts'].values():
                if sha(ref['receipt']) != ref['receipt_sha256']:
                    raise ValueError('Parent financial receipt changed')
    folder = source/'global/generation-0010'
    if read(folder/'reproduction.json')['telemetry'] != dict(reproduction_performed=False):
        raise ValueError('Source boundary already reproduced; refusing a second birth')
    frozen = read(folder/'successor.json')
    if digest(dict(population=cp['state']['population'], ids=cp['state']['ids'],
                   rng=cp['state']['rng'])) != frozen['sha256']:
        raise ValueError('Terminal population/RNG differs from its frozen successor')
    training = read(folder/'training.json')
    if training['ids'] != cp['state']['ids'] or training['population_sha256'] != digest(cp['state']['population']):
        raise ValueError('Terminal score population changed')
    ordered = []
    for day in job['training_days']:
        receipt = read(training['receipts'][day]['receipt'])
        if (receipt['execution'] != job['execution'] or not receipt['full_session']
            or receipt['split'] != 'training' or receipt['validation_opened']
            or receipt['population_sha256'] != training['population_sha256']):
            raise ValueError('Terminal replay contract changed')
        ordered.append(receipt['metrics'])
    panel = aggregate(ordered, Objective(**job['objective']))
    if digest(serializable(panel)) != digest(training['panel']):
        raise ValueError('Reconstructed terminal fitness differs')
    probe = load_probe(job['initialization_probe'], job['training'])
    state, births, telemetry = boundary_state(job, cp['state'], ordered, panel, probe)
    result = dict(version='v53-boundary-context-v1', parent=str(source),
        parent_checkpoint_sha256=sha(cp_path), parent_job_sha256=sha(source/'pilot-job.json'),
        parent_source_sha256=source_hash(), base_source_sha256=base_hash(),
        engine=engine_hashes(code), financial=financial_hashes(), completed=10, next_generation=11,
        state=state, state_sha256=digest(state), births=births, telemetry=telemetry,
        market_replay_performed=False)
    write_json(output, result)
    print(json.dumps(dict(exported=str(output), next_generation=11,
                         state_sha256=result['state_sha256'], births=len(births))))


def verify(job):
    from research.vectorized_backtest.v53.identity import digest
    bound = job.get('continuation')
    if not bound:
        raise ValueError('Continuation proof required')
    if sha(bound['context']) != bound['context_sha256']:
        raise ValueError('Continuation context hash changed')
    context = read(bound['context'])
    if (context['completed'], context['next_generation'], context['market_replay_performed']) != (10, 11, False):
        raise ValueError('Invalid continuation boundary')
    if digest(context['state']) != context['state_sha256']:
        raise ValueError('Continuation state changed')
    if context['state']['completed'] != 10:
        raise ValueError('Continuation completion boundary changed')
    parent = Path(context['parent'])
    if (sha(parent/'global/checkpoint.json') != context['parent_checkpoint_sha256']
        or sha(parent/'pilot-job.json') != context['parent_job_sha256']):
        raise ValueError('Continuation parent changed')
    from research.vectorized_backtest.v42.run_search import source_hash as base_hash
    if (engine_hashes(job['code']) != context['engine'] or financial_hashes() != context['financial']
        or base_hash() != context['base_source_sha256']):
        raise ValueError('Continuation numerical engine differs')
    prior = read(parent/'pilot-job.json')
    for key in ('objective','execution','selection','seed','population','initial_multiplier',
                'training_days','validation_days','training_sha256','validation_sha256',
                'initialization_probe_sha256','mutation_probe_size','comparison'):
        if prior.get(key) != job.get(key):
            raise ValueError('Continuation contract differs: '+key)
    return context


def prepare(source, output, code, generations=50):
    from research.vectorized_backtest.v6.torch_backtest.runtime import require_runtime
    from research.vectorized_backtest.v53.identity import source_hash
    from research.vectorized_backtest.v53.full_controller import checkpoint_contract
    from research.vectorized_backtest.v53.full_common import check_job
    from research.vectorized_backtest.v42.publication import write_json, write_checkpoint
    source, code = Path(source), Path(code)
    output = require_runtime(output)
    if (output/'pilot-job.json').exists() or (output/'global/checkpoint.json').exists():
        raise ValueError('Existing continuation must be inspected, not replaced')
    if generations != 50:
        raise ValueError('This extension is authorized through generation 50')
    context_path = output/'boundary-context.json'
    if context_path.exists():
        raise ValueError('Existing boundary context must be inspected before retry')
    subprocess.run([sys.executable, '-B', str(Path(__file__).resolve()),
        '--export-context', str(source), '--context-output', str(context_path)], check=True)
    job = read(source/'pilot-job.json')
    job.update(version='v53-pilot-v3', campaign=str(output), code=str(code),
        source_sha256=source_hash(), phase2_generations=generations,
        commit=read(code/'deployment.json')['commit'], transport=str(output/'asset-bus'),
        continuation=dict(context=str(context_path), context_sha256=sha(context_path)))
    job['machines']['laptop']['code'] = str(code)
    context = verify(job)
    check_job(job)
    for g, expected in context['state']['seals'].items():
        prior = source/'global'/f'generation-{int(g):04d}'
        target = require_runtime(output/'global'/prior.name)
        for name in (*EVIDENCE, 'complete'):
            src, dst = prior/(name+'.json'), target/(name+'.json')
            if dst.exists():raise ValueError('Historical destination already exists')
            shutil.copy2(src, dst)
            if sha(src) != sha(dst):raise ValueError('Historical evidence copy changed')
        if sha(target/'complete.json') != expected:raise ValueError('Copied seal differs')
    shutil.copy2(source/'global/initial-pool.json', output/'global/initial-pool.json')
    if sha(source/'global/initial-pool.json') != sha(output/'global/initial-pool.json'):
        raise ValueError('Founder evidence copy changed')
    write_checkpoint(output/'global/checkpoint.json',
        checkpoint_contract(job, job['training_days'], job['validation_days']), context['state'])
    write_json(output/'pilot-job.json', job)
    write_json(output/'manifest.json', dict(version=job['version'], parent=str(source),
        resume_after_generation=10, first_new_generation=11, target_generations=50,
        context_sha256=job['continuation']['context_sha256'], source_sha256=job['source_sha256'],
        commit=job['commit'], validation_opened=False, objective=job['objective']))
    print(json.dumps(dict(campaign=str(output), resume_after=10, target=50)))
    return job


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path)
    p.add_argument('--output', type=Path)
    p.add_argument('--code', type=Path)
    p.add_argument('--generations', type=int, default=50)
    p.add_argument('--export-context', type=Path)
    p.add_argument('--context-output', type=Path)
    a = p.parse_args()
    if a.export_context:
        if not a.context_output:p.error('--context-output is required')
        export_context(a.export_context, a.context_output)
    else:
        if not all((a.source, a.output, a.code)):p.error('--source, --output and --code are required')
        prepare(a.source, a.output, a.code, a.generations)


if __name__ == '__main__':
    main()
