"""Two-host session evolution, followed by workstation-only parallel global replay."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
for k in ('OMP_NUM_THREADS','MKL_NUM_THREADS','OPENBLAS_NUM_THREADS'):os.environ.setdefault(k,'1')
import argparse,json,time,threading,queue,traceback
from pathlib import Path
from types import SimpleNamespace
import psutil
from .full_controller import Controller
from .full_common import config,check_job
from .identity import digest
from .campaign_export import publish as export_session,records as load_export
from research.vectorized_backtest.v42.distributed_assets import ensure_day,remote,sha,copy_verified
from research.vectorized_backtest.v42.publication import write_json
from research.vectorized_backtest.v42.distributed_storage import publish as publish_transport
from research.vectorized_backtest.v6.torch_backtest.materialize import owned_run

def exchange(job,node):return Path(job['transport']) if node=='workstation' else remote(job['transport'],job['workstation_share'])

def stop_check(roots):
    if any((Path(p)/'STOP').exists() for p in roots):raise InterruptedError('V56 full campaign stop requested')

class Evolution(Controller):
    def __init__(self,job,node,root,workers,stop_roots):
        self.job=job;self.node=node;self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True);self.workers=workers;self.stop_roots=stop_roots
        self.owner=dict(pid=os.getpid(),creation_time=psutil.Process().create_time());self.status='running';self.stage='Starting';self.view={};self.lock=threading.RLock()
        self.worker=SimpleNamespace(config={'output':str(self.root/'replays')},resident_results=None);self.lanes=None;self.lane_key=None;self.direct=None;self.events=queue.Queue(maxsize=1);self.halt=threading.Event()
    def publish(self,**values):
        with self.lock:
            self.view.update(values)
            write_json(self.root/'full-status.json',dict(status=self.status,stage=self.stage,node=self.node,owner=self.owner,updated_epoch=time.time(),total_generations=self.job['phase2_generations'],**self.view))
    def session_progress(self,event):self.publish(worker_progress=event)
    def begin_sessions(self,days):self.publish(active_sessions=days)
    def complete_sessions(self,count,seconds):self.publish(last_cohort_completed=count,last_cohort_seconds=seconds)
    def pump(self):
        while not self.halt.wait(1):
            try:event=self.events.get_nowait()
            except queue.Empty:event=None
            self.publish(**({'worker_progress':event} if event else {}))
    def close_lanes(self):
        if self.lanes:self.lanes.__exit__(None,None,None);self.lanes=None;self.lane_key=None
    def evaluate(self,role,days,members,generation):
        stop_check(self.stop_roots)
        if self.job['campaign_stage']=='global' and self.node!='workstation':raise ValueError('Global replays belong exclusively to workstation')
        expected=self.job['training_days'] if role=='training' else self.job['validation_days']
        if days!=expected or not days:raise ValueError('Replay split/day authority changed')
        if self.job['campaign_stage']=='session' and role!='training':raise ValueError('Local validation is forbidden')
        for day in days:ensure_day(self.job,self.node,role,day)
        out=Path(self.worker.config['output'])/role;token=digest([m.payload() for m in members])
        cache=Path('D:/TradingML/runtimes/v56c')/self.job['source_sha256'][:12]/self.node
        cfg=config(self.job,self.node,role);cfg.update(output=str(out),stop_roots=list(map(str,self.stop_roots)),boundary_stop=str(self.stop_roots[0]),compile_cache_root=str(cache))
        if self.workers==1:
            from .session_lanes import Worker
            key=(role,str(out))
            if self.direct is None or self.lane_key!=key:
                if self.direct:self.direct.close()
                import torch,gc
                self.direct=None;gc.collect()
                if torch.device(cfg['device']).type=='cuda':
                    torch.cuda.empty_cache()
                    cfg['lane_memory_bytes']=torch.cuda.mem_get_info(cfg['device'])[0]*.8
                else:cfg['lane_memory_bytes']=0
                self.direct=Worker(cfg,self.events,0);self.lane_key=key
            day=days[0];width=len(json.loads((Path(cfg['history'])/day/'complete.json').read_bytes())['listing_ids'])
            self.direct.run(day,None,[m.payload() for m in members],generation,token,width)
        else:
            from .session_lanes import SessionLanes
            key=(role,str(out))
            if self.lane_key!=key:
                self.close_lanes();self.lanes=SessionLanes(cfg,self.workers,self).__enter__();self.lane_key=key
            self.lanes.generation(days,members,generation,token)
        result=[];refs={}
        for day in days:
            p=out/f'generation-{generation:04d}'/day/'receipt.json';receipt=json.loads(p.read_bytes())
            if receipt['day']!=day or receipt['population_sha256']!=token or not receipt['full_session'] or receipt['execution']!=self.job['execution'] or receipt['split']!=role or receipt['validation_opened']!=(role=='validation') or any(x is not None for x in receipt['case_owners']):raise ValueError('Financial replay receipt differs')
            result.append(receipt['metrics']);refs[day]=dict(receipt=str(p),receipt_sha256=sha(p))
        return result,refs
    def run(self,exports):
        with owned_run(self.root,version='v56-campaign-evolution-v1'):
            write_json(self.root/'owner.json',self.owner);thread=threading.Thread(target=self.pump,daemon=True);thread.start()
            try:
                self.global_run(exports,self.job['training_days'],self.job['validation_days']);self.status='complete';self.stage='Complete'
            except BaseException:
                self.status='interrupted' if any((Path(p)/'STOP').exists() for p in self.stop_roots) else 'failed'
                write_json(self.root/'failure.json',dict(traceback=traceback.format_exc(),epoch=time.time()));raise
            finally:
                self.halt.set();thread.join(5)
                try:self.close_lanes()
                finally:
                    if self.direct:self.direct.close()
                    self.publish()

def probe(job,node,day):
    from research.vectorized_backtest.v42.calibration import load_probe
    p=Path(job['probe_root'])/(day+'.npz')
    if not p.exists():
        src=Path(job['source_probe_root'])/(day+'.npz')
        if not src.exists() and node=='laptop':src=remote(src,job['workstation_share'])
        copy_verified(src,p,job['session_probe_sha256'][day])
    if sha(p)!=job['session_probe_sha256'][day]:raise ValueError('Pinned session probe changed')
    if load_probe(p,job['training'])['metadata']['days']!=[day]:raise ValueError('Session probe leaked another day')
    return p

def run(job,node):
    check_job(job);root=Path(job['campaign']);root.mkdir(parents=True,exist_ok=True);bus=exchange(job,node);bus.mkdir(parents=True,exist_ok=True)
    stops=[root,bus];node_root=root/'nodes'/node;node_root.mkdir(parents=True,exist_ok=True)
    with owned_run(node_root,version='v56-full-node-v1'):
        completed=[];skipped=[];retried=[];failed=[];unit=None;owner=dict(pid=os.getpid(),creation_time=psutil.Process().create_time())
        def status(stage,active=None,**extra):
            publish_transport(bus,bus/(node+'-status.json'),dict(status=stage,node=node,owner=owner,active=active,completed=completed,queued=[d for d in job['machines'][node]['session_days'] if d not in completed and d!=active],skipped=skipped,retried=retried,failed=failed,updated_epoch=time.time(),**extra))
        try:
            for day in job['machines'][node]['session_days']:
                unit=day;stop_check(stops);status('session-stage',day);target=bus/'sessions'/day;local=root/'sessions'/day
                if (target/'export.json').exists():load_export(target,job,day);skipped.append(day)
                else:
                    if (local/'global/checkpoint.json').exists():retried.append(day)
                    ensure_day(job,node,'training',day);p=probe(job,node,day)
                    derived=dict(job,campaign_stage='session',training_days=[day],validation_days=[],phase2_generations=job['phase1_generations'],initialization_probe=str(p),initialization_probe_sha256=sha(p),parent_job_sha256=digest(job))
                    Evolution(derived,node,local,1,stops).run({});export_session(local,target,job,day)
                completed.append(day);unit=None;status('session-stage')
            if node=='laptop':status('complete');return
            while True:
                stop_check(stops)
                pending=[d for d in job['training_days'] if not (bus/'sessions'/d/'export.json').exists()]
                if not pending:break
                for n in job['machines']:
                    f=bus/(n+'-status.json')
                    if f.exists() and json.loads(f.read_bytes())['status']=='failed':raise RuntimeError('Session node failed: '+n)
                status('waiting-for-session-exports',pending_sessions=pending);time.sleep(2)
            exports={d:load_export(bus/'sessions'/d,job,d) for d in job['training_days']}
            for day in job['training_days']:probe(job,node,day)
            from research.vectorized_backtest.v42.probes import global_probe
            p=global_probe(job['training'],job['training_days'],job['probe_root'])
            derived=dict(job,campaign_stage='global',initialization_probe=str(p),initialization_probe_sha256=sha(p),parent_job_sha256=digest(job),promotion_exports={d:e['export_sha256'] for d,e in exports.items()})
            unit='global';status('global-stage',unit);Evolution(derived,node,root/'phase-2',job['global_session_workers'],stops).run(exports);unit=None;status('complete')
            write_json(root/'complete.json',dict(status='complete',session_generations=50,global_generations=50,global_complete_sha256=sha(root/'phase-2/global/complete.json'),validation='display-only',job_sha256=digest(job)))
        except BaseException:
            interrupted=any((Path(p)/'STOP').exists() for p in stops)
            if not interrupted:failed.append(unit or node)
            status('interrupted' if interrupted else 'failed',unit,traceback=traceback.format_exc());raise

def main():
    p=argparse.ArgumentParser();p.add_argument('--job',type=Path,required=True);p.add_argument('--node',choices=('laptop','workstation'),required=True);a=p.parse_args();job=json.loads(a.job.read_bytes())
    from research.vectorized_backtest.v42.windows_process import quiet_compilers
    quiet_compilers();m=job['machines'][a.node]
    import platform
    if platform.node().lower()!=m['hostname'].lower():raise ValueError('Wrong execution host')
    os.environ.update(V42_CPU_RESERVE_GIB=str(m['reserve_gib']),V42_CPU_ADMISSION_GIB=str(m['admission_gib']),V42_REPRODUCTION_WORKERS='1',V56_MASK_QUOTES='0')
    run(job,a.node)
if __name__=='__main__':main()
