"""CPU-only paired range-bin audit of immutable saved evaluation receipts."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np


def compare(before, after):
    rows=[]
    for mode in ('masked','all_visible','bar_probe'):
        a,b=before[mode]['moves'],after[mode]['moves']
        for key in ('bins','families','timeframes'):
            if a[key]!=b[key]:raise ValueError('Changed metric schema')
        x,y=(np.asarray(q['confusion']['range'],dtype=np.float64) for q in (a,b))
        if x.shape!=(6,3,11,11) or y.shape!=x.shape:raise ValueError('Unexpected confusion shape')
        if not all(np.isfinite(q).all() and (q>=0).all() for q in (x,y)):raise ValueError('Invalid counts')
        if not np.array_equal(x.sum(-1),y.sum(-1)):raise ValueError('Unmatched target counts')
        # Valid financial ranges should be nonnegative. Do not discard violations.
        for f,frame in enumerate(a['timeframes']):
            for j,family in enumerate(a['families']):
                for k,label in enumerate(a['bins']):
                    n=x[f,j,k].sum()
                    if not n:continue
                    old,new=x[f,j,k],y[f,j,k]
                    rows.append(dict(mode=mode,timeframe=frame,family=family,target_bin=label,count=int(n),
                        before_correct_pct=100*old[k]/n,after_correct_pct=100*new[k]/n,
                        accuracy_delta_pp=100*(new[k]-old[k])/n,
                        before_predicted_counts=old.tolist(),after_predicted_counts=new.tolist()))
    return rows


def main():
    p=argparse.ArgumentParser();p.add_argument('--before',type=Path,required=True)
    p.add_argument('--after',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    if a.output.exists():raise FileExistsError(a.output)
    payloads=[q.read_bytes() for q in (a.before,a.after)]
    result=dict(inputs=[dict(path=str(q),sha256=hashlib.sha256(v).hexdigest()) for q,v in zip((a.before,a.after),payloads)],
        rows=compare(*(json.loads(v) for v in payloads)),
        scope='Saved validation confusion counts only. Bar probe uses fixed legacy teacher decoder; drift does not prove loss of recoverable information. No training, fitting or data reread.')
    a.output.write_text(json.dumps(result,indent=2))
    print(json.dumps([{k:v for k,v in r.items() if not k.endswith('counts')}
        for r in sorted(result['rows'],key=lambda r:r['accuracy_delta_pp'])[:8]],indent=2))


if __name__=='__main__':main()
