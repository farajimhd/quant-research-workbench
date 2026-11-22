"""Compare complete calibration trials with their parent and older reference."""
import argparse,json
from pathlib import Path
from ..v5.shards.phase1_contract import file_hash,write_json
from ..phase1_v20.compare import KEYS


def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--run',required=True);a=p.parse_args()
    directory=a.root/a.run;manifest=json.loads((directory/'manifest.json').read_text())
    refs={name:json.loads((a.root/path/'latest-metrics.json').read_text()) for name,path in
          (('parent','phase1-v20-joint-singlehead-20261121/joint'),('v18','phase1-v18-joint-20261121/joint'))}
    results=[]
    for arm in manifest['arms']:
        path=directory/arm;r=json.loads((path/'result.json').read_text())
        if file_hash(path/'checkpoint.pt')!=r['checkpoint_sha'] or r['curve'][-1]['step']!=manifest['steps']:raise ValueError('Incomplete/changed trial')
        m=r['curve'][-1]['metrics'];comparisons={}
        for name,reference in refs.items():
            ratio=sorted(((v/reference[k],k) for k,v in m.items() if k.startswith(('all_visible/raw_mae/','masked/raw_mae/')) and reference.get(k,0)>0),reverse=True)
            delta=sorted(((v-reference[k],k) for k,v in m.items() if k in reference and k.startswith(('all_visible/','activity/all_visible/')) and k.endswith(('moving_direction_pct','bin_accuracy_pct'))))
            comparisons[name]=dict(worst_mae_ratios=ratio[:20],worst_accuracy_delta_pp=delta[:20],mae_regressions_above_1pct=sum(x>1.01 for x,k in ratio),accuracy_regressions_above_half_pp=sum(x<-.5 for x,k in delta))
        result=dict(arm=arm,checkpoint_sha=r['checkpoint_sha'],seconds=r['seconds'],speed=r['cached_windows_per_second'],wandb_url=r['wandb_url'],metrics={k:m[k] for k in KEYS},comparisons=comparisons)
        results.append(result);print(arm,result['metrics'],flush=True)
    write_json(directory/'quality-comparison.json',dict(results=results,source=manifest['source'],scope='Development only; hard accuracy distinct from differentiable training surrogate'))


if __name__=='__main__':main()
