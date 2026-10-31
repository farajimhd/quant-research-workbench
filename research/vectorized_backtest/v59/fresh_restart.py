"""Prepare a fresh V59 crossing/order-state campaign; never inherit old fitness."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json
from pathlib import Path
from .identity import source_hash
from .orders import VERSION
from .global_contract import check
from research.vectorized_backtest.v42.publication import write_json


def prepare(previous_job,root,code):
    from research.vectorized_backtest.v6.torch_backtest.runtime import require_runtime
    previous_job=Path(previous_job);root=require_runtime(root);code=Path(code)
    if (root/'job.json').exists():raise ValueError('Preserve the existing campaign')
    job=json.loads(previous_job.read_bytes())
    if not (Path(job['campaign'])/'STOP').exists():raise ValueError('Stop the predecessor before preparing its replacement')
    for key in ('execution_restart','resume_transition','result_cache_transition','founder_restart'):
        job.pop(key,None)
    job.update(campaign=str(root),transport=str(root/'asset-bus'),code=str(code),
        commit=json.loads((code/'deployment.json').read_bytes())['commit'],source_sha256=source_hash(),
        fresh_initial_replay='crossing-orders-v3',execution=dict(job['execution'],order_mode=VERSION,maximum_quote_age_seconds=None))
    job['machines']['workstation']['code']=str(code)
    check(job);root.mkdir(parents=True,exist_ok=True);write_json(root/'job.json',job)
    write_json(root/'restart-verification.json',dict(previous_job=str(previous_job),resume_generation=0,
        fresh_candidates=6144,financial_results_reused=False,old_genomes_reused=False,first_evolution_generation=1,
        execution=VERSION,cost_function_changed=False))
    launch=f'''import os,sys,runpy
os.environ.update(PYTHONDONTWRITEBYTECODE='1',PYTHONUTF8='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1')
sys.path.insert(0,{str(code)!r})
if __name__=='__main__':
 log=open({str(root/'optimizer.log')!r},'a',encoding='utf8',buffering=1);sys.stdout=log;sys.stderr=log
 os.dup2(log.fileno(),1);os.dup2(log.fileno(),2)
 sys.argv=['run_train','--job',{str(root/'job.json')!r}]
 runpy.run_module('research.vectorized_backtest.v59.run_train',run_name='__main__')
'''
    progress=f'''import os,sys,runpy
os.environ.update(PYTHONDONTWRITEBYTECODE='1',PYTHONUTF8='1')
sys.path.insert(0,{str(code)!r})
if __name__=='__main__':
 sys.argv=['campaign_progress','--job',{str(root/'job.json')!r},'--view','global_sessions']
 runpy.run_module('research.vectorized_backtest.v59.campaign_progress',run_name='__main__')
'''
    python=r'C:\Users\Mehdi\miniconda3\envs\ml4t\python.exe'
    opening="import os,subprocess\nos.environ['PYTHONDONTWRITEBYTECODE']='1'\nif __name__=='__main__':\n subprocess.run("+repr(['wt.exe','-w','new','new-tab','--title','V59 | Fresh crossing strategies | generations 0-200',python,'-B',str(root/'progress.py')])+",check=True)\n"
    import ast
    for name,value in [('launch.py',launch),('progress.py',progress),('open-progress.py',opening)]:
        ast.parse(value);(root/name).write_text(value,encoding='utf8')
    return job


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--previous-job',required=True,type=Path)
    p.add_argument('--output',required=True,type=Path);p.add_argument('--code',required=True,type=Path)
    a=p.parse_args();j=prepare(a.previous_job,a.output,a.code);print(json.dumps(dict(campaign=j['campaign'],generation=0,founders=6144)))
