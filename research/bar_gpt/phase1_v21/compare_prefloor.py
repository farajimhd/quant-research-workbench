"""Compare completed paired pre-floor and unchanged-objective trials."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse
import json
import math
from ..v5.shards.phase1_contract import file_hash,write_json
from pathlib import Path


def main():
    p=argparse.ArgumentParser();p.add_argument('--run',type=Path,required=True);a=p.parse_args()
    manifest=json.loads((a.run/'manifest.json').read_text())
    rows=[]
    for arm in ('joint_repaired','joint_prefloor'):
        folder=a.run/arm;r=json.loads((folder/'result.json').read_text())
        if file_hash(folder/'checkpoint.pt')!=r['checkpoint_sha'] or r['curve'][-1]['step']!=manifest['steps']:
            raise ValueError('Incomplete or changed checkpoint')
        rows.append(r)
    c,n=rows
    if c['curve'][0]['metrics']!=n['curve'][0]['metrics']:
        for k,v in c['curve'][0]['metrics'].items():
            if k!='evaluation_seconds' and not math.isclose(n['curve'][0]['metrics'][k],v,rel_tol=1e-8,abs_tol=1e-10):
                raise ValueError('Initial metric mismatch: '+k)
    comparisons=[]
    for control,candidate in zip(c['curve'],n['curve']):
        if control['step']!=candidate['step']:raise ValueError('Unmatched validation steps')
        old=control['metrics'];new=candidate['metrics']
        ratios=sorted([(new[k]/v,k) for k,v in old.items() if v>0 and k.startswith(('all_visible/raw_mae/','masked/raw_mae/'))],reverse=True)
        direction=sorted([(new[k]-v,k) for k,v in old.items() if k.startswith(('all_visible/','masked/','activity/')) and k.endswith(('moving_direction_pct','bin_accuracy_pct'))])
        comparisons.append(dict(step=control['step'],raw_groups=len(ratios),improved=sum(r<1 for r,k in ratios),
            raw_regressions_over_5pct=sum(r>1.05 for r,k in ratios),accuracy_regressions_over_1pp=sum(d< -1 for d,k in direction),
            worst_raw=ratios[:12],worst_accuracy=direction[:12],all_raw_ratios=ratios,all_accuracy_deltas=direction))
    result=dict(source=manifest['source'],comparisons=comparisons,
        trials=[{k:r[k] for k in ('arm','checkpoint_sha','seconds','cached_windows_per_second','wandb_url')} for r in rows],
        scope='Matched development only; no full-run promotion. Accuracy includes low-count groups; inspect detailed counts before interpreting extremes.')
    write_json(a.run/'paired-quality.json',result)
    print(json.dumps({k:v for k,v in comparisons[-1].items() if not k.startswith('all_')},indent=2))


if __name__=='__main__':main()
