"""Prepare an immutable 24/12 full job with measured host affinity."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json
from pathlib import Path
import numpy as np
from .identity import source_hash
from .full_common import check_job
from research.vectorized_backtest.v42.distributed_assets import sha
from research.vectorized_backtest.v42.publication import write_json


def assign(days,manifest,rates):
    costs={d:(len(json.loads((Path(manifest['history'])/d/'complete.json').read_bytes())['listing_ids'])*(json.loads((Path(manifest['inputs'])/d/'complete.json').read_bytes())['clocks']-1)) for d in days}
    load={n:0. for n in rates};out={n:[] for n in rates}
    for day in sorted(days,key=lambda d:(-costs[d],d)):
        node=min(rates,key=lambda n:(load[n]+costs[day]/rates[n],n));out[node].append(day);load[node]+=costs[day]/rates[node]
    return {n:sorted(v) for n,v in out.items()},load


def prepare(source,root,laptop_code,workstation_code,profiles):
    root=Path(root)
    if (root/'full-job.json').exists():raise ValueError('Existing full campaign; audit it rather than preparing a duplicate')
    original=json.loads(Path(source).read_bytes());train=json.loads(Path(original['training']).read_bytes());valid=json.loads(Path(original['validation']).read_bytes())
    from research.vectorized_backtest.v42.distributed_assets import receipt_inventory
    receipt_inventory(original,'laptop')
    rates={}
    for node,path in profiles.items():
        result=json.loads(Path(path).read_bytes())
        if not result['ownership_verified'] or not result['finite_financial_results']:raise ValueError('Unqualified host profile')
        rates[node]=result['composed_candidates']*result['clocks']*result['listing_width']/result['gpu_replay_seconds']
    training,training_load=assign(train['days'],train,rates);validation,validation_load=assign(valid['days'],valid,rates)
    machines={n:dict(original['machines'][n],code=str(code),days=dict(training=training[n],validation=validation[n])) for n,code in (('laptop',laptop_code),('workstation',workstation_code))}
    prior=json.loads((Path(original['campaign'])/'local/2026-08-13/checkpoint.json').read_bytes())['contract']
    job=dict(version='v48-full-v1',source_sha256=source_hash(),source_base_sha256=original['source_sha256'],source_job=str(source),source_job_sha256=sha(source),
        commit=json.loads((Path(laptop_code)/'deployment.json').read_bytes())['commit'],campaign=str(root),transport='D:/TradingML/runtimes/v48-bus-'+root.name,workstation_share=original['workstation_share'],machines=machines,
        training=original['training'],validation=original['validation'],training_sha256=original['training_sha256'],validation_sha256=original['validation_sha256'],
        assets=original['assets'],asset_sha256=original['asset_sha256'],initial_pools=original.get('initial_pools',{}),probe_root=str(Path(original['campaign'])/'initialization-probes'),
        population=1024,initial_multiplier=6,seed=2020,phase0_generations=50,phase1_generations=50,phase2_generations=100,
        objective=dict(drawdown_weight=.05,loss_weight=.75),execution=dict(prior['execution'],price_mode='bid-ask',maximum_quote_age_seconds=1.),selection=prior['selection'],batch_size=1024,
        profile_rates=rates,profile_files={n:dict(path=str(p),sha256=sha(p)) for n,p in profiles.items()},estimated_balanced_work=dict(training=training_load,validation=validation_load))
    check_job(job);root.mkdir(parents=True,exist_ok=True);write_json(root/'full-job.json',job)
    write_json(root/'manifest.json',dict(version='v48-full-v1',job=str(root/'full-job.json'),job_sha256=sha(root/'full-job.json'),source_sha256=job['source_sha256'],days=train['days'],validation_days=valid['days'],population=1024,phase0_generations=50,phase1_generations=50,phase2_generations=100,loss_weight=.75))
    return job


def main():
    p=argparse.ArgumentParser();p.add_argument('--source-job',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--laptop-code',type=Path,required=True);p.add_argument('--workstation-code',type=Path,required=True)
    p.add_argument('--laptop-profile',type=Path,required=True);p.add_argument('--workstation-profile',type=Path,required=True);a=p.parse_args()
    j=prepare(a.source_job,a.output,a.laptop_code,a.workstation_code,dict(laptop=a.laptop_profile,workstation=a.workstation_profile))
    print(json.dumps({n:m['days'] for n,m in j['machines'].items()},indent=2))


if __name__=='__main__':main()
