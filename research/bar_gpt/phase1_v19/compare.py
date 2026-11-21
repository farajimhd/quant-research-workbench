"""Expose every regression; never promote by aggregate loss alone."""
import argparse,hashlib,json,math
from pathlib import Path
from ..v5.shards.phase1_contract import file_hash,write_json


def compare(candidate,reference):
    errors=[];directions=[];missing=[]
    for key,old in reference.items():
        if not key.startswith(('masked/','all_visible/','activity/masked/','activity/all_visible/')):continue
        error='/raw_mae/' in key or key.endswith(('/raw_mae','/log_mae'))
        direction=key.endswith(('/moving_direction_pct','/balanced_direction_pct','/direction_pct'))
        if not error and not direction:continue
        if key not in candidate or not math.isfinite(candidate[key]):missing.append(key);continue
        new=candidate[key]
        if error:errors.append(dict(metric=key,reference=old,candidate=new,ratio=new/max(abs(old),1e-12),regression=new>old+1e-10))
        else:directions.append(dict(metric=key,reference=old,candidate=new,change_pp=new-old,regression=new<old-1e-10))
    if not errors or not directions:raise ValueError('Incomplete comparison')
    errors.sort(key=lambda r:r['ratio'],reverse=True);directions.sort(key=lambda r:r['change_pp'])
    return dict(strict_no_regression=not missing and not any(r['regression'] for r in errors+directions),
                missing=missing,error_groups=len(errors),direction_groups=len(directions),
                error_regressions=sum(r['regression'] for r in errors),direction_regressions=sum(r['regression'] for r in directions),
                errors=errors,directions=directions,
                note='Same fixed population and masks required. Full qualification is separate. No tolerance hides a regression.')


def main():
    p=argparse.ArgumentParser();p.add_argument('--candidate',type=Path,required=True);p.add_argument('--reference',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    def load(path):
        payload=path.read_bytes();r=json.loads(payload)
        return r.get('metrics',r),hashlib.sha256(payload).hexdigest()
    candidate,csha=load(a.candidate);reference,rsha=load(a.reference)
    r=compare(candidate,reference);r.update(candidate=str(a.candidate),reference=str(a.reference),candidate_sha=csha,reference_sha=rsha)
    write_json(a.output,r)
    print(json.dumps({k:v for k,v in r.items() if k not in ('errors','directions')},indent=2))


if __name__=='__main__':main()
