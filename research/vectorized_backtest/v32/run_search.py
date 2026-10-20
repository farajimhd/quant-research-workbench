"""Robust training search; validation is a separate, display-only observer."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse
from dataclasses import asdict
from datetime import date
from hashlib import sha256
import json
from pathlib import Path
from time import perf_counter,time
import numpy as np
import torch
from research.vectorized_backtest.v6.torch_backtest.runtime import require_runtime,file_hash
from .publication import write_json,write_checkpoint
from research.vectorized_backtest.v6.torch_backtest.materialize import owned_run
from .genome import Individual,sample
from .config import MAX_NODES
from .lineage import Pedigree,Selection,next_population,session_order,training_shortlist
from .objectives import Objective,score,session_scores
from .evaluator import Execution
from .session_lanes import SessionLanes
from .terminal import CampaignProgress
from .config import VERSION as SEARCH_SPACE_VERSION

VERSION='v32-global-search-v1'


def digest(value):
    return sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def source_hash():
    from research.vectorized_backtest.v27.certified_sources import verify,RAW_SHA256
    verify()
    for name in ('indicator_math.py','enriched_math.py','features.py','indicator_identity.py'):
        if sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()!=RAW_SHA256[name]:raise ValueError('Certified producer bytes changed: '+name)
    root=Path(__file__).resolve().parents[1];h=sha256()
    for version in ('v6','v7','v27','v32'):
        for p in sorted((root/version).rglob('*.py')):
            h.update(str(p.relative_to(root)).replace('\\','/').encode());h.update(p.read_bytes().replace(b'\r\n',b'\n'))
    return h.hexdigest()


def bind_dataset(path,role):
    record=json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if record.get('version')!='v8-split-manifest-v1' or record.get('role')!=role:
        raise ValueError('Explicit versioned training/validation manifest required')
    days=record['days']
    if not days or days!=sorted(set(days)):raise ValueError('Sorted unique session dates required')
    if any(date.fromisoformat(day).isoformat()!=day for day in days):raise ValueError('Canonical session dates required')
    roots={k:Path(record[k]) for k in ('inputs','history')}
    receipts={}
    for day in days:
        receipts[day]={k:file_hash(v/day/'complete.json') for k,v in roots.items()}
        if receipts[day]!=record['receipts'][day]:raise ValueError('Dataset certificate changed')
    if 'indicator_root' not in record or set(record.get('indicator_receipts',{}))!=set(days):raise ValueError('Explicit continuous indicator bank required')
    for day in days:
        if file_hash(Path(record['indicator_root'])/day/'complete.json')!=record['indicator_receipts'][day]:raise ValueError('Indicator certificate changed')
    if 'enriched_root' not in record or set(record.get('enriched_receipts',{}))!=set(days):raise ValueError('Explicit V32 enriched bank required')
    for day in days:
        if file_hash(Path(record['enriched_root'])/day/'complete.json')!=record['enriched_receipts'][day]:raise ValueError('Enriched certificate changed')
    return record


def ranking(ordered,population,ids,pedigree,objective,*,selection_required=True,return_objective=False):
    complexity=[pedigree.records[int(i)]['complexity'] for i in ids]
    result=score(ordered,complexity,objective)
    eligible=np.flatnonzero(result['feasible']) if selection_required else np.arange(len(population))
    rank=sorted(eligible.tolist(),key=lambda i:(-float(result['total_pnl'][i]),-pedigree.records[int(ids[i])]['generation'],int(ids[i])))
    if not rank:raise ValueError('No feasible traded candidate; preserve evidence')
    rows=[]
    for place,i in enumerate(rank,1):
        diagnostic={k:float(v[i]) for k,v in result.items() if isinstance(v,np.ndarray)}
        closed=diagnostic['closed_positions'];wins=sum(v['winning_positions'][i] for v in ordered)
        ticker_pnl={}
        for session in ordered:
            for ticker,value in session.get('ticker_pnl',[{}]*len(population))[i].items():
                ticker_pnl[ticker]=ticker_pnl.get(ticker,0.)+value
        diagnostic.update(objective_components={k:float(v[i]) for k,v in result['components'].items()},
            best_ticker_pnl=max(ticker_pnl.values(),default=0.),
            turnover=sum(v.get('turnover',[0.]*len(population))[i] for v in ordered),
            reused_sessions=sum(v.get('result_reused',[False]*len(population))[i] for v in ordered),
            position_win_rate=wins/closed if closed else None,
            worst_position_pnl=min(v['worst_position_pnl'][i] for v in ordered),
            worst_pnl=min(v['net_pnl'][i] for v in ordered),
            worst_drawdown=max(v['drawdown'][i] for v in ordered),
            positions=closed,winning_positions=wins,
            capital_hours=sum(v['capital_dollar_seconds'][i] for v in ordered)/3600,
            stop_risk_hours=sum(v['stop_risk_dollar_seconds'][i] for v in ordered)/3600)
        from .genome import STAGES
        for stage in STAGES:
            key=f'signal_observations_{stage}'
            if all(key in v for v in ordered):diagnostic[key]=sum(v[key][i] for v in ordered)
        record=pedigree.records[int(ids[i])]
        for key in ('maximum_position_risk','maximum_stop_distance'):
            if all(key in v for v in ordered):diagnostic[key]=max(v[key][i] for v in ordered)
        for key in ('risk_rejected_entries','risk_rejected_adds'):
            if all(key in v for v in ordered):diagnostic[key]=sum(v[key][i] for v in ordered)
        rows.append(dict(rank=place,candidate=i+1,candidate_id=record['label'],serial=int(ids[i]),
            score=float(result['score'][i]),valid=bool(result['feasible'][i]),metrics=diagnostic))
    return (rank,rows,result) if return_objective else (rank,rows)


def publish_session_panel(root,days,ordered,values,prepared=None):
    """One compact numeric export; display sorting runs only in the observer."""
    temporary=root/'session-panel.tmp.npz'
    diagnostics={} if prepared is None else {name:prepared['_local'][key] for name,key in
        (('trimmed_profits','trimmed'),('other_session_loss_penalties','other_loss'),('drawdowns','drawdown'),('risk_hours','risk_hours'),('capital_hours','capital_hours'))}
    np.savez(temporary,days=np.asarray(days),scores=values,**diagnostics,
        profits=np.asarray([m['net_pnl'] for m in ordered],dtype=np.float64),
        active=np.asarray([m['closed_positions'] for m in ordered])>0)
    temporary.replace(root/'session-panel.npz')


def read_panel(root,days,token):
    result=[];hashes={}
    for day in days:
        path=root/day/'receipt.json';record=json.loads(path.read_text(encoding='utf-8-sig'))
        if record['population_sha256']!=token or not record['full_session']:
            raise ValueError('Session/population identity mismatch')
        result.append(record['metrics']);hashes[day]=file_hash(path)
    return result,hashes


def validation_shortlist(scores,profits,valid,ids,size=512):
    """Frozen half score/half raw-profit union; no performance eligibility."""
    byscore=np.lexsort((ids,-np.asarray(scores)));byprofit=np.lexsort((ids,-np.asarray(profits)))
    byscore=byscore[np.asarray(valid)[byscore]];byprofit=byprofit[np.asarray(valid)[byprofit]]
    chosen=[];seen=set()
    for i in np.concatenate((byscore[:size//2],byprofit[:size-size//2],byscore)):
        if int(i) not in seen:chosen.append(int(i));seen.add(int(i))
        if len(chosen)==size:break
    if not chosen:raise ValueError('No financially valid validation candidate')
    return chosen


def run(training,validation,output,*,population_size=1024,generations=50,batch_size=512,
        session_workers=2,seed=182237,device='cuda',backend='compile',execution=Execution(fee_per_share=.005,minimum_order_fee=1),
        objective=Objective(),selection=Selection(global_fraction=1.,champion_partners=0),episode_capacity=65536,maximum_gate_gib=6.665611267089844,
        maximum_data_gib=4.,plan_limit=2,capture_gib=4.,initial_population=None,predecessor_job=None,predecessor_proof=None,
        host_reserve_gib=128.,host_admission_gib=32.,replay_width_workers=None,host_prefetch=False,initialization_probe=None,shared_replay=False):

    if not 10<=population_size<=65536 or not 1<=batch_size<=1024 or not 1<=session_workers<=8 or generations<1:
        raise ValueError('Explicit bounded campaign required')
    replay_width_workers=session_workers if replay_width_workers is None else replay_width_workers
    if not 1<=replay_width_workers<=8:raise ValueError('Invalid listing-width groups')
    if type(host_prefetch) is not bool:raise ValueError('Host prefetch must be explicit boolean')
    if shared_replay and (device!='cuda' or backend!='compile' or batch_size!=512 or session_workers!=2):raise ValueError('Shared replay requires CUDA compile, batch 512 and two sessions')
    if not 1<=plan_limit<=4 or maximum_gate_gib<=0 or maximum_data_gib<=0:raise ValueError('Invalid memory/plan bounds')
    if not .25<=capture_gib<=16.:raise ValueError('Capture cache must be between 0.25 and 16 GiB per worker')
    if initial_population is not None:
        if initial_population.get('version')!='v32-local-expert-pool-v1' or digest(initial_population['population'])!=initial_population['population_sha256']:raise ValueError('Certified Phase-1 seed pool required')
        if len(initial_population['population'])!=population_size:raise ValueError('Seed pool population mismatch')
    objective.validate();selection.validate();execution.validate()
    if selection.global_fraction!=1.:raise ValueError('V32 Phase 2 uses global-only parent selection')
    if not execution.risk_protection:raise ValueError('V32 optimization requires risk protection')
    train=bind_dataset(training,'training');valid=bind_dataset(validation,'validation')
    if initial_population is not None:
        from .phase2 import verify_pool
        verify_pool(initial_population,train['days'],dataset=train)
    if set(train['days'])&set(valid['days']) or max(train['days'])>=min(valid['days']):
        raise ValueError('Chronologically disjoint validation required')
    output=require_runtime(Path(output));version_hash=source_hash()
    from .composition import settings
    contract=dict(maximum_rule_nodes=MAX_NODES,feature_semantics="v32-canonical-stable-magnitude-ratio-v1",version=VERSION,source_sha256=version_hash,training=train,validation=valid,
        population=population_size,generations=generations,batch_size=batch_size,session_workers=session_workers,replay_width_workers=replay_width_workers,neural_cuda_block=int(os.environ.get("V32_NEURAL_BLOCK","128")),
        seed=seed,device=device,backend=backend,execution=asdict(execution),objective=asdict(objective),selection=asdict(selection),recombination=settings(selection),
        episode_capacity=episode_capacity,maximum_gate_gib=maximum_gate_gib,maximum_data_gib=maximum_data_gib,
        plan_limit=plan_limit,capture_gib=capture_gib,validation_feedback=False,input_receipts=train['receipts'],session_execution='independent',
        initialization='training-activation-calibrated-v1' if initial_population is None else 'v32-local-expert-pool-v1',
        initialization_probe_sha256=file_hash(initialization_probe) if initialization_probe is not None else None,
        initial_population_sha256=None if initial_population is None else initial_population['pool_sha256'],search_space_version=SEARCH_SPACE_VERSION,maximum_host_sessions=2,
        host_reserve_gib=host_reserve_gib,host_admission_gib=host_admission_gib,host_prefetch=host_prefetch,candidate_tail_padding=False,shared_replay=shared_replay)
    def lane_config(data,role):
        return dict(inputs=data['inputs'],history=data['history'],feature_cache=data.get('feature_cache'),indicator_root=data['indicator_root'],enriched_root=data['enriched_root'],
            output=str(output/role),boundary_stop=str(output),device=device,backend=backend,batch_size=batch_size,replay_width_workers=replay_width_workers,
            maximum_data_gib=maximum_data_gib,maximum_gate_gib=maximum_gate_gib,maximum_host_sessions=2,execution=asdict(execution),
            source_sha256=version_hash,split=role,episode_capacity=episode_capacity,plan_limit=plan_limit,capture_gib=capture_gib,
            host_reserve_gib=host_reserve_gib,host_admission_gib=host_admission_gib,host_prefetch=host_prefetch,shared_replay=shared_replay)
    from .species import Species
    species=Species()
    rng=np.random.default_rng(seed);checkpoint=output/'checkpoint.json';completed=0
    with owned_run(output,version=VERSION),CampaignProgress(output,contract) as panel:
        if checkpoint.exists():
            saved=json.loads(checkpoint.read_text(encoding='utf-8-sig'))
            if saved['contract']!=contract:raise ValueError('Exact V32 resume contract changed')
            if digest(saved['state'])!=saved['state_sha256']:raise ValueError('Checkpoint state changed')
            state=saved['state'];population=[Individual.restore(v) for v in state['population']]
            ids=np.asarray(state['ids'],dtype=np.int64);pedigree=Pedigree(selection.ancestry_generations,state['pedigree'])
            rng.bit_generator.state=state['rng'];completed=state['completed'];species=Species(state.get('species'))
            for generation in range(1,completed+1):
                for role,dataset in (('training',train),('validation',valid)):
                    root=output/role/f'generation-{generation:04d}'
                    seal=json.loads((root/'complete.json').read_text(encoding='utf-8-sig'))
                    if seal.get('validation_feedback') is not False or set(seal['session_receipts'])!=set(dataset['days']):
                        raise ValueError('Sealed split coverage or feedback contract changed')
                    for day,h in seal['session_receipts'].items():
                        if file_hash(root/day/'receipt.json')!=h:raise ValueError('Sealed '+role+' receipt changed')
            panel.update(training_ranking_generation=completed,top_strategies=state.get('leaders',[]),validation_ranking=state.get('validation_ranking',[]),
                         validation_generation=completed,completed_generations=completed)
        else:
            from .initial_population import initialize
            if initial_population is None:
                from .calibration import load_probe
                if initialization_probe is None:raise ValueError('Explicit training-only initialization probe required')
                population,report=initialize(rng,population_size,probe=load_probe(initialization_probe,training))
            else:
                population=[Individual.restore(p) for p in initial_population['population']]
                report=dict(seed_count=initial_population['expert_count'],random_count=population_size-initial_population['expert_count'],sources=initial_population['sources'])
            pedigree=Pedigree(selection.ancestry_generations)
            ids=pedigree.birth(population,1,[(-1,-1)]*population_size,['fresh']*population_size)
            if initial_population is not None:
                from .phase2 import attach_source_ancestry
                attach_source_ancestry(pedigree,initial_population['sources'])
            write_json(output/'initialization.json',dict(**report,random_seed=seed,
                population_sha256=digest([m.payload() for m in population]),validation_feedback=False))

        def save(index,leaders=None,validation_rows=None):
            state=dict(completed=index,population=[m.payload() for m in population],ids=ids.tolist(),
                pedigree=pedigree.records,rng=rng.bit_generator.state,leaders=leaders or [],validation_ranking=validation_rows or [],species=species.state)
            write_checkpoint(checkpoint,contract,state)
        if not checkpoint.exists():save(0)
        with SessionLanes(lane_config(train,'training'),session_workers,panel) as trainers,SessionLanes(lane_config(valid,'validation'),session_workers,panel) as observers:
            for generation in range(completed+1,generations+1):
                started=perf_counter();panel.begin_generation(generation)
                pipeline={};stage_started=perf_counter()
                root=require_runtime(output/'training'/f'generation-{generation:04d}')
                token=digest([m.payload() for m in population])
                panel.begin_evaluation('training',len(train['days']),len(population))
                panel.update(evaluation_split='training',validation_status='Waiting for training shortlist')
                training_seal=root/'complete.json'
                if training_seal.exists():
                    sealed=json.loads(training_seal.read_text(encoding='utf-8-sig'))
                    if sealed['population_sha256']!=token:raise ValueError('Interrupted generation population changed')
                    for day,h in sealed['session_receipts'].items():
                        if file_hash(root/day/'receipt.json')!=h:raise ValueError('Interrupted generation receipt changed')
                    train_timings=[]
                else:train_timings=trainers.generation(train['days'],population,generation,token)
                pipeline['training_evaluation_seconds']=perf_counter()-stage_started;stage_started=perf_counter()
                ordered,hashes=read_panel(root,train['days'],token)
                pipeline['training_panel_read_seconds']=perf_counter()-stage_started;stage_started=perf_counter()
                rank,leaders,result=ranking(ordered,population,ids,pedigree,objective,return_objective=True)
                pipeline['training_ranking_seconds']=perf_counter()-stage_started
                selection_started=perf_counter()
                values,eligible=session_scores(ordered,objective,prepared=result)
                if not training_seal.exists():
                    write_json(root/'ranking.json',leaders)
                    publish_session_panel(root,train['days'],ordered,values,result)
                    write_json(root/'population.json',dict(population=[m.payload() for m in population],ids=ids.tolist()))
                    write_json(training_seal,dict(population_sha256=token,session_receipts=hashes,
                        training_days=train['days'],session_panel_sha256=file_hash(root/'session-panel.npz'),validation_feedback=False,training_wall_seconds=perf_counter()-started))
                panel.update(top_strategies=leaders,training_ranking_generation=generation,evaluation_basis=f'Generation {generation}: training only')
                # Freeze shortlist and successor BEFORE any validation evaluation.
                global_values=np.zeros(len(population))
                for row in leaders:global_values[row['candidate']-1]=row['score']
                shortlist=validation_shortlist(result['score'],result['total_pnl'],result['feasible'],ids,min(512,population_size))
                members=[population[i] for i in shortlist];short_ids=ids[shortlist]
                from .behavior import BehaviorIndex
                behavior=None # Species descriptors reuse the training episode ledger below; no second BehaviorIndex.
                pipeline['training_evidence_shortlist_seconds']=perf_counter()-selection_started
                # All receipts are durable. Drop obsolete session/staging data
                # before admitting reproduction or the validation role.
                phase_release_started=perf_counter();trainers.release_plans()
                pipeline['training_phase_release_seconds']=perf_counter()-phase_release_started
                reproduction_started=perf_counter()
                from .validation_cache import validation_scope
                with validation_scope():
                    successor,new_ids,diversity=next_population(rng,population,ids,pedigree,rank,generation+1,selection,session_values=values,session_eligible=eligible,global_values=global_values,global_eligible=result['global_eligible'],behavior=behavior,species=species,ordered=ordered) if generation<generations else (population,ids,{})
                if generation==generations:
                    species.assign(population,ids,global_values,ordered,generation,eligible=result['global_eligible'])
                    diversity=species.telemetry
                pipeline['reproduction_species_seconds']=perf_counter()-reproduction_started
                del ordered
                selection_seconds=perf_counter()-selection_started
                panel.update(evolution=species.telemetry)
                rng_before_validation=digest(rng.bit_generator.state)
                panel.begin_evaluation('validation',len(valid['days']),len(members))
                panel.update(evaluation_split='validation',validation_opened=True,validation_status='Evaluating display-only shortlist',
                             stage='Validation — no selection feedback')
                validation_start=perf_counter();validation_token=digest([m.payload() for m in members])
                validation_root=output/'validation'/f'generation-{generation:04d}'
                validation_seal=validation_root/'complete.json'
                if validation_seal.exists():
                    sealed=json.loads(validation_seal.read_text(encoding='utf-8-sig'))
                    if sealed['population_sha256']!=validation_token or sealed['validation_feedback'] is not False:raise ValueError('Validation seal changed')
                    for day,h in sealed['session_receipts'].items():
                        if file_hash(validation_root/day/'receipt.json')!=h:raise ValueError('Validation receipt changed')
                else:observers.generation(valid['days'],members,generation,validation_token)
                pipeline['validation_evaluation_seconds']=perf_counter()-validation_start;stage_started=perf_counter()
                heldout,validation_hashes=read_panel(validation_root,valid['days'],validation_token)
                _,validation_rows,validation_result=ranking(heldout,members,short_ids,pedigree,objective,selection_required=False,return_objective=True)
                training_ranks={int(ids[i]):place for place,i in enumerate(rank,1)}
                score_order=np.lexsort((ids,-result['score']));training_score_ranks={int(ids[i]):place for place,i in enumerate(score_order[result['feasible'][score_order]],1)}
                training_profit={row['serial']:row['metrics']['total_pnl'] for row in leaders}
                for row in validation_rows:
                    row['training_rank']=training_ranks[row['serial']]
                    row['training_score_rank']=training_score_ranks[row['serial']]
                    row['training_net_profit']=training_profit[row['serial']]
                if rng_before_validation!=digest(rng.bit_generator.state):raise RuntimeError('Validation altered training RNG')
                if not validation_seal.exists():
                    write_json(validation_root/'ranking.json',validation_rows)
                    validation_values,_=session_scores(heldout,objective,prepared=validation_result)
                    publish_session_panel(validation_root,valid['days'],heldout,validation_values,validation_result)
                    write_json(validation_seal,dict(population_sha256=validation_token,session_receipts=validation_hashes,
                        validation_feedback=False,display_only=True,session_panel_sha256=file_hash(validation_root/'session-panel.npz'),wall_seconds=perf_counter()-validation_start))
                pipeline['validation_scoring_publication_seconds']=perf_counter()-stage_started
                stage_started=perf_counter();observers.release_plans();del heldout
                pipeline['validation_phase_release_seconds']=perf_counter()-stage_started;stage_started=perf_counter()
                population,ids=successor,new_ids
                write_json(root/'diversity.json',diversity)
                save(generation,leaders,validation_rows)
                from .semantics import snapshot
                panel.update(rule_quality_rejections=snapshot())
                write_json(root/'rule-quality.json',snapshot())
                panel.publish()
                pipeline['checkpoint_publication_seconds']=perf_counter()-stage_started
                wall=perf_counter()-started
                write_json(root/'generation-profile.json',dict(source_sha256=version_hash,generation=generation,
                    training_sessions=len(train['days']),validation_sessions=len(valid['days']),population=population_size,
                    full_generation_seconds=wall,stages=pipeline,
                    ordering='All training sessions complete before the single reproduction; successor frozen before display-only validation',
                    timing_semantics='Host wall stages; concurrent evaluation internals overlap and must not be summed across sessions; no additional CUDA synchronization'))
                write_json(root/'wall.json',dict(full_generation_seconds=wall,validation_seconds=perf_counter()-validation_start,
                    includes_selection_validation_publication=True,selection_seconds=selection_seconds,training_timings=train_timings))
                panel.generation_samples.append(wall)
                panel.update(completed_generations=generation,validation_ranking=validation_rows,validation_generation=generation,
                    validation_status='Complete — display only',evaluation_split='training',diversity=diversity,
                    stage='Generation sealed',wall_seconds=wall)
                if (output/'STOP').exists():panel.update(status='interrupted',stage='Stopped at generation boundary');return 130
            panel.update(status='complete',stage='Training complete',finished_epoch=time())
    return 0


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('training','validation','output'):parser.add_argument('--'+name,type=Path,required=True)
    for name,default in (('population',1024),('generations',50),('batch-size',512),('session-workers',2),('seed',182237),('episode-capacity',65536),('plan-limit',2)):
        parser.add_argument('--'+name,type=int,default=default)
    parser.add_argument('--device',default='cuda');parser.add_argument('--backend',choices=('compile','eager'),default='compile')
    parser.add_argument('--predecessor-job',type=Path,required=True)
    parser.add_argument('--objective',type=Path,help='JSON with explicit Objective fields')
    parser.add_argument('--selection',type=Path,help='JSON with explicit Selection fields')
    parser.add_argument('--initial-population',type=Path,help='Verified V32 local expert pool')
    parser.add_argument('--initialization-probe',type=Path)
    parser.add_argument('--maximum-gate-gib',type=float,default=6.665611267089844)
    parser.add_argument('--maximum-data-gib',type=float,default=4.)
    parser.add_argument('--capture-gib',type=float,default=4.,help='Total graph-cache GiB per worker, split across retained plans')
    args=vars(parser.parse_args());args['population_size']=args.pop('population')
    args['objective']=Objective(**json.loads(args['objective'].read_text(encoding='utf-8-sig'))) if args['objective'] else Objective()
    args['selection']=Selection(**json.loads(args['selection'].read_text(encoding='utf-8-sig'))) if args['selection'] else Selection(global_fraction=1.,champion_partners=0)
    if args['initial_population'] is not None:args['initial_population']=json.loads(args['initial_population'].read_text(encoding='utf-8-sig'))
    elif args['initialization_probe'] is None:parser.error('Fresh initialization requires a training probe')
    torch.set_num_threads(1)
    return run(**args)


if __name__=='__main__':raise SystemExit(main())
