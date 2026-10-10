"""Profile one complete fixed-population evaluation through the real runner."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json
from pathlib import Path
from time import perf_counter
from .run_search import run,source_hash
from .evaluator import Execution
from research.vectorized_backtest.v6.torch_backtest.runtime import require_runtime,configure_caches,write_json,file_hash

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('config','qualification','output'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();config=json.loads(a.config.read_text());root=require_runtime(a.output)
    required={'inputs','history','feature_cache','population_size','batch_size','session_workers','seed','cost_bps','maximum_data_gib','maximum_gate_gib'}
    if set(config)!=required:raise ValueError('Explicit optimization evaluation contract required: '+str(sorted(required)))
    q=json.loads(a.qualification.read_text())
    if q.get('status')!='passed' or not q.get('full_session') or q.get('validation_opened') is not False or q.get('source_sha256')!=source_hash():raise ValueError('Same-code full-session qualification required')
    args=dict(config);args['execution']=Execution(cost_bps=args.pop('cost_bps'))
    configure_caches(root/'cache',recompile_limit=128);start=perf_counter()
    # One evaluation only: no mutation or next generation, all30 aggregation
    # and publication included. No sealed-validation entry point.
    code=run(output=root/'evaluation',generations=1,device='cuda',backend='compile',**args)
    write_json(root/'profile.json',dict(status='passed' if code==0 else 'stopped',wall_seconds=perf_counter()-start,
        config=config,config_sha256=file_hash(a.config),source_sha256=source_hash(),qualification_sha256=file_hash(a.qualification),
        optimization_started=False,validation_opened=False,measurement='cold full all30 fixed-population evaluation; includes scoring/publication'))
    return code
if __name__=='__main__':raise SystemExit(main())
