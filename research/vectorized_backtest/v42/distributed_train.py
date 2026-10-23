"""Laptop authority for restart-safe V42 local100 -> global200 on two GPUs."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
for k in ('OMP_NUM_THREADS','MKL_NUM_THREADS','OPENBLAS_NUM_THREADS'):os.environ.setdefault(k,'1')
import argparse,json,time,threading,subprocess,sys,signal,hashlib
from pathlib import Path
from dataclasses import asdict
from concurrent.futures import ThreadPoolExecutor
import psutil
from .distributed_bus import Bus,read,digest
from .distributed_assets import sha,remote,receipt_inventory
from .publication import write_json
from .distributed_mirror import snapshot

class Coordinator:
    def __init__(self,job):self.job=job;self.bus=Bus(job);self.stamps={};self.sealed={}
    snapshot=snapshot
    def local_progress(self,key,day):
        claim=self.bus.root/'claims'/(key+'.json')/'owner.json'
        if not claim.exists():return
        node=read(claim)['node'];root=Path(self.job['campaign'])/'local'/day
        self.snapshot(node,root/'status.json',root/'status.json')
        if not (root/'status.json').exists():write_json(root/'status.json',dict(status='running',day=day,generation=0,completed_generations=0,total_generations=self.job['local_generations'],stage='Staging certified session data',machine=node,updated_epoch=time.time()))
        self.snapshot(node,root/'checkpoint.json',root/'checkpoint.json')
        if not (root/'checkpoint.json').exists():return
        stamp=(root/'checkpoint.json').stat().st_mtime_ns
        prior=self.sealed.get(day)
        if prior and prior[0]==stamp:return
        cp=read(root/'checkpoint.json');generation=cp['state']['completed'];self.sealed[day]=(stamp,generation)
        for g in dict.fromkeys((1,generation)):
            if g<1:continue
            folder=root/f'generation-{g:04d}'
            for name in ('scores.json','families.json','candidate-labels.json','complete.json','wall.json'):
                self.snapshot(node,folder/name,folder/name)
            self.snapshot(node,folder/day/'receipt.json',folder/day/'receipt.json')
    def locals(self,training,cfg):
        pending={};done={};root=Path(self.job['campaign'])
        for day in training['days']:
            key=self.bus.submit(dict(kind='local',day=day,role='training',training=self.job['training'],config=cfg,source_sha256=self.job['source_sha256']))
            pending[key]=day
        from .monitor import publish_snapshot
        while pending:
            for key,day in list(pending.items()):
                self.local_progress(key,day)
                result=self.bus.result(key)
                if result is None:continue
                if result['source_sha256']!=self.job['source_sha256']:raise ValueError('Local source changed')
                for item in result['files']:self.bus.mirror(result['node'],item['path'],item['sha256'])
                done[day]=result;del pending[key];self.bus.retire(key)
            state=publish_snapshot(root,training['days'],self.job['local_generations'])
            for day,status in state['workers'].items():
                match=next((key for key,d in pending.items() if d==day),None)
                if match and (self.bus.root/'claims'/(match+'.json')/'owner.json').exists():status['machine']=read(self.bus.root/'claims'/(match+'.json')/'owner.json')['node']
            state['distributed_workers']={node:read(self.bus.root/'heartbeats'/(node+'.json')) for node in self.job['machines'] if (self.bus.root/'heartbeats'/(node+'.json')).exists()}
            write_json(root/'status.json',state)
            self.health()
            if (root/'STOP').exists():raise InterruptedError('Coordinator stop requested')
            if pending:time.sleep(2)
        write_json(root/'phase1-distributed-proof.json',dict(source_sha256=self.job['source_sha256'],sessions={day:dict(node=r['node'],seconds=r['seconds'],seals=r['seals'],files=r['files']) for day,r in done.items()},generations=self.job['local_generations']))
    def health(self):
        for node in self.job['machines']:
            path=self.bus.root/'heartbeats'/(node+'.json')
            if not path.exists():continue
            h=read(path)
            if h.get('status')=='exited' or time.time()-h['epoch']>90:raise RuntimeError('Node agent unavailable: '+node)

def start_agents(job):
    """Operational tasks are installed externally; require their live heartbeats."""
    bus=Bus(job);deadline=time.time()+90
    while time.time()<deadline:
        ready=True
        for node in job['machines']:
            path=bus.root/'heartbeats'/(node+'.json')
            if not path.exists():ready=False;continue
            h=read(path)
            if h.get('status')=='exited' or time.time()-h['epoch']>30:ready=False
        if ready:return
        time.sleep(1)
    raise RuntimeError('Both node agents must be started before coordinator')

def main():
    p=argparse.ArgumentParser();p.add_argument('--job',type=Path,required=True);p.add_argument('--authorize-run',action='store_true');a=p.parse_args()
    if not a.authorize_run:p.error('--authorize-run required')
    job=read(a.job)
    from .run_search import bind_dataset,source_hash
    if source_hash()!=job['source_sha256']:raise ValueError('Coordinator immutable source changed')
    for key in ('training','validation','qualification'):
        if sha(job[key])!=job[key+'_sha256']:raise ValueError('Pinned '+key+' changed')
    q=read(job['qualification'])
    if not q['ready_for_full_run'] or q['source_sha256']!=source_hash() or not q['cross_gpu_financial_parity']:raise ValueError('Distributed qualification missing')
    if (job['local_generations'],job['global_generations'],job['population'],job['initial_multiplier'])!=(100,200,1024,6):raise ValueError('Authorized V42 budgets changed')
    os.environ.update(V42_CPU_RESERVE_GIB='12',V42_CPU_ADMISSION_GIB='8',V42_PREPARATION_WORKERS='2')
    receipt_inventory(job,'laptop');train=bind_dataset(job['training'],'training');valid=bind_dataset(job['validation'],'validation')
    if len(train['days'])!=24 or len(valid['days'])!=12 or max(train['days'])>=min(valid['days']):raise ValueError('Certified chronological 24/12 split required')
    start_agents(job)
    from research.vectorized_backtest.v6.torch_backtest.materialize import owned_run
    from .run_train import config as lane_config
    from types import SimpleNamespace
    cfg=lane_config(train,SimpleNamespace(training=Path(job['training']),batch_size=job['batch_size'],capture_gib=job['machines']['laptop']['capture_gib'],host_reserve_gib=12.,host_admission_gib=8.,shared_replay=False))
    root=Path(job['campaign']);coordinator=Coordinator(job)
    manifest=dict(version='v42-distributed-controller-v1',commit=job['commit'],source_sha256=job['source_sha256'],job_sha256=sha(a.job),days=train['days'],local_generations=100,global_generations=200,config=cfg,validation_feedback=False)
    if (root/'manifest.json').exists() and read(root/'manifest.json')!=manifest:raise ValueError('Exact coordinator resume contract changed')
    print('V42 distributed | 24 local x 100 -> global x 200 | population1024 initial6x | laptop + workstation',flush=True)
    with owned_run(root,version='v42-distributed-controller-v1'):
        write_json(root/'manifest.json',manifest);owner=dict(pid=os.getpid(),creation_time=psutil.Process().create_time(),campaign=str(root),source_sha256=job['source_sha256'],commit=job['commit'])
        write_json(root.parent.parent/'active-campaign.json',owner);write_json(root/'active.json',owner)
        def stop(*_):(root/'STOP').touch();(coordinator.bus.root/'STOP').touch()
        signal.signal(signal.SIGINT,stop)
        code=1
        try:
            coordinator.locals(train,cfg)
            from .probes import global_probe
            probe=global_probe(job['training'],train['days'],root/'initialization-probes')
            from .phase2 import run_global
            code=run_global(job['training'],job['validation'],root/'local',root/'global',1024,200,job['seed'],batch_size=job['batch_size'],session_workers=2,capture_gib=job['machines']['laptop']['capture_gib'],host_reserve_gib=12.,host_admission_gib=8.,initialization_probe=probe,distributed_job=str(a.job))
            write_json(root/'status.json',dict(status='complete' if code==0 else 'interrupted',stage='Distributed campaign complete' if code==0 else 'Stopped',completed=24,failed=0,global_output=str(root/'global')))
        except BaseException:
            import traceback
            write_json(root/'failure.json',dict(traceback=traceback.format_exc(),epoch=time.time()));raise
        finally:
            (coordinator.bus.root/'STOP').touch()
            from .preparation import close
            close();write_json(root/'exit.json',dict(exit_code=code,epoch=time.time()))
if __name__=='__main__':main()
