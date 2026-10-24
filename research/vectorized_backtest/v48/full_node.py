"""One persistent GPU owner per host; paired local phases and global replay."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
for k in ('OMP_NUM_THREADS','MKL_NUM_THREADS','OPENBLAS_NUM_THREADS'):os.environ.setdefault(k,'1')
import argparse,json,time,threading,queue,traceback,gc
from pathlib import Path
import psutil
from .full_common import check_job,config
from .identity import digest
from research.vectorized_backtest.v42.distributed_bus import Bus,read
from research.vectorized_backtest.v42.distributed_assets import ensure_day,sha,copy_verified
from research.vectorized_backtest.v42.publication import write_json
from research.vectorized_backtest.v6.torch_backtest.materialize import owned_run


def exact_live(owner):
    try:return abs(psutil.Process(owner['pid']).create_time()-owner['creation_time'])<.01
    except psutil.NoSuchProcess:return False


class Agent:
    def __init__(self,job,node):
        self.job=job;self.node=node;self.bus=Bus(job,node);self.owner=dict(pid=os.getpid(),creation_time=psutil.Process().create_time())
        self.current=None;self.stage='Ready';self.events=queue.Queue(maxsize=1);self.stop=threading.Event();self.worker=None;self.worker_role=None;self.last_day=None;self.displayed=set()

    def heartbeat(self):
        while not self.stop.wait(1):
            event={}
            try:
                while True:event=self.events.get_nowait()
            except queue.Empty:pass
            if self.current:
                key,request=self.current
                if (self.bus.root/'STOP').exists() or (Path(self.job['campaign'])/'STOP').exists():
                    if request['kind']=='session':
                        root=Path(self.job['campaign'])/'sessions'/request['day'];write_json(root/'STOP',dict(reason='Full campaign stop'))
                        active=root/'active.json'
                        if active.exists():write_json(Path(read(active)['campaign'])/'local'/request['day']/'STOP',dict(reason='Full campaign stop'))
                    elif self.worker:write_json(Path(self.worker.config['output'])/'STOP',dict(reason='Full campaign stop'))
                if request['kind']=='session':
                    root=Path(self.job['campaign'])/'sessions'/request['day'];active=root/'active.json'
                    if active.exists():
                        owner=read(active);path=Path(owner['campaign'])/'local'/request['day']/'status.json'
                        if not path.exists():event=dict(phase=owner['phase'],generation=0,completed_generations=0,status='running',stage='Verify specialist promotion / initialize next phase')
                        if path.exists():
                            event=dict(read(path),phase=owner['phase'])
                            try:self.display(root,request['day'],owner,event)
                            except (OSError,ValueError,KeyError) as error:event['display_error']=str(error)
                if event:self.bus.write(self.bus.root/'progress'/(key+'.json'),dict(event,node=self.node))
            self.bus.write(self.bus.root/'heartbeats'/(self.node+'.json'),dict(self.owner,node=self.node,epoch=time.time(),current=self.current[0] if self.current else None,stage=event.get('stage',self.stage),available_gib=psutil.virtual_memory().available/2**30))

    def display(self,root,day,owner,status):
        g=status.get('completed_generations',0);key=(day,owner['phase'],g)
        if not g or key in self.displayed:return
        folder=Path(owner['campaign'])/'local'/day
        if not (folder/f'generation-{g:04d}/complete.json').exists():return
        from research.vectorized_backtest.v27.local_details import load_generation
        from .observer_model import enrich
        manifest=read(self.job['training']);source=read(Path(manifest['inputs'])/day/'complete.json');labels={day+':'+r['security_id']:r.get('ticker',r['security_id'][-8:]) for r in source['listings']}
        scores=read(folder/f'generation-{g:04d}/scores.json');rows=enrich(load_generation(folder,g),scores,labels)
        data=dict(rows=rows,case_labels={r['case_owner']:r['ticker'] for r in rows if r['case_owner']},next_quotas=scores['diversity'].get('case_quotas',{}),phase=owner['phase'],running=True,day=day,generation=g,target=owner['generations'],session_progress=status,payloads={},display_ready=True,loss_weight=self.job['objective']['loss_weight'])
        self.bus.write(self.bus.root/'details'/self.node/(day+'.json'),data);self.displayed.add(key)

    def close_worker(self):
        if self.worker:self.worker.close();self.worker=None
        self.worker_role=None;self.last_day=None;gc.collect()
        import torch
        if torch.cuda.is_initialized():torch.cuda.empty_cache()

    def export(self,key,files):
        results=[]
        for name,path in files.items():
            target=self.bus.root/'results'/key/name;expected=sha(path);copy_verified(path,target,expected)
            results.append(dict(name=name,sha256=expected,size=target.stat().st_size))
        return results

    def execute(self,key,request):
        if request['source_sha256']!=self.job['source_sha256']:raise ValueError('Request source changed')
        started=time.perf_counter();role=request.get('role','training');day=request['day'];self.stage='Verify/stage certified data'
        ensure_day(self.job,self.node,role,day)
        if request['kind']=='session':
            self.close_worker();cfg=config(self.job,self.node);cfg['initialization_probe']=str(Path(self.job['probe_root'])/(day+'.npz'))
            os.environ.pop('V48_INITIAL_POOL',None);os.environ.pop('V48_INITIAL_POOL_SHA256',None)
            pool=self.job.get('initial_pools',{}).get(day)
            if pool:os.environ['V42_INITIAL_POOL']=pool
            else:os.environ.pop('V42_INITIAL_POOL',None)
            root=Path(self.job['campaign'])/'sessions'/day
            write_json(root/'manifest.json',dict(days=[day],job=self.job['job_path'],job_sha256=sha(self.job['job_path']),local_generations=100,source_sha256=self.job['source_sha256']))
            from .phases import execute
            result=execute(cfg,day,root,dict(self.owner,day=day,version='v48',source_sha256=self.job['source_sha256']))
            if result['status']!='complete':raise InterruptedError('Session stopped at preserved checkpoint')
            from .full_seed import session_export
            seed=session_export(root,day,self.job);write_json(root/'phase2-experts.json',seed)
            files=self.export(key,dict(experts=root/'phase2-experts.json',complete=root/'complete.json'))
            return dict(files=files,result=result,seconds=time.perf_counter()-started,day=day)
        if request['kind']!='replay':raise ValueError('Unknown full-campaign request')
        if self.worker_role!=role:
            self.close_worker();cfg=config(self.job,self.node,role)
            cfg.update(output=str(Path(self.job['campaign'])/'nodes'/self.node/role),boundary_stop=self.job['campaign'])
            from .session_lanes import Worker
            self.worker=Worker(cfg,self.events,0);self.worker_role=role
        if self.last_day!=day:self.worker.resident_results=None
        self.last_day=day
        payload=read(self.bus.root/'payloads'/(request['payload_sha256']+'.json'))
        if digest(payload)!=request['payload_sha256']:raise ValueError('Global population transport changed')
        if any(p.get('case_owner') is not None for p in payload):raise ValueError('Owned specialist leaked into unrestricted global replay')
        width=len(read(Path(self.worker.config['history'])/day/'complete.json')['listing_ids'])
        next_day=None
        for path in sorted((self.bus.root/'requests').glob('*.json'),key=lambda p:p.stat().st_mtime_ns):
            other=read(path)
            if other.get('kind')=='replay' and other.get('role')==role and other.get('generation')==request['generation'] and other.get('node')==self.node and other['day']!=day and not (self.bus.root/'done'/path.name).exists():
                marker=Path(self.job['campaign'])/'data-ready'/self.node/role/(other['day']+'.json')
                if marker.exists():next_day=other['day'];break
        self.worker.run(day,next_day,payload,request['generation'],request['payload_sha256'],width)
        folder=Path(self.worker.config['output'])/f"generation-{request['generation']:04d}"/day
        return dict(files=self.export(key,dict(receipt=folder/'receipt.json',timings=folder/'timings.json')),seconds=time.perf_counter()-started,day=day)

    def run(self):
        root=Path(self.job['campaign'])/'nodes'/self.node;root.mkdir(parents=True,exist_ok=True)
        with owned_run(root,version='v48-full-node-v1'):
            self.bus.recover_own_claims(exact_live);write_json(root/'owner.json',dict(self.owner,node=self.node))
            thread=threading.Thread(target=self.heartbeat,daemon=True);thread.start()
            try:
                while not (self.bus.root/'STOP').exists() and not (Path(self.job['campaign'])/'STOP').exists():
                    item=self.bus.claim(self.owner)
                    if not item:time.sleep(1);continue
                    self.current=item;key,request=item
                    try:
                        result=self.execute(key,request)
                        self.bus.write(self.bus.root/'done'/(key+'.json'),dict(result,request_sha256=key,node=self.node,source_sha256=self.job['source_sha256'],owner=self.owner,epoch=time.time()))
                    except BaseException:
                        self.bus.write(self.bus.root/'failed'/(key+'.json'),dict(request_sha256=key,node=self.node,owner=self.owner,traceback=traceback.format_exc(),epoch=time.time()));raise
                    finally:self.current=None
            finally:
                self.stop.set();thread.join(5);self.close_worker()
                self.bus.write(self.bus.root/'heartbeats'/(self.node+'.json'),dict(self.owner,node=self.node,status='exited',epoch=time.time()))


def main():
    p=argparse.ArgumentParser();p.add_argument('--job',type=Path,required=True);p.add_argument('--node',choices=['laptop','workstation'],required=True);a=p.parse_args()
    job=read(a.job);job['job_path']=str(a.job);check_job(job)
    machine=job['machines'][a.node];os.environ.update(V42_CPU_RESERVE_GIB=str(machine['reserve_gib']),V42_CPU_ADMISSION_GIB=str(machine['admission_gib']),V42_REPRODUCTION_WORKERS='1',V48_MASK_QUOTES='0')
    from research.vectorized_backtest.v42.windows_process import quiet_compilers
    quiet_compilers()
    Agent(job,a.node).run()


if __name__=='__main__':main()
