"""Freeze a chronological, matched-budget study plan; never launch extra runs."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json,hashlib
from pathlib import Path
from .run_search import bind_dataset,source_hash,digest
from research.vectorized_backtest.v6.torch_backtest.runtime import require_runtime,write_json

def plan_study(training,seeds,validation=None):
    train=bind_dataset(Path(training),'training')
    if not seeds or len(seeds)!=len(set(seeds)) or any(type(s) is not int or s<0 for s in seeds):raise ValueError('Distinct nonnegative seeds required')
    holdout=None
    if validation:
        valid=bind_dataset(Path(validation),'validation')
        if max(train['days'])>=min(valid['days']):raise ValueError('Holdout must strictly follow training')
        holdout=dict(path=str(validation),days=valid['days'],manifest_sha256=hashlib.sha256(Path(validation).read_bytes()).hexdigest(),feedback_allowed=False)
    plan=dict(version='v29-study-plan-v1',source_sha256=source_hash(),training=str(training),training_days=train['days'],
        training_sha256=hashlib.sha256(Path(training).read_bytes()).hexdigest(),holdout=holdout,
        seeds=seeds,population=1024,initial_multiplier=6,local_generations=30,global_generations=50,
        methods=['frozen-v28-baseline','v29-recurrent-variable-size'],
        controls=['identical certificates','matched population and evaluation budgets','same seed list','freeze architecture before final holdout'],
        ablations=['disable delayed edges','fixed sizing heads','disable niche protection','disable homologous weight exchange'],
        ablation_execution_requires_separately_source_bound_implementations=True,launch_authorized=False,
        inference='Single-session or single-seed results do not establish generalization')
    return dict(plan=plan,plan_sha256=digest(plan))

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--training',type=Path,required=True);p.add_argument('--validation',type=Path)
    p.add_argument('--seeds',default='2020,2021,2022');p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    write_json(require_runtime(a.output)/'study-plan.json',plan_study(a.training,[int(s) for s in a.seeds.split(',')],a.validation))

if __name__=='__main__':main()
