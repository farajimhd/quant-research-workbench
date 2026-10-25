"""Workstation CPU coordinator: paired local sessions, then global 100 barriers."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json,time,traceback
from pathlib import Path
import numpy as np
import psutil
from .full_common import check_job,aggregate,ranking,serializable
from .objectives import Objective
from .identity import digest
from .genome import Individual
from .local_runner import CompactPedigree
from .lineage import next_population,Selection
from .species import Species
from research.vectorized_backtest.v42.distributed_bus import Bus,read
from research.vectorized_backtest.v42.distributed_assets import sha
from research.vectorized_backtest.v42.publication import write_json,write_checkpoint
from research.vectorized_backtest.v6.torch_backtest.materialize import owned_run


class Controller:
    def __init__(self,job):
        self.job=job;self.root=Path(job['campaign']);self.bus=Bus(job,'workstation');self.sessions={};self.owner=dict(pid=os.getpid(),creation_time=psutil.Process().create_time());self.stage='Starting';self.view={};self.status='running'

    def publish(self,**extra):
        hb={n:read(self.bus.root/'heartbeats'/(n+'.json')) for n in self.job['machines'] if (self.bus.root/'heartbeats'/(n+'.json')).exists()}
        self.view.update(extra)
        write_json(self.root/'full-status.json',dict(status=self.status,stage=self.stage,owner=self.owner,source_sha256=self.job['source_sha256'],sessions=self.sessions,heartbeats=hb,updated_epoch=time.time(),**self.view))

    def health(self):
        if (self.root/'STOP').exists():raise InterruptedError('Full V49 stop requested')
        for n in self.job['machines']:
            path=self.bus.root/'heartbeats'/(n+'.json')
            if path.exists():
                h=read(path)
                if h.get('status')=='exited' or time.time()-h['epoch']>180:raise RuntimeError('Exact node heartbeat unavailable: '+n)

    def host(self,role,day):
        return next(n for n,m in self.job['machines'].items() if day in m['days'][role])

    def result_file(self,key,result,name):
        item=next(x for x in result['files'] if x['name']==name);path=self.bus.root/'results'/key/name
        if sha(path)!=item['sha256']:raise ValueError('Transferred result changed')
        return path

    def local_sessions(self,days):
        self.stage='Session phase 1';pending={};exports={}
        for day in days:
            node=self.host('training',day);self.sessions[day]=dict(node=node,status='queued',phase=1,generation=0)
            key=self.bus.submit(dict(kind='session',day=day,role='training',node=node,source_sha256=self.job['source_sha256']));pending[key]=day
        while pending:
            self.health()
            for key,day in list(pending.items()):
                progress=self.bus.root/'progress'/(key+'.json')
                if progress.exists():self.sessions[day].update(read(progress),status='running')
                result=self.bus.result(key)
                if result is None:continue
                if result['node']!=self.host('training',day) or result['source_sha256']!=self.job['source_sha256']:raise ValueError('Session affinity/source changed')
                export=read(self.result_file(key,result,'experts'))
                if export['day']!=day or export['objective']!=self.job['objective'] or export['validation_opened']:raise ValueError('Session export contract changed')
                exports[day]=export;self.sessions[day].update(status='complete',phase=1,completed_generations=50,generation=50);del pending[key]
            self.publish(phase='local',completed_sessions=len(exports),total_sessions=len(days))
            if pending:time.sleep(2)
        write_json(self.root/'session-export-index.json',{d:dict(checkpoint_sha256=e['checkpoint_sha256'],experts=len(e['records']),node=self.host('training',d)) for d,e in exports.items()})
        return exports

    def evaluate(self,role,days,members,generation):
        payload=[m.payload() for m in members];token=self.bus.payload(payload);pending={};out={}
        for day in days:
            key=self.bus.submit(dict(kind='replay',role=role,day=day,node=self.host(role,day),generation=generation,payload_sha256=token,source_sha256=self.job['source_sha256']));pending[key]=day
        while pending:
            self.health();progress={}
            for key,day in list(pending.items()):
                path=self.bus.root/'progress'/(key+'.json')
                if path.exists():progress[day]=read(path)
                result=self.bus.result(key)
                if result is None:continue
                if result['node']!=self.host(role,day) or result['source_sha256']!=self.job['source_sha256']:raise ValueError('Replay affinity/source changed')
                path=self.result_file(key,result,'receipt');receipt=read(path)
                if receipt['population_sha256']!=token or receipt['split']!=role or receipt['validation_opened']!=(role=='validation') or receipt['execution']!=self.job['execution'] or not receipt['full_session']:raise ValueError('Global replay receipt changed')
                if any(o is not None for o in receipt['case_owners']):raise ValueError('Phase0 ownership leaked into global fitness')
                out[day]=dict(metrics=receipt['metrics'],receipt_sha256=sha(path),request_sha256=key,node=result['node']);del pending[key]
            self.publish(phase=2,generation=generation,completed_generations=generation-1,evaluation_split=role,completed_sessions=len(out),total_sessions=len(days),session_progress=progress)
            if pending:time.sleep(1)
        return [out[d]['metrics'] for d in days],{d:{k:v for k,v in out[d].items() if k!='metrics'} for d in days}

    def global_run(self,exports,train,valid):
        from .full_seed import global_seed
        from research.vectorized_backtest.v42.probes import global_probe
        from research.vectorized_backtest.v42.calibration import load_probe
        self.stage='Build phase 2 seed from verified historical session experts';self.publish(phase=2,generation=0,completed_generations=0,completed_sessions=0,total_sessions=24)
        root=self.root/'global';root.mkdir(exist_ok=True);objective=Objective(**self.job['objective'])
        contract=dict(source_sha256=self.job['source_sha256'],objective=self.job['objective'],training_sha256=self.job['training_sha256'],validation_sha256=self.job['validation_sha256'],generations=self.job['phase2_generations'],population=self.job['population'],validation='display-only; successor and RNG frozen before opening')
        cp=root/'checkpoint.json';species=Species();completed=0;seals={}
        if cp.exists():
            saved=read(cp)
            if saved['contract']!=contract or digest(saved['state'])!=saved['state_sha256']:raise ValueError('Global resume contract changed')
            state=saved['state'];completed=state['completed'];seals=state['seals'];species=Species(state['species'])
            for g,h in seals.items():
                f=root/f'generation-{int(g):04d}'
                if sha(f/'complete.json')!=h:raise ValueError('Global checkpoint seal link changed')
                seal=read(f/'complete.json')
                for filename,key in (('training.json','training_sha256'),('validation.json','validation_sha256'),('successor.json','successor_sha256'),('population.json','population_sha256')):
                    if sha(f/filename)!=seal[key]:raise ValueError('Sealed global evidence changed')
                validation=read(f/'validation.json')
                if validation['successor_sha256_before']!=validation['successor_sha256_after'] or not validation['display_only']:raise ValueError('Validation independence proof changed')
        else:
            probe=load_probe(global_probe(self.job['training'],train,self.job['probe_root']),self.job['training'])
            state=global_seed(exports,self.job,probe);state.update(completed=0,seals={},species=species.state)
            write_json(root/'initial-pool.json',state);write_checkpoint(cp,contract,state)
        members=[Individual.restore(p) for p in state['population']];ids=np.asarray(state['ids']);pedigree=CompactPedigree(3,state['pedigree'],state['next_serial']);rng=np.random.default_rng();rng.bit_generator.state=state['rng']
        for generation in range(completed+1,self.job['phase2_generations']+1):
            started=time.perf_counter();folder=root/f'generation-{generation:04d}';folder.mkdir(exist_ok=True);self.stage='Phase 2 training'
            source_population=[m.payload() for m in members];source_ids=ids.copy();token=digest(source_population)
            write_json(folder/'population.json',dict(population=source_population,ids=ids.tolist(),population_sha256=token))
            ordered,training_receipts=self.evaluate('training',train,members,generation);panel=aggregate(ordered,objective)
            write_json(folder/'training.json',dict(population_sha256=token,ids=source_ids.tolist(),panel=serializable(panel),ranking=ranking(panel,source_ids),receipts=training_receipts))
            if generation<self.job['phase2_generations']:
                from .validation_cache import validation_scope
                with validation_scope():
                    successor,newids,diversity=next_population(rng,members,ids,pedigree,[],generation+1,Selection(**self.job['selection']),session_values=panel['score'][None],session_eligible=panel['eligible'][None],global_values=panel['score'],global_eligible=panel['eligible'],target_size=self.job['population'],species=species,ordered=ordered,case_values=None,case_ids=None)
            else:successor,newids,diversity=members,ids,species.telemetry
            frozen=dict(population=[m.payload() for m in successor],ids=newids.tolist(),rng=rng.bit_generator.state)
            frozen_hash=digest(frozen);write_json(folder/'successor.json',dict(**frozen,sha256=frozen_hash,training_only=True))
            self.stage='Phase 2 validation: display only';validation,validation_receipts=self.evaluate('validation',valid,members,generation);validation_panel=aggregate(validation,objective)
            if digest(dict(population=[m.payload() for m in successor],ids=newids.tolist(),rng=rng.bit_generator.state))!=frozen_hash:raise ValueError('Validation changed successor or training RNG')
            write_json(folder/'validation.json',dict(population_sha256=token,ids=source_ids.tolist(),panel=serializable(validation_panel),ranking=ranking(validation_panel,source_ids),receipts=validation_receipts,display_only=True,successor_sha256_before=frozen_hash,successor_sha256_after=frozen_hash))
            write_json(folder/'complete.json',dict(training_sha256=sha(folder/'training.json'),validation_sha256=sha(folder/'validation.json'),successor_sha256=sha(folder/'successor.json'),population_sha256=sha(folder/'population.json'),source_sha256=self.job['source_sha256'],validation_independent=True,wall_seconds=time.perf_counter()-started))
            seals[str(generation)]=sha(folder/'complete.json');members=successor;ids=newids;pedigree.retain(ids)
            state=dict(completed=generation,population=[m.payload() for m in members],ids=ids.tolist(),pedigree=list(pedigree.records.values()),next_serial=pedigree.next_serial,rng=rng.bit_generator.state,seals=seals,species=species.state)
            write_checkpoint(cp,contract,state)
            self.publish(phase=2,generation=generation,ranking_generation=generation,completed_generations=generation,training_ranking=ranking(panel,source_ids),validation_ranking=ranking(validation_panel,source_ids),validation_display_only=True)
        write_json(root/'complete.json',dict(status='complete',checkpoint_sha256=sha(cp),completed=self.job['phase2_generations'],training_sessions=len(train),validation_sessions=len(valid),validation_independent=True))

    def run(self):
        with owned_run(self.root,version='v49-full-controller-v1'):
            write_json(self.root/'controller-owner.json',self.owner)
            train=read(self.job['training'])['days'];valid=read(self.job['validation'])['days']
            try:
                exports=self.local_sessions(train);self.global_run(exports,train,valid)
                write_json(self.root/'complete.json',dict(status='complete',global_complete_sha256=sha(self.root/'global/complete.json'),sessions=24,phase0_generations=0,phase1_generations=50,phase2_generations=100))
                self.stage='Complete';self.status='complete';self.publish(phase=2,completed_generations=100)
            except BaseException as error:
                self.status='interrupted' if isinstance(error,InterruptedError) else 'failed';self.stage=str(error);self.publish()
                write_json(self.root/'failure.json',dict(traceback=traceback.format_exc(),epoch=time.time()));raise
            finally:self.bus.write(self.bus.root/'STOP',dict(reason='Controller exited; preserve all checkpoints',epoch=time.time()))


def main():
    p=argparse.ArgumentParser();p.add_argument('--job',type=Path,required=True);a=p.parse_args();job=read(a.job);job['job_path']=str(a.job);check_job(job);Controller(job).run()


if __name__=='__main__':main()
