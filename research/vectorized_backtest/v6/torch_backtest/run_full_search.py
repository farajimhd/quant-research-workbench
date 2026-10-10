"""Explicit-budget V6 full-training optimization launcher; no validation path."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse
import json
from pathlib import Path
from .full_search import run_generations
from .resident_evaluator import ResidentSessionEvaluator
from .runtime import configure_caches,require_runtime
from .stability import LowerTailDollarObjective


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--sessions',type=Path,required=True)
    p.add_argument('--inputs',type=Path,required=True);p.add_argument('--structure',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    # No population/generation defaults: the measured run budget is an explicit choice.
    p.add_argument('--population',type=int,required=True);p.add_argument('--generations',type=int,required=True)
    p.add_argument('--batch-size',type=int,required=True);p.add_argument('--session-workers',type=int,required=True)
    p.add_argument('--holding-capacity',type=int,default=40);p.add_argument('--maximum-fills',type=int,default=16384)
    p.add_argument('--maximum-input-gib',type=float,default=4.);p.add_argument('--maximum-state-gib',type=float,default=4.)
    p.add_argument('--capture-variants',type=int,choices=(1,2),default=1)
    p.add_argument('--seed',type=int,default=2236)
    a=p.parse_args(argv)
    if not 1<=a.session_workers<=8:p.error('--session-workers must be 1..8')
    spec=json.loads(a.sessions.read_text())
    root=require_runtime(a.output);configure_caches(root/'cache')
    evaluate=ResidentSessionEvaluator(a.inputs,a.structure,batch_size=a.batch_size,holding_capacity=a.holding_capacity,
        maximum_fills=a.maximum_fills,maximum_input_gib=a.maximum_input_gib,maximum_state_gib=a.maximum_state_gib,capture_variants=a.capture_variants)
    try:
        return run_generations(spec,a.population,a.generations,evaluate,root,seed=a.seed,
            workers=a.session_workers,objective=LowerTailDollarObjective())
    finally:evaluate.close()


if __name__=='__main__':raise SystemExit(main())
