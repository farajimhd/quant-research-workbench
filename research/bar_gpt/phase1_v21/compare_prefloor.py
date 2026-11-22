"""Compare completed paired pre-floor and unchanged-objective trials."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse
import json
import math
from ..v5.shards.phase1_contract import file_hash,write_json
from pathlib import Path


def compare(old,new,step):
    ratios=sorted([(new[k]/v,k) for k,v in old.items() if v>0 and k.startswith(('all_visible/raw_mae/','masked/raw_mae/'))],reverse=True)
    direction=sorted([(new[k]-v,k) for k,v in old.items() if k.startswith(('all_visible/','masked/','activity/')) and k.endswith(('moving_direction_pct','bin_accuracy_pct'))])
    return dict(step=step,raw_groups=len(ratios),improved=sum(r<1 for r,k in ratios),
        raw_regressions_over_5pct=sum(r>1.05 for r,k in ratios),accuracy_regressions_over_1pp=sum(d< -1 for d,k in direction),
        worst_raw=ratios[:12],worst_accuracy=direction[:12],all_raw_ratios=ratios,all_accuracy_deltas=direction)


def main():
    p=argparse.ArgumentParser();p.add_argument('--run',type=Path,required=True)
    p.add_argument('--control',default='joint_repaired');p.add_argument('--candidate',default='joint_prefloor');a=p.parse_args()
    if a.control not in ('joint_repaired','joint_prefloor','joint_basis_prefloor','joint_basis_prefloor_slow_temporal','joint_basis_prefloor_range_balanced','joint_basis_prefloor_nonlinear32','joint_basis_prefloor_temporal_extra','joint_basis_prefloor_temporal_extra_fast') or a.candidate not in ('joint_prefloor','joint_basis_prefloor','joint_basis_prefloor_slow_temporal','joint_basis_prefloor_range_balanced','joint_basis_prefloor_nonlinear32','joint_basis_prefloor_temporal_extra','joint_basis_prefloor_temporal_extra_fast') or a.control==a.candidate:
        raise ValueError('Explicit supported comparison arms required')
    manifest=json.loads((a.run/'manifest.json').read_text())
    rows=[]
    for arm in (a.control,a.candidate):
        folder=a.run/arm;r=json.loads((folder/'result.json').read_text())
        if file_hash(folder/'checkpoint.pt')!=r['checkpoint_sha'] or r['curve'][-1]['step']!=manifest['steps']:
            raise ValueError('Incomplete or changed checkpoint')
        rows.append(r)
    c,n=rows
    initial_delta={k:n['curve'][0]['metrics'][k]-v for k,v in c['curve'][0]['metrics'].items() if k!='evaluation_seconds'}
    if a.candidate.startswith('joint_basis_prefloor'):
        parity=json.loads((a.run/a.candidate/'initial-parity.json').read_text())
        if [r['visible'] for r in parity]!=[16,64] or not all(r['price_exact'] for r in parity):raise ValueError('Missing basis initialization gate')
        # Real code-level parity is checked before training. Preserve all initial
        # metric deltas rather than hiding FP32 basis-conversion differences.
    elif c['curve'][0]['metrics']!=n['curve'][0]['metrics']:
        for k,v in c['curve'][0]['metrics'].items():
            if k!='evaluation_seconds' and not math.isclose(n['curve'][0]['metrics'][k],v,rel_tol=1e-8,abs_tol=1e-10):
                raise ValueError('Initial metric mismatch: '+k)
    comparisons=[]
    if len(c['curve'])!=len(n['curve']):raise ValueError('Missing validation point')
    for control,candidate in zip(c['curve'],n['curve']):
        if control['step']!=candidate['step']:raise ValueError('Unmatched validation steps')
        comparisons.append(compare(control['metrics'],candidate['metrics'],control['step']))
    result=dict(source=manifest['source'],comparisons=comparisons,
        initial_metric_deltas=initial_delta,
        versus_parent={r['arm']:[compare(r['curve'][0]['metrics'],point['metrics'],point['step']) for point in r['curve']] for r in rows},
        trials=[{k:r[k] for k in ('arm','checkpoint_sha','seconds','cached_windows_per_second','wandb_url')} for r in rows],
        scope='Matched development only; no full-run promotion. Accuracy includes low-count groups; inspect detailed counts before interpreting extremes.')
    write_json(a.run/'parent-aware-quality.json',result)
    print(json.dumps({k:v for k,v in comparisons[-1].items() if not k.startswith('all_')},indent=2))


if __name__=='__main__':main()
