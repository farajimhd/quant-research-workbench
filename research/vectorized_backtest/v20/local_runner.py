"""Session-owned evolution inside persistent workers; training only."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import json,queue,time,threading
from pathlib import Path
from dataclasses import asdict
import numpy as np
from .genome import Individual
from .initial_population import initialize
from .lineage import Selection,next_population,program_key
from .complexity import count
from .objectives import Objective,local_panel
from .archive import update_archive,descriptor
from .run_search import digest,source_hash
from research.vectorized_backtest.v6.torch_backtest.runtime import require_runtime,write_json,file_hash
from research.vectorized_backtest.v6.torch_backtest.materialize import owned_run


class CompactPedigree:
    """Keep active parent metadata; retain birth evidence in generation seals."""
    def __init__(self,depth=5,records=None,next_serial=0):
        self.depth=depth;self.records={r['serial']:r for r in (records or [])};self.next_serial=next_serial
    def birth(self,members,generation,parents,operations):
        ids=np.arange(self.next_serial,self.next_serial+len(members),dtype=np.int64)
        for serial,member,pair,op in zip(ids,members,parents,operations):
            ancestors={};primary=self.records[int(pair[0])] if pair[0]>=0 else None
            for parent in pair:
                if parent<0:continue
                ancestors[int(parent)]=1
                for ancestor,distance in self.records[int(parent)]['ancestors']:
                    if distance<self.depth:ancestors[ancestor]=min(ancestors.get(ancestor,self.depth+1),distance+1)
            if len(ancestors)>2**(self.depth+1)-2:raise ValueError('Pedigree bound exceeded')
            mutations=(primary['mutations'] if primary else 0)+int('mutation' in op)
            crosses=(primary['crossovers'] if primary else 0)+int('crossover' in op)
            origin=primary['origin_generation'] if primary else generation;key=program_key(member)
            self.records[int(serial)]=dict(serial=int(serial),generation=generation,origin_generation=origin,parents=list(map(int,pair)),operation=op,
                mutations=mutations,crossovers=crosses,ancestors=sorted(ancestors.items()),program_sha256=key,complexity=count(member),
                label=f'G{origin:03d}-{serial:06d}-M{mutations:03d}-X{crosses:03d}-{key[:4]}')
        self.next_serial+=len(members);return ids
    def retain(self,ids):self.records={int(i):self.records[int(i)] for i in ids}


def run_session(config,day,output,generations=30,population_size=1024,seed=2020,archive_capacity=64,archive_shortlist=256,worker_factory=None):
    """Dispatch once per session. No controller population serialization per generation."""
    if not 1<=generations<=500 or not 10<=population_size<=65536:raise ValueError('Bounded local search required')
    from .completion_gate import require_complete
    require_complete(config.get('predecessor_job'),config.get('predecessor_proof'))
    if config.get('source_sha256')!=source_hash():raise ValueError('Pinned local source changed')
    if not 1<=archive_capacity<=archive_shortlist<=1024:raise ValueError('Bounded archive required')
    from .evaluator import Execution
    if not Execution(**config['execution']).validate().risk_protection:raise ValueError('Local search requires V19 risk protection')
    output=require_runtime(output);objective=Objective();selection=Selection(global_fraction=1.,global_fresh_fraction=.2,champion_partners=0)
    contract=dict(version='v20-local-session-v1',source_sha256=source_hash(),day=day,generations=generations,population=population_size,seed=seed,
                  objective=asdict(objective),selection=asdict(selection),archive_capacity=archive_capacity,archive_shortlist=archive_shortlist,
                  dataset={k:config.get(k) for k in ['inputs','history','indicator_root','enriched_root','input_receipts','indicator_receipts','enriched_receipts']},execution=config['execution'],
                  backend=config['backend'],device=config['device'],batch_size=config['batch_size'],local_other_session_penalty='omitted: unavailable in single-session stage',validation_opened=False)
    rng=np.random.default_rng(np.random.SeedSequence([seed,int(day.replace('-',''))]));pedigree=CompactPedigree(selection.ancestry_generations);archive=[];completed=0
    checkpoint=output/'checkpoint.json';seals={}
    with owned_run(output,version='v20-local-session-v1'):
        if checkpoint.exists():
            saved=json.loads(checkpoint.read_bytes())
            if saved['contract']!=contract or digest(saved['state'])!=saved['state_sha256']:raise ValueError('Local resume identity changed')
            state=saved['state'];completed=state['completed'];seals=state['seals'];members=[Individual.restore(p) for p in state['population']];ids=np.asarray(state['ids'])
            pedigree=CompactPedigree(selection.ancestry_generations,state['pedigree'],state['next_serial']);rng.bit_generator.state=state['rng']
            archive=[dict(r,descriptor=descriptor(Individual.restore(r['payload'])).tolist()) for r in state['archive']]
            for g in range(1,completed+1):
                folder=output/f'generation-{g:04d}';seal=json.loads((folder/'complete.json').read_bytes())
                if file_hash(folder/'complete.json')!=seals[str(g)] or file_hash(folder/day/'receipt.json')!=seal['receipt_sha256'] or file_hash(folder/'scores.json')!=seal['scores_sha256'] or file_hash(folder/'births.json')!=seal['births_sha256']:raise ValueError('Local sealed evidence changed')
        else:
            members,_=initialize(rng,population_size);ids=pedigree.birth(members,1,[(-1,-1)]*population_size,['fresh']*population_size)
        if worker_factory is None:
            from .session_lanes import Worker
            worker_factory=Worker
        cfg=dict(config,output=str(output),split='training',retain_session_residency=True,reuse_results=True,maximum_host_sessions=2,
                 compile_cache_root=str(output.parent/'worker-caches'/str(os.getpid())))
        events=queue.Queue(maxsize=1);stopped=threading.Event();current={};progress_errors=[]
        def pump():
            while not stopped.wait(1):
                try:
                    event=events.get_nowait()
                    write_json(output/'status.json',dict(current,**event,status='running',total_generations=generations))
                except queue.Empty:pass
                except Exception as error:progress_errors.append(error);return
        worker=worker_factory(cfg,events,0)
        thread=threading.Thread(target=pump,name='v20-local-progress',daemon=True);thread.start()
        try:
            if (output/'STOP').exists():return dict(status='interrupted',day=day,completed=completed)
            for generation in range(completed+1,generations+1):
                began=time.perf_counter();payload=[m.payload() for m in members];token=digest(payload)
                def status(stage,**fields):
                    current.update(status='running',day=day,generation=generation,completed_generations=generation-1,total_generations=generations,stage=stage,updated_epoch=time.time(),**fields)
                    # The progress thread owns status publication during evaluation.
                status('Evaluate resident session');item=worker.load(day)
                if item.identity['input_sha256']!=config['input_receipts'][day]['inputs']:raise ValueError('Pinned local input certificate changed')
                for bank in ('indicator','enriched'):
                    if item.identity[bank+'_sha256']!=config[bank+'_receipts'][day]:raise ValueError('Pinned local feature certificate changed')
                if item.identity['history_sha256']!=config['input_receipts'][day]['history']:raise ValueError('Pinned causal history changed')
                evaluation=worker.run(day,None,payload,generation,token,len(item.listing_ids))
                folder=output/f'generation-{generation:04d}';receipt=folder/day/'receipt.json';record=json.loads(receipt.read_bytes())
                if record['population_sha256']!=token or record['validation_opened'] is not False or record['execution']!=config['execution'] or not record['full_session']:raise ValueError('Local session receipt changed')
                status('Score and archive');prepared=local_panel([record['metrics']],objective);values=prepared['values'][0];eligible=prepared['eligible'][0]
                archive=update_archive(archive,members,ids,values,eligible,pedigree,day,generation,archive_capacity,archive_shortlist)
                rank=np.lexsort((ids,-values)).tolist();source_ids=ids.copy()
                members,ids,report=next_population(rng,members,ids,pedigree,rank,generation+1,selection,session_values=values[None],session_eligible=eligible[None],global_values=values,global_eligible=eligible) if generation<generations else (members,ids,{})
                births=[r for r in pedigree.records.values() if r['generation']==generation+1];write_json(folder/'births.json',births)
                write_json(folder/'scores.json',dict(ids=source_ids.tolist(),scores=values.tolist(),eligible=eligible.tolist(),archive_count=len(archive),diversity=report))
                write_json(folder/'complete.json',dict(receipt_sha256=file_hash(receipt),population_sha256=token,validation_opened=False,day=day,
                    scores_sha256=file_hash(folder/'scores.json'),births_sha256=file_hash(folder/'births.json'),wall_seconds=time.perf_counter()-began,evaluation_seconds=evaluation['wall_seconds'],
                    cumulative_host_loads=getattr(worker,'loads',None),cumulative_device_uploads=getattr(worker,'uploads',None)))
                pedigree.retain(ids)
                seals[str(generation)]=file_hash(folder/'complete.json')
                state=dict(completed=generation,population=[m.payload() for m in members],ids=ids.tolist(),pedigree=list(pedigree.records.values()),next_serial=pedigree.next_serial,
                           rng=rng.bit_generator.state,seals=seals,archive=[{k:v for k,v in r.items() if k!='descriptor'} for r in archive])
                write_json(checkpoint,dict(contract=contract,state=state,state_sha256=digest(state)))
                if progress_errors:raise RuntimeError('Local progress publication failed') from progress_errors[0]
                write_json(folder/'wall.json',dict(full_generation_seconds=time.perf_counter()-began,includes_scoring_archive_reproduction_checkpoint=True,
                           checkpoint_sha256=file_hash(checkpoint),complete_sha256=file_hash(folder/'complete.json')))
                if (output/'STOP').exists():return dict(status='interrupted',day=day,completed=generation)
            stopped.set();thread.join()
            records=[{k:v for k,v in r.items() if k!='descriptor'} for r in archive];write_json(output/'archive.json',records)
            write_json(output/'complete.json',dict(status='complete',day=day,generations=generations,checkpoint_sha256=file_hash(checkpoint),archive_sha256=file_hash(output/'archive.json'),validation_opened=False))
            write_json(output/'status.json',dict(status='complete',day=day,completed_generations=generations,archive_count=len(archive)))
            return dict(status='complete',day=day,archive_count=len(archive),output=str(output))
        finally:
            stopped.set();thread.join();worker.close()
            if not (output/'complete.json').exists():
                write_json(output/'status.json',dict(current,status='interrupted' if (output/'STOP').exists() else 'failed',
                    stage='Stopped at sealed generation boundary' if (output/'STOP').exists() else 'Local task exited without completion',updated_epoch=time.time()))
