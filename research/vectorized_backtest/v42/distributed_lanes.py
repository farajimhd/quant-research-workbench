"""Dynamic two-machine evaluation adapter for the unchanged global barrier."""
import time,json,threading
from pathlib import Path
from .distributed_bus import Bus,read,digest
from .publication import write_json
from .distributed_assets import sha,remote,copy_verified

class DistributedLanes:
    def __init__(self,config,workers,panel):
        self.config=config;self.panel=panel;self.job=read(config['distributed_job']);self.bus=Bus(self.job);self.role=config['split']
    def __enter__(self):return self
    def generation(self,days,population,generation,token,**kwargs):
        payload=[p.payload() for p in population]
        if digest(payload)!=token:raise ValueError('Coordinator population changed')
        payload_key=self.bus.payload(payload);pending={};results={};parent={}
        if self.role=='validation':
            path=Path(self.config['output']).parent/'training'/f'generation-{generation:04d}'/'population.json'
            if path.exists():
                full=read(path)['population'];lookup={digest(p):i for i,p in enumerate(full)}
                parent=dict(parent_payload_sha256=digest(full),selection=[lookup[digest(p)] for p in payload])
        cfg={k:v for k,v in self.config.items() if k!='distributed_job'}
        for day in days:
            width=len(read(Path(cfg['history'])/day/'complete.json')['listing_ids'])
            request=dict(**parent,kind='replay',role=self.role,day=day,generation=generation,token=token,payload_sha256=payload_key,width=width,config=cfg,source_sha256=self.job['source_sha256'])
            pending[self.bus.submit(request)]=day
        from .global_pipeline import IndependentProgress
        progress=IndependentProgress(self.panel,days,len(population),cfg['batch_size']);begun=set();started=time.perf_counter()
        while pending:
            if (self.bus.root/'STOP').exists() or (Path(self.job['campaign'])/'STOP').exists():raise InterruptedError('Distributed replay stop requested')
            for node in self.job['machines']:
                heartbeat=self.bus.root/'heartbeats'/(node+'.json')
                if heartbeat.exists():
                    h=read(heartbeat)
                    if h.get('status')=='exited' or time.time()-h['epoch']>90:raise RuntimeError('Node agent unavailable: '+node)
            for key,day in list(pending.items()):
                claim=self.bus.root/'claims'/(key+'.json')/'owner.json'
                if claim.exists() and day not in begun:progress.begin(day);begun.add(day)
                path=self.bus.root/'progress'/(key+'.json')
                if path.exists():progress.event(read(path))
                done=self.bus.result(key)
                if done is None:continue
                if done['source_sha256']!=self.job['source_sha256']:raise ValueError('Result source changed')
                for item in done['files']:self.bus.mirror(done['node'],item['path'],item['sha256'])
                receipt=read(Path(cfg['output'])/f'generation-{generation:04d}'/day/'receipt.json')
                if receipt['population_sha256']!=token or not receipt['full_session'] or receipt['split']!=self.role or receipt['execution']!=cfg['execution']:raise ValueError('Distributed receipt identity mismatch')
                if receipt['validation_opened']!=(self.role=='validation'):raise ValueError('Distributed split crossed')
                results[day]=done;del pending[key]
                if day not in begun:progress.begin(day);begun.add(day)
                progress.finish(day,done['result']);self.bus.retire(key)
            workers={}
            for node in self.job['machines']:
                path=self.bus.root/'heartbeats'/(node+'.json')
                if path.exists():workers[node]=read(path)
            self.panel.update(distributed_workers=workers)
            if pending:time.sleep(.5)
        root=Path(cfg['output'])/f'generation-{generation:04d}'
        write_json(root/'distributed-profile.json',dict(source_sha256=self.job['source_sha256'],population_sha256=token,wall_seconds=time.perf_counter()-started,assignments={day:dict(node=r['node'],seconds=r['seconds'],data_stage_seconds=r['data_stage_seconds'],preparation=r.get('preparation_cache')) for day,r in results.items()},barrier='all sessions sealed before reproduction',validation_feedback=False))
        return [t for day in days for t in results[day]['result']['timings']]
    def release_plans(self):
        pending=[self.bus.submit(dict(kind='release',node=node,role=self.role,source_sha256=self.job['source_sha256'],phase_nonce=str(time.time_ns()))) for node in self.job['machines']]
        while pending:
            for key in pending[:]:
                if self.bus.result(key) is not None:pending.remove(key);self.bus.retire(key)
            if pending:time.sleep(.25)
    def __exit__(self,*args):
        if args[0] is None:self.release_plans()
