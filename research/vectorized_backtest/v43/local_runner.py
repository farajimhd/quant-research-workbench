"""Session-owned evolution inside persistent workers; training only."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import json,queue,time,threading
from pathlib import Path
from dataclasses import asdict
import numpy as np
from research.vectorized_backtest.v42.genome import Individual
from .initial_population import initialize
from research.vectorized_backtest.v42.lineage import Selection,next_population,program_key
from research.vectorized_backtest.v42.complexity import count
from .objectives import Objective,local_panel
from research.vectorized_backtest.v42.archive import update_archive
from .identity import digest,source_hash
from research.vectorized_backtest.v6.torch_backtest.runtime import require_runtime,file_hash
from research.vectorized_backtest.v42.publication import write_json,write_checkpoint
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
        from research.vectorized_backtest.v42.family_stats import annotate
        for serial,pair in zip(ids,parents):
            annotate(self.records[int(serial)],[self.records[int(p)] for p in pair if p>=0])
        self.next_serial+=len(members);return ids
    def retain(self,ids):self.records={int(i):self.records[int(i)] for i in ids}


def run_session(config,day,output,generations=50,population_size=1024,seed=2020,archive_capacity=64,archive_shortlist=1024,worker_factory=None,initial_multiplier=6):
    """Dispatch once per session. No controller population serialization per generation."""
    if not 1<=generations<=500 or not 10<=population_size<=65536:raise ValueError('Bounded local search required')
    if type(initial_multiplier) is not int or not 1<=initial_multiplier<=64 or initial_multiplier*population_size>65536:raise ValueError('Bounded initial multiplier required')
    if config.get('authorization')!='v43-training':raise ValueError('Explicit V43 training authorization required')
    if config.get('source_sha256')!=source_hash():raise ValueError('Pinned local source changed')
    from research.vectorized_backtest.v42.calibration import load_probe
    probe=load_probe(config['initialization_probe'],config['training_manifest'])
    if probe['metadata']['days']!=[day]:raise ValueError('Local calibration must use only its own session')
    if not 1<=archive_capacity<=archive_shortlist<=4096:raise ValueError('Bounded archive required')
    from research.vectorized_backtest.v42.evaluator import Execution
    if not Execution(**config['execution']).validate().risk_protection:raise ValueError('Local search requires V19 risk protection')
    output=require_runtime(output);objective=Objective();selection=Selection(**config.get('selection',{}));selection.validate()
    from research.vectorized_backtest.v42.composition import settings
    contract=dict(feature_semantics="v21-magnitude-ratio-v1",version='v43-local-session-v1',source_sha256=source_hash(),day=day,generations=generations,population=population_size,seed=seed,
                  objective=asdict(objective),selection=asdict(selection),recombination=settings(selection),archive_capacity=archive_capacity,archive_shortlist=archive_shortlist,initial_multiplier=initial_multiplier,
                  dataset={k:config.get(k) for k in ['inputs','history','indicator_root','enriched_root','input_receipts','indicator_receipts','enriched_receipts']},execution=config['execution'],
                  backend=config['backend'],device=config['device'],batch_size=config['batch_size'],capture_gib=config.get('capture_gib',4.),host_reserve_gib=config.get('host_reserve_gib'),host_admission_gib=config.get('host_admission_gib'),local_other_session_penalty='unavailable: single-session V43 uses trade removal only',validation_opened=False)
    contract['initialization_probe_sha256']=probe['sha256']
    contract['shared_replay']=config.get('shared_replay',False)
    reuse_path=config.get('reuse_initial_manifest')
    reuse_manifest=json.loads(Path(reuse_path).read_bytes()) if reuse_path else None
    contract['reuse_initial_manifest_sha256']=file_hash(reuse_path) if reuse_path else None
    if reuse_path and contract['reuse_initial_manifest_sha256']!=config['reuse_initial_manifest_sha256']:raise ValueError('Pinned initial reuse manifest changed')
    from research.vectorized_backtest.v42.species import Species
    species=Species()
    rng=np.random.default_rng(np.random.SeedSequence([seed,int(day.replace('-',''))]));pedigree=CompactPedigree(selection.ancestry_generations);archive=[];archive_pool=[];completed=0
    checkpoint=output/'checkpoint.json';seals={}
    with owned_run(output,version='v43-local-session-v1'):
        if checkpoint.exists():
            saved=json.loads(checkpoint.read_bytes())
            if saved['contract']!=contract or digest(saved['state'])!=saved['state_sha256']:raise ValueError('Local resume identity changed')
            state=saved['state'];completed=state['completed'];seals=state['seals'];members=[Individual.restore(p) for p in state['population']];ids=np.asarray(state['ids'])
            pedigree=CompactPedigree(selection.ancestry_generations,state['pedigree'],state['next_serial']);rng.bit_generator.state=state['rng']
            archive=state['archive'];archive_pool=state['archive_pool']
            species=Species(state['species'])
            for g in range(1,completed+1):
                folder=output/f'generation-{g:04d}';seal=json.loads((folder/'complete.json').read_bytes())
                if file_hash(folder/'complete.json')!=seals[str(g)] or file_hash(folder/day/'receipt.json')!=seal['receipt_sha256'] or file_hash(folder/'scores.json')!=seal['scores_sha256'] or file_hash(folder/'births.json')!=seal['births_sha256']:raise ValueError('Local sealed evidence changed')
            if completed==generations and (output/'complete.json').exists():
                seal=json.loads((output/'complete.json').read_bytes())
                if file_hash(checkpoint)!=seal['checkpoint_sha256'] or file_hash(output/'archive.json')!=seal['archive_sha256']:raise ValueError('Local completion evidence changed')
                return dict(status='complete',day=day,archive_count=len(archive),output=str(output))
        else:
            write_json(output/'status.json',dict(status='running',day=day,generation=1,completed_generations=0,total_generations=generations,
                stage=f'Initialize {population_size*initial_multiplier:,} calibrated candidates',updated_epoch=time.time()))
            if config.get('shared_replay') and config['device']=='cuda' and not config.get('persistent_session_owner'):
                from research.vectorized_backtest.v42.preparation import initialize as initialize_cpu
                members,initial_report=initialize_cpu(rng,population_size*initial_multiplier,config)
            else:members,initial_report=initialize(rng,population_size*initial_multiplier,probe=probe)
            write_json(output/'initialization.json',dict(**initial_report,seed=seed,day=day,initial_multiplier=initial_multiplier,
                population_sha256=digest([m.payload() for m in members])))
            ids=pedigree.birth(members,1,[(-1,-1)]*len(members),['fresh']*len(members))
            from research.vectorized_backtest.v42.family_stats import seed as seed_families
            seed_families(pedigree,ids,initial_report['initial_families'])
            state=dict(completed=0,population=[m.payload() for m in members],ids=ids.tolist(),pedigree=list(pedigree.records.values()),
                next_serial=pedigree.next_serial,rng=rng.bit_generator.state,seals={},archive_pool=[],archive=[],species=species.state)
            write_checkpoint(checkpoint,contract,state)
        if worker_factory is None:
            if config.get('shared_replay'):raise ValueError('Shared local replay requires the CUDA-owner service')
            from research.vectorized_backtest.v42.session_lanes import Worker
            worker_factory=Worker
        cfg=dict(config,output=str(output),boundary_stop=str(output),split='training',retain_session_residency=True,reuse_results=True,maximum_host_sessions=2,
                 plan_limit=1,
                 compile_cache_root=str(Path('D:/TradingML/runtimes/v43-cache')/str(os.getpid())))
        events=queue.Queue(maxsize=1);stopped=threading.Event();current={};progress_errors=[]
        def pump():
            while not stopped.wait(1):
                try:
                    event=events.get_nowait()
                    write_json(output/'status.json',dict(current,**event,status='running',total_generations=generations))
                except queue.Empty:pass
                except Exception as error:progress_errors.append(error);return
        import torch
        cfg['lane_memory_bytes']=torch.cuda.mem_get_info(cfg['device'])[0]*.8 if torch.device(cfg['device']).type=='cuda' and not cfg.get('shared_replay') else 0
        worker=worker_factory(cfg,events,0)
        thread=threading.Thread(target=pump,name='v43-local-progress',daemon=True);thread.start()
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
                reused=False
                if generation==1 and reuse_manifest is not None:
                    from .initial_reuse import import_receipt
                    reused=import_receipt(reuse_manifest,day,contract,token,len(payload),output,item.identity,source_token=digest([dict(p,version='v31-recurrent-v1') for p in payload]))
                    write_json(output/'initial-reuse.json',dict(reused=reused,day=day,manifest_sha256=contract['reuse_initial_manifest_sha256'],reason='matched sealed initial evaluation' if reused else 'missing or incompatible initial pool; evaluated normally'))
                evaluate=worker.run_members if config.get('persistent_session_owner') else worker.run
                evaluation={'wall_seconds':0.} if reused else evaluate(day,None,members if config.get('persistent_session_owner') else payload,generation,token,len(item.listing_ids))
                folder=output/f'generation-{generation:04d}';receipt=folder/day/'receipt.json';record=json.loads(receipt.read_bytes())
                if record['population_sha256']!=token or record['validation_opened'] is not False or record['execution']!=config['execution'] or not record['full_session']:raise ValueError('Local session receipt changed')
                from research.vectorized_backtest.v42.family_stats import aggregate
                evaluated_records=[dict(pedigree.records[int(i)]) for i in ids]
                family_report=aggregate(evaluated_records,record['metrics'])
                write_json(folder/'families.json',family_report)
                write_json(folder/'candidate-labels.json',{str(r['serial']):r['label'] for r in evaluated_records})
                status('Score and archive');score_start=time.perf_counter();prepared=local_panel([record['metrics']],objective);values=prepared['values'][0];eligible=prepared['eligible'][0];score_seconds=time.perf_counter()-score_start
                archive_start=time.perf_counter()
                archive,archive_pool=update_archive(archive_pool,members,ids,values,eligible,pedigree,day,generation,archive_capacity,archive_shortlist,profits=prepared['pnl'][0],return_reservoir=True)
                archive_seconds=time.perf_counter()-archive_start;reproduction_start=time.perf_counter()
                rank=np.lexsort((ids,-values)).tolist();source_ids=ids.copy()
                if generation<generations:
                    reproduce=next_population
                    if config.get('shared_replay') and not config.get('persistent_session_owner'):
                        from research.vectorized_backtest.v42.evolution_work import next_population as reproduce
                    from research.vectorized_backtest.v42.validation_cache import validation_scope
                    with validation_scope():
                        members,ids,report=reproduce(rng,members,ids,pedigree,rank,generation+1,selection,session_values=values[None],session_eligible=eligible[None],global_values=values,global_eligible=eligible,target_size=population_size,species=species,ordered=[record['metrics']])
                else:
                    species.assign(members,ids,values,[record['metrics']],generation,eligible=eligible)
                    report=species.telemetry
                reproduction_seconds=time.perf_counter()-reproduction_start;publication_start=time.perf_counter()
                report=dict(report,phase='local',local_slots=population_size,global_slots=0)
                births=[r for r in pedigree.records.values() if r['generation']==generation+1];write_json(folder/'births.json',births)
                write_json(folder/'scores.json',dict(ids=source_ids.tolist(),scores=values.tolist(),eligible=eligible.tolist(),archive_count=len(archive),diversity=report,objective_diagnostics={k:prepared[k][0].tolist() for k in ('pnl','T_trades','T_tickers','concentration_deduction','removed_trade_count','removed_ticker_count')},session_penalty_available=False))
                write_json(folder/'complete.json',dict(receipt_sha256=file_hash(receipt),population_sha256=token,validation_opened=False,day=day,
                    scores_sha256=file_hash(folder/'scores.json'),births_sha256=file_hash(folder/'births.json'),wall_seconds=time.perf_counter()-began,evaluation_seconds=evaluation['wall_seconds'],
                    cumulative_host_loads=getattr(worker,'loads',None),cumulative_device_uploads=getattr(worker,'uploads',None)))
                pedigree.retain(ids)
                seals[str(generation)]=file_hash(folder/'complete.json')
                state=dict(completed=generation,population=[m.payload() for m in members],ids=ids.tolist(),pedigree=list(pedigree.records.values()),next_serial=pedigree.next_serial,
                           rng=rng.bit_generator.state,seals=seals,archive_pool=archive_pool,archive=[{k:v for k,v in r.items() if k!='descriptor'} for r in archive],species=species.state)
                write_checkpoint(checkpoint,contract,state)
                completed=generation;current['completed_generations']=completed
                if progress_errors:raise RuntimeError('Local progress publication failed') from progress_errors[0]
                write_json(folder/'wall.json',dict(full_generation_seconds=time.perf_counter()-began,includes_scoring_archive_reproduction_checkpoint=True,
                           score_seconds=score_seconds,archive_seconds=archive_seconds,reproduction_seconds=reproduction_seconds,publication_checkpoint_seconds=time.perf_counter()-publication_start,
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
