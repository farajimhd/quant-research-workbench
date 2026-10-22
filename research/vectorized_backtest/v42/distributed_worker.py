"""Persistent node agent: local evolution units or frozen global replay units."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
for name in ('OMP_NUM_THREADS','MKL_NUM_THREADS','OPENBLAS_NUM_THREADS'):os.environ.setdefault(name,'1')
import argparse,json,time,threading,queue,traceback,gc
from pathlib import Path
import psutil
from .distributed_bus import Bus,read,digest
from .distributed_assets import ensure_day,receipt_inventory,sha
from .publication import write_json

def exact_live(owner):
    try:return abs(psutil.Process(owner['pid']).create_time()-owner['creation_time'])<.01
    except psutil.NoSuchProcess:return False

class Agent:
    def __init__(self,job,node):
        self.job=job;self.node=node;self.bus=Bus(job,node);self.owner=dict(pid=os.getpid(),creation_time=psutil.Process().create_time())
        from .distributed_preparation import PopulationPreparation
        self.preparer=PopulationPreparation()
        from concurrent.futures import ThreadPoolExecutor
        self.disk_loader=ThreadPoolExecutor(max_workers=1,thread_name_prefix='v42-disk-prefetch');self.disk_pending=None
        self.worker=None;self.worker_key=None;self.payload=None;self.payload_key=None;self.current=None;self.events=queue.Queue(maxsize=64);self.stop=threading.Event()
    def progress(self):
        while not self.stop.wait(1):
            event={}
            try:
                while True:event=self.events.get_nowait()
            except queue.Empty:pass
            if self.current and (self.bus.root/'STOP').exists():
                request=read(self.bus.root/'requests'/(self.current+'.json'))
                output=Path(self.job['campaign'])/'local'/request['day'] if request['kind']=='local' else Path(request.get('config',{}).get('output',self.job['campaign']))
                output.mkdir(parents=True,exist_ok=True);(output/'STOP').touch()
            if event and self.current:self.bus.write(self.bus.root/'progress'/(self.current+'.json'),dict(event,machine=self.node))
            self.bus.write(self.bus.root/'heartbeats'/(self.node+'.json'),dict(self.owner,node=self.node,current=self.current,epoch=time.time(),available_gib=psutil.virtual_memory().available/2**30,rss_gib=psutil.Process().memory_info().rss/2**30))
    def close_worker(self):
        if self.worker:self.worker.close();self.worker=None;self.worker_key=None
        self.payload=None;self.payload_key=None
        gc.collect()
        import torch
        if torch.cuda.is_initialized():torch.cuda.empty_cache()
    def configuration(self,cfg):
        c=dict(cfg);machine=self.job['machines'][self.node]
        c.update(host_reserve_gib=machine['reserve_gib'],host_admission_gib=machine['admission_gib'],capture_gib=machine['capture_gib'],immutable_population_cache=True,host_prefetch=True)
        import torch
        c['lane_memory_bytes']=int(torch.cuda.mem_get_info()[0]*.8)
        return c
    def warm_cache(self):
        # Disk-only staging: no feature evaluation, CUDA, fitness or RNG. A
        # single bounded copier overlaps the long independent local campaign.
        for role,days in self.job['machines'][self.node].get('days',{}).items():
            for day in days:
                if (self.bus.root/'STOP').exists():return
                ensure_day(self.job,self.node,role,day)
    def hint(self,request):
        for path in sorted((self.bus.root/'requests').glob('*.json'),key=lambda p:p.stat().st_mtime_ns):
            other=read(path)
            allowed=self.job['machines'][self.node].get('days')
            if allowed and other.get('day') not in allowed.get(request['role'],[]):continue
            if other.get('kind')!='replay' or other.get('role')!=request['role'] or other.get('generation')!=request['generation'] or other.get('day')==request['day']:continue
            if other.get('node') not in (None,self.node) or (self.bus.root/'claims'/path.name).exists() or (self.bus.root/'done'/path.name).exists():continue
            day=other['day'];marker=Path(self.job['campaign'])/'data-ready'/self.node/request['role']/(day+'.json')
            if marker.exists():return day
            if self.disk_pending is None or self.disk_pending.done():
                if self.disk_pending:self.disk_pending.result()
                self.disk_pending=self.disk_loader.submit(ensure_day,self.job,self.node,request['role'],day)
            return None
        return None
    def execute(self,key,request):
        if request['source_sha256']!=self.job['source_sha256']:raise ValueError('Distributed source mismatch')
        began=time.perf_counter();kind=request['kind'];files=[]
        if kind=='release':self.close_worker();return dict(files=[],seconds=time.perf_counter()-began)
        role=request.get('role','training');day=request['day'];stage_seconds=ensure_day(self.job,self.node,role,day)
        if kind=='local':
            if self.node=='laptop' and self.disk_pending is None:self.disk_pending=self.disk_loader.submit(self.warm_cache)
            self.close_worker()
            from .probes import local_probe
            from .local_runner import run_session
            cfg=self.configuration(request['config']);probe=local_probe(request['training'],day,Path(self.job['campaign'])/'initialization-probes')
            cfg['initialization_probe']=str(probe)
            # An optional imported pool is identity-checked by the normal initializer.
            pool=self.job.get('initial_pools',{}).get(day)
            if pool:os.environ['V42_INITIAL_POOL']=pool
            else:os.environ.pop('V42_INITIAL_POOL',None)
            output=Path(self.job['campaign'])/'local'/day
            result=run_session(cfg,day,output,self.job['local_generations'],self.job['population'],self.job['seed'],64,1024,initial_multiplier=self.job['initial_multiplier'])
            if result['status']!='complete':raise InterruptedError('Local unit stopped at preserved checkpoint')
            for name in ('checkpoint.json','archive.json','complete.json','initialization.json','status.json'):
                path=output/name;files.append(dict(path=str(path),sha256=sha(path)))
            files.append(dict(path=str(probe),sha256=sha(probe)))
            # Complete local seals and their evidence remain on this exact host.
            state=read(output/'checkpoint.json');assert state['state']['completed']==self.job['local_generations']
            seals={}
            for generation,h in state['state']['seals'].items():
                folder=output/f'generation-{int(generation):04d}';seal=read(folder/'complete.json')
                if sha(folder/'complete.json')!=h or sha(folder/day/'receipt.json')!=seal['receipt_sha256'] or sha(folder/'scores.json')!=seal['scores_sha256'] or sha(folder/'births.json')!=seal['births_sha256']:raise ValueError('Local seal evidence changed')
                seals[generation]=h
            return dict(files=files,result=result,seals=seals,seconds=time.perf_counter()-began,data_stage_seconds=stage_seconds)
        if kind!='replay':raise ValueError('Unknown distributed unit')
        cfg=self.configuration(request['config']);worker_key=digest({k:v for k,v in cfg.items() if k!='lane_memory_bytes'})
        if self.worker_key!=worker_key:
            self.close_worker()
            from .session_lanes import Worker
            self.worker=Worker(cfg,self.events,0,preparation=self.preparer);self.worker_key=worker_key
        pkey=request['payload_sha256']
        if self.payload_key!=pkey:
            self.payload=read(self.bus.root/'payloads'/(pkey+'.json'))
            if digest(self.payload)!=pkey:raise ValueError('Frozen payload changed')
            self.payload_key=pkey
            if request.get('parent_payload_sha256'):self.preparer.subset(self.payload,pkey,request['parent_payload_sha256'],request['selection'])
        if pkey!=request['token']:raise ValueError('Replay population hash mismatch')
        next_day=self.hint(request)
        result=self.worker.run(day,next_day,self.payload,request['generation'],request['token'],request['width'])
        folder=Path(cfg['output'])/f"generation-{request['generation']:04d}"/day
        for name in ('receipt.json','timings.json'):
            path=folder/name
            if path.exists():files.append(dict(path=str(path),sha256=sha(path)))
        record=read(folder/'receipt.json')
        if record['population_sha256']!=request['token'] or not record['full_session'] or record['execution']!=cfg['execution'] or record['split']!=role:raise ValueError('Worker receipt identity changed')
        return dict(files=files,result=result,seconds=time.perf_counter()-began,data_stage_seconds=stage_seconds,preparation_cache=dict(hits=self.preparer.hits,misses=self.preparer.misses,subset_hits=self.preparer.subset_hits))
    def run(self):
        old=self.bus.root/'heartbeats'/(self.node+'.json')
        if old.exists() and exact_live(read(old)):raise RuntimeError('Exact prior node agent still live')
        self.bus.recover_own_claims(exact_live);receipt_inventory(self.job,self.node)
        thread=threading.Thread(target=self.progress,daemon=True);thread.start()
        try:
            while not (self.bus.root/'STOP').exists():
                claimed=self.bus.claim(self.owner)
                if claimed is None:time.sleep(.5);continue
                key,request=claimed;self.current=key
                try:result=self.execute(key,request)
                except BaseException:
                    self.bus.write(self.bus.root/'failed'/(key+'.json'),dict(self.owner,node=self.node,request_sha256=key,traceback=traceback.format_exc(),epoch=time.time()));raise
                self.bus.write(self.bus.root/'done'/(key+'.json'),dict(result,node=self.node,request_sha256=key,source_sha256=self.job['source_sha256'],owner=self.owner,epoch=time.time()));self.current=None
        finally:self.close_worker();self.disk_loader.shutdown(wait=True,cancel_futures=True);self.preparer.close();self.stop.set();thread.join();self.bus.write(old,dict(self.owner,node=self.node,status='exited',epoch=time.time()))

def main():
    p=argparse.ArgumentParser();p.add_argument('--job',type=Path,required=True);p.add_argument('--node',choices=('laptop','workstation'),required=True);a=p.parse_args();job=read(a.job)
    machine=job['machines'][a.node]
    os.environ.update(V42_CPU_RESERVE_GIB=str(machine['reserve_gib']),V42_CPU_ADMISSION_GIB=str(machine['admission_gib']),V42_PREPARATION_WORKERS=str(machine.get('cpu_workers',2)))
    from .run_search import source_hash
    if source_hash()!=job['source_sha256']:raise ValueError('Node immutable source changed')
    Agent(job,a.node).run()
if __name__=='__main__':main()
