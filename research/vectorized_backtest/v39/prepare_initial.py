"""Prepare this experiment's fresh seeds once, before bounded GPU checks."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,copy
from pathlib import Path
import numpy as np
from .probes import local_probe
from .calibration import load_probe
from .initial_population import initialize
from .run_search import source_hash,digest
from research.vectorized_backtest.v6.torch_backtest.runtime import require_runtime,write_json


def main():
    p=argparse.ArgumentParser();p.add_argument('--training',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    root=require_runtime(a.output)
    source=source_hash()
    if os.environ.get('V39_INITIAL_POOL'):raise ValueError('Preparation must create fresh seeds')
    path=local_probe(a.training,'2026-08-04',root/'initialization-probes')
    probe=load_probe(path,a.training);rng=np.random.default_rng(np.random.SeedSequence([2020,20260804]));before=copy.deepcopy(rng.bit_generator.state)
    print('Preparing 3,072 calibrated random seeds and 3,072 causal templates',flush=True)
    members,report=initialize(rng,6144,probe=probe);payload=[m.payload() for m in members]
    if source_hash()!=source:raise ValueError('Source changed during initialization preparation')
    write_json(root/'initial-pool.json',dict(source_sha256=source,probe_sha256=probe['sha256'],rng_before=before,rng_after=rng.bit_generator.state,
        population=payload,population_sha256=digest(payload),report=report,financial_results_imported=False))
    print('Fresh initialization sealed:',root/'initial-pool.json',flush=True)


if __name__=='__main__':main()
