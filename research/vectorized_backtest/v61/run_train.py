"""Run a hash-pinned fresh V60 Aug13 laptop experiment."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json,hashlib,sys
from pathlib import Path

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--job',type=Path,required=True);a=p.parse_args()
    job=json.loads(a.job.read_bytes());code=Path(job['code']);root=Path(job['campaign'])
    deployment=json.loads((code/'deployment.json').read_bytes())
    if deployment['commit']!=job['commit']:raise ValueError('Pinned deployment commit differs')
    for name,expected in deployment['files'].items():
        if hashlib.sha256((code/name).read_bytes()).hexdigest()!=expected:raise ValueError('Pinned source changed: '+name)
    os.environ.update(NUMBA_CACHE_DIR=str(root/'numba-cache'),TORCHINDUCTOR_CACHE_DIR=str(root/'compiler-cache/inductor'),TRITON_CACHE_DIR=str(root/'compiler-cache/triton'))
    print(f'V60: {job["phase2_generations"]} generations on {job["training_days"]}; output {root}',flush=True)
    if job.get('version')=='v60-global-v1':
        from .global_runner import run
        run(job)
        return
    from .pilot import main as run
    sys.argv=['pilot','--job',str(a.job)];run()

if __name__=='__main__':main()
