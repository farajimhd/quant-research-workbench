"""Prepare pinned node entries; installation explicitly starts a full-run node."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json,sys,platform,subprocess
from pathlib import Path
from .full_common import check_job
from research.vectorized_backtest.v42.distributed_assets import sha
from research.vectorized_backtest.v42.publication import write_json

def prepare_entries(job_path):
    job_path=Path(job_path);job=json.loads(job_path.read_bytes());check_job(job);root=Path(job['campaign']);commands={};manifest_sha=sha(Path(job['machines']['laptop']['code'])/'deployment.json')
    for node,m in job['machines'].items():
        code=Path(m['code']);entry=root/('entry-'+node+'.py')
        source=("import os,sys,json,hashlib,runpy,time,traceback,platform\nfrom pathlib import Path\nos.environ['PYTHONDONTWRITEBYTECODE']='1'\n"
          +"root=Path("+repr(str(root))+");code=Path("+repr(str(code))+")\nos.chdir(code);sys.path.insert(0,str(code))\n"
          +"if platform.node().lower()!="+repr(m['hostname'].lower())+":raise ValueError('Wrong execution host')\n"
          +"log=open(root/"+repr(node+'-run.log')+",'a',buffering=1);sys.stdout=log;sys.stderr=log\nos.environ['NUMBA_CACHE_DIR']=str(root/'numba-cache')\n"
          +"job=root/'full-job.json'\nif hashlib.sha256(job.read_bytes()).hexdigest()!="+repr(sha(job_path))+":raise ValueError('Pinned full job changed')\n"
          +"if hashlib.sha256((code/'deployment.json').read_bytes()).hexdigest()!="+repr(manifest_sha)+":raise ValueError('Pinned deployment changed')\n"
          +"deployment=json.loads((code/'deployment.json').read_bytes())\nif deployment['commit']!="+repr(job['commit'])+":raise ValueError('Deployment commit changed')\n"
          +"for name,expected in deployment['files'].items():\n if hashlib.sha256((code/name).read_bytes()).hexdigest()!=expected:raise ValueError('Deployment changed: '+name)\n"
          +"status='failed'\ntry:\n sys.argv=['campaign_runner','--job',str(job),'--node',"+repr(node)+"]\n runpy.run_module('research.vectorized_backtest.v56.campaign_runner',run_name='__main__');status='complete'\nexcept InterruptedError:\n status='interrupted';traceback.print_exc();raise\nexcept BaseException:\n traceback.print_exc();raise\nfinally:\n (root/"+repr(node+'-exit.json')+").write_text(json.dumps(dict(status=status,epoch=time.time())))\n")
        # Spawned replay lanes import the main entry as __mp_main__. Importing
        # that file must never start another full-campaign coordinator.
        source='def main():\n'+''.join('    '+line+'\n' for line in source.splitlines())+"\nif __name__=='__main__':\n    main()\n"
        if entry.exists() and entry.read_text()!=source:raise ValueError('Existing node entry differs')
        entry.write_text(source)
        commands[node]=subprocess.list2cmdline([m['python'],'-B','-m','research.vectorized_backtest.v56.campaign_launch','--job',str(job_path),'--node',node,'--install'])
    write_json(root/'launch-plan.json',dict(status='prepared; not started',commands=commands,working_directories={n:m['code'] for n,m in job['machines'].items()},stop='Create STOP under campaign or exchange; resume after removing STOP and auditing exact exited owners.'))
    return commands

def main():
    p=argparse.ArgumentParser();p.add_argument('--job',type=Path,required=True);p.add_argument('--node',choices=('laptop','workstation'));p.add_argument('--install',action='store_true');p.add_argument('--preflight',action='store_true');a=p.parse_args()
    j=json.loads(a.job.read_bytes());check_job(j)
    if a.preflight:
        if not a.node:p.error('--preflight requires --node')
        m=j['machines'][a.node]
        if platform.node().lower()!=m['hostname'].lower():raise ValueError('Wrong host')
        import torch
        free,total=torch.cuda.mem_get_info();result=dict(node=a.node,gpu=torch.cuda.get_device_name(),free_gib=free/2**30,total_gib=total/2**30,session_workers=1,global_session_workers=j['global_session_workers'] if a.node=='workstation' else 0,job_sha256=sha(a.job),training_sessions=len(j['training_days']),validation_sessions=len(j['validation_days']),started=False)
        write_json(Path(j['campaign'])/('preflight-'+a.node+'.json'),result);print(json.dumps(result));return
    if a.install:
        if not a.node:p.error('--install requires --node')
        m=j['machines'][a.node]
        if platform.node().lower()!=m['hostname'].lower():raise ValueError('Wrong host')
        from .scheduled_launch import install
        task='Codex-V56-full-'+Path(j['campaign']).name+'-'+a.node
        background=Path(m['python']).with_name('pythonw.exe')
        if not background.is_file():raise ValueError('Background Python runtime unavailable')
        print(install(task,str(background),Path(j['campaign'])/('entry-'+a.node+'.py'),m['code'],[],Path(m['code'])/'research/vectorized_backtest/v56/scheduled_launch.py'));return
    print(json.dumps(prepare_entries(a.job),indent=2))
if __name__=='__main__':main()
