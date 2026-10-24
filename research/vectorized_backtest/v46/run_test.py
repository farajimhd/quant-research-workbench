"""Separate 50-generation laptop test using V42's exact first evaluated cohort."""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
os.environ['V46_MASK_QUOTES'] = '0'  # Faster measured resident-quote path; exact parity with masked loads.
for key in ('OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'OPENBLAS_NUM_THREADS'):
    os.environ.setdefault(key, '1')

import argparse
import json
import shutil
from pathlib import Path
import sys
import time
import traceback
import numpy as np
import psutil
from .identity import digest, source_hash
from research.vectorized_backtest.v42.publication import write_json
from research.vectorized_backtest.v6.torch_backtest.runtime import file_hash, require_runtime


def run(job_path, output, day, initial_pool=None):
    from research.vectorized_backtest.v42.run_search import source_hash as base_hash
    from research.vectorized_backtest.v42.calibration import load_probe
    from research.vectorized_backtest.v42.initial_population import initialize
    from research.vectorized_backtest.v42.distributed_assets import ensure_day
    from .local_runner import run_session
    job = json.loads(job_path.read_bytes())
    if base_hash() != job['source_sha256'] or file_hash(job['training']) != job['training_sha256']:
        raise ValueError('Original optimizer or training identity changed')
    original = Path(job['campaign']) / 'local' / day
    checkpoint = json.loads((original / 'checkpoint.json').read_bytes())
    if digest(checkpoint['state']) != checkpoint['state_sha256']:
        raise ValueError('Original local checkpoint changed')
    prior = checkpoint['contract']
    folder = original / 'generation-0001'
    seal = json.loads((folder / 'complete.json').read_bytes())
    if file_hash(folder / 'complete.json') != checkpoint['state']['seals']['1']:
        raise ValueError('Original initial seal changed')
    receipt = folder / day / 'receipt.json'
    if file_hash(receipt) != seal['receipt_sha256']:
        raise ValueError('Original initial financial results changed')
    if (prior['seed'], prior['population'], prior['initial_multiplier']) != (2020, 1024, 6):
        raise ValueError('Expected original V42 cohort budgets')
    probe_path = Path(job['campaign']) / 'initialization-probes' / (day + '.npz')
    probe = load_probe(probe_path, job['training'])
    if probe['metadata']['days'] != [day]:
        raise ValueError('Single-session training probe required')
    owner = dict(pid=os.getpid(), creation_time=psutil.Process().create_time(),
                 version='v46', campaign=str(output), day=day, generations=50,
                 source_sha256=source_hash(), base_source_sha256=base_hash())
    write_json(output / 'active.json', owner)
    write_json(output.parent / 'active-test.json', owner)

    def stage(text):
        print(text, flush=True)
        write_json(output / 'launch-status.json', dict(owner, stage=text, updated_epoch=time.time()))

    pool_path = output / 'initial-pool.json'
    proof_path = output / 'fairness-proof.json'
    if initial_pool is not None and not pool_path.exists():
        stage('Verify retained original-environment initial cohort against V42 seal')
        supplied = json.loads(initial_pool.read_bytes())
        expected_rng = np.random.default_rng(np.random.SeedSequence([2020, int(day.replace('-', ''))]))
        if (supplied['source_sha256'] != base_hash()
                or supplied['probe_sha256'] != probe['sha256']
                or len(supplied['population']) != 6144
                or digest(supplied['population']) != seal['population_sha256']
                or supplied['population_sha256'] != seal['population_sha256']
                or supplied['rng_before'] != expected_rng.bit_generator.state):
            raise ValueError('Supplied initial cohort differs from exact V42 initialization')
        shutil.copyfile(initial_pool, pool_path)
        write_json(proof_path, dict(initial_pool_sha256=file_hash(pool_path),
            population_sha256=supplied['population_sha256'], source_seal_sha256=file_hash(folder / 'complete.json'),
            source_receipt_sha256=file_hash(receipt), source_job_sha256=file_hash(job_path),
            exact_initial_candidate_count=6144, old_scores_imported=False, offspring_imported=False, old_financial_results_imported=False,
            prepared_pool_source=str(initial_pool), prepared_pool_sha256=file_hash(initial_pool)))
    if pool_path.exists():
        proof = json.loads(proof_path.read_bytes())
        if file_hash(pool_path) != proof['initial_pool_sha256']:
            raise ValueError('Preserved initial cohort changed')
        pool = json.loads(pool_path.read_bytes())
    else:
        stage('Reconstruct exact original 6144 programs; verify sealed population hash')
        rng = np.random.default_rng(np.random.SeedSequence([2020, int(day.replace('-', ''))]))
        before = rng.bit_generator.state
        os.environ.pop('V42_INITIAL_POOL', None)
        members, report = initialize(rng, 6144, probe=probe)
        payload = [m.payload() for m in members]
        if digest(payload) != seal['population_sha256']:
            raise ValueError('Reconstructed initial cohort differs from V42: '
                             + digest(payload) + ' != ' + seal['population_sha256'])
        pool = dict(population=payload, population_sha256=digest(payload), report=report,
                    rng_before=before, rng_after=rng.bit_generator.state,
                    source_sha256=base_hash(), probe_sha256=probe['sha256'])
        write_json(pool_path, pool)
        write_json(proof_path, dict(initial_pool_sha256=file_hash(pool_path),
            population_sha256=pool['population_sha256'], source_seal_sha256=file_hash(folder / 'complete.json'),
            source_receipt_sha256=file_hash(receipt), source_job_sha256=file_hash(job_path),
            exact_initial_candidate_count=6144, old_scores_imported=False, offspring_imported=False, old_financial_results_imported=False))
        del members, payload
    if pool['population_sha256'] != seal['population_sha256'] or pool['probe_sha256'] != probe['sha256']:
        raise ValueError('Initial cohort or probe changed')
    # The original cohort is reused, never its close-priced financial results.
    os.environ['V46_INITIAL_POOL'] = str(pool_path)
    os.environ['V46_INITIAL_POOL_SHA256'] = file_hash(pool_path)
    write_json(output/'initial-execution.json',dict(initial_evaluation_reused=False,reason='New bid/ask execution requires full replay',price_mode='bid-ask',maximum_quote_age_seconds=1.))
    stage('Stage certified August training session on laptop; no acquisition')
    staging_job = dict(job, campaign=str(output / 'staging'))
    ensure_day(staging_job, 'laptop', 'training', day)
    training_manifest = json.loads(Path(job['training']).read_bytes())
    cfg = dict(prior['dataset'], feature_cache=training_manifest.get('feature_cache'),
        authorization='v46-training', source_sha256=source_hash(),
        training_manifest=job['training'], initialization_probe=str(probe_path),
        execution=dict(prior['execution'],price_mode='bid-ask',maximum_quote_age_seconds=1.), selection=prior['selection'], backend=prior['backend'],
        device=prior['device'], batch_size=prior['batch_size'], capture_gib=job['machines']['laptop']['capture_gib'],
        host_reserve_gib=12., host_admission_gib=8., host_prefetch=False, shared_replay=False,
        maximum_data_gib=4., maximum_gate_gib=8., episode_capacity=65536, plan_limit=1,
        reuse_initial_manifest=None)
    stage('Run V46: 50 generations, population1024, exact V42 6144 cohort; bid/ask fills; global-profit elites + adaptive ticker/session crossover')
    result = run_session(cfg, day, output / 'local' / day, 50, 1024, 2020, initial_multiplier=6)
    write_json(output / 'exit.json', dict(result=result, epoch=time.time()))
    stage('Complete' if result['status'] == 'complete' else result['status'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--day', choices=['2026-08-13'], default='2026-08-13')
    parser.add_argument('--initial-pool', type=Path,
                        help='Original-environment cohort; must match the exact sealed V42 hash')
    parser.add_argument('--authorize-run', action='store_true', required=True)
    args = parser.parse_args()
    output = require_runtime(args.output)
    log = (output / 'run.log').open('a', encoding='utf-8', buffering=1)
    sys.stdout = log
    sys.stderr = log
    try:
        run(args.job, output, args.day, args.initial_pool)
    except BaseException:
        write_json(output / 'failure.json', dict(traceback=traceback.format_exc(), epoch=time.time()))
        traceback.print_exc()
        raise


if __name__ == '__main__':
    main()
