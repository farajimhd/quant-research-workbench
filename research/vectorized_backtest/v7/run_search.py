"""V7 all-training assumed-fill search; no sealed-validation entry point."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json,gc,math
from contextlib import nullcontext
from .session_lanes import SessionLanes
from concurrent.futures import ThreadPoolExecutor
from .io_pipeline import Publisher,prepare_host,TransferStaging
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path
from time import perf_counter,time
import numpy as np
import torch
from .terminal import CampaignProgress
from research.vectorized_backtest.v6.torch_backtest.runtime import require_runtime,write_json,file_hash,configure_caches
from research.vectorized_backtest.v6.torch_backtest.materialize import owned_run
from research.vectorized_backtest.v6.torch_backtest.run_structure import training_days
from research.vectorized_backtest.v6.torch_backtest.stability import LowerTailDollarObjective,score
from .features import CATALOG,VERSION as FEATURE_VERSION
from .genome import Individual,sample,mutate,effective_rules
from .data import SessionData
from .evaluator import PopulationPrograms,Execution,replay_cohort

VERSION='v7-state-conditional-assumed-fill-search-v3'

def digest(value):return sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()

def source_hash():
    # Include reused program execution and certified data contracts, not just V7.
    root=Path(__file__).resolve().parents[1];h=sha256()
    for path in sorted(root.rglob('*.py')):
        h.update(str(path.relative_to(root)).encode());h.update(path.read_bytes())
    return h.hexdigest()

def run(inputs,history,output,*,population_size,generations,batch_size=128,session_workers=8,
        device='cuda',backend='compile',seed=2237,execution=Execution(),maximum_data_gib=4.,maximum_gate_gib=2.,feature_cache=None,session_execution="cohort"):
    if type(population_size) is not int or population_size<10 or generations<1 or not 1<=batch_size<=1024 or not 1<=session_workers<=30:
        raise ValueError('Explicit bounded search budget required')
    if session_execution not in ('cohort','independent'):raise ValueError('Unknown session execution')
    inputs=Path(inputs);history=Path(history);output=require_runtime(output);execution.validate()
    days=training_days(inputs)
    contract=dict(version=VERSION,source_sha256=source_hash(),feature_version=FEATURE_VERSION,
        input_receipts={d:file_hash(inputs/d/'complete.json') for d in days},
        history_receipts={d:file_hash(history/d/'complete.json') for d in days},
        feature_receipts={d:file_hash(Path(feature_cache)/d/'complete.json') for d in days} if feature_cache else None,
        population=population_size,generations=generations,batch_size=batch_size,session_workers=session_workers,
        device=device,backend=backend,seed=seed,execution=asdict(execution),maximum_data_gib=maximum_data_gib,maximum_gate_gib=maximum_gate_gib,
        objective=asdict(LowerTailDollarObjective()),timing='signals at second close; assumed fill at next observed close; terminal liquidation at last known mark')
    if session_execution=='independent':contract['session_execution']='independent'
    lane_config=dict(inputs=str(inputs),history=str(history),output=str(output),feature_cache=str(feature_cache) if feature_cache else None,
        device=device,backend=backend,batch_size=batch_size,maximum_data_gib=maximum_data_gib,maximum_gate_gib=maximum_gate_gib,execution=asdict(execution))
    checkpoint=output/'checkpoint.json';rng=np.random.default_rng(seed);completed=0
    with Publisher() as publisher,TransferStaging() as staging,ThreadPoolExecutor(max_workers=1,thread_name_prefix="v7-session") as loader,owned_run(output,version=VERSION),CampaignProgress(output,contract) as panel:
        if checkpoint.exists():
            saved=json.loads(checkpoint.read_text())
            if saved['contract']!=contract:raise ValueError('Exact V7 resume contract changed')
            population=[Individual.restore(v) for v in saved['population']]
            if digest([v.payload() for v in population])!=saved['population_sha256']:raise ValueError('Population changed')
            rng.bit_generator.state=saved['rng_state'];completed=saved['completed_generations']
            for generation in range(1,completed+1):
                root=output/f'generation-{generation:04d}';receipt=json.loads((root/'complete.json').read_text())
                for day,checksum in receipt['session_receipts'].items():
                    if file_hash(root/day/'receipt.json')!=checksum:raise ValueError('Completed V7 session receipt changed')
                if generation==completed and file_hash(root/'complete.json')!=saved['last_generation_sha256']:raise ValueError('Generation seal changed')
        else:
            population=sample(rng,population_size)
        def save(index,last=None):
            write_json(checkpoint,dict(contract=contract,completed_generations=index,population=[v.payload() for v in population],
                population_sha256=digest([v.payload() for v in population]),rng_state=rng.bit_generator.state,last_generation_sha256=last))
        if not checkpoint.exists():save(0)
        with (SessionLanes(lane_config,session_workers,panel) if session_execution=='independent' else nullcontext()) as lanes:
            host_cache={}
            for generation in range(completed+1,generations+1):
                started=perf_counter();root=require_runtime(output/f'generation-{generation:04d}');token=digest([v.payload() for v in population])
                panel.begin_generation(generation)
                already_complete=(root/'complete.json').exists()
                if already_complete:
                    record=json.loads((root/'complete.json').read_text())
                    if record['population_sha256']!=token or record['training_days']!=days or record.get('validation_opened') is not False:
                        raise ValueError('Completed generation identity changed')
                    for day,checksum in record['session_receipts'].items():
                        if file_hash(root/day/'receipt.json')!=checksum:raise ValueError('Completed generation session changed')
                parts={d:[] for d in days};timings=[]
                shared_batches={offset:PopulationPrograms(population[offset:offset+batch_size],device)
                                for offset in range(0,len(population),batch_size)} if not already_complete and session_execution=='cohort' else {}
                # Cohorts share one replay kernel over the session axis. No separate
                # thread invokes Python transitions for every session.
                def load_cohort(cohort):
                    return [(day,prepare_host(host_cache[day] if day in host_cache else SessionData(inputs/day,history/day,device='cpu',maximum_gib=maximum_data_gib,
                        feature_cache=Path(feature_cache)/day if feature_cache else None),resident=torch.device(device).type=='cuda')) for day in cohort]
                loaded=loader.submit(load_cohort,days[:session_workers]) if not already_complete and session_execution=='cohort' else None
                for first in ([] if already_complete or session_execution=='independent' else range(0,len(days),session_workers)):
                    cohort=days[first:first+session_workers];data=[]
                    try:
                        panel.update(stage='Prepare session cohort',focus=', '.join(cohort),progress={},rule_tiles=0,total_rule_tiles=0)
                        if torch.device(device).type=='cuda':
                            free,_=torch.cuda.mem_get_info()
                            reserve=len(cohort)*(maximum_data_gib+maximum_gate_gib+2.)*1024**3
                            if reserve>free*.8:raise MemoryError('Proposed V7 cohort exceeds GPU memory budget')
                        load_start=perf_counter()
                        ready=loaded.result()
                        if torch.device(device).type=='cuda':
                            reserve=sum(item.bytes+item.resident_features.bytes for _,item in ready)
                            reserve+=len(ready)*(2*maximum_gate_gib+2.)*1024**3+4*1024**3
                            if reserve>torch.cuda.mem_get_info()[0]*.8:raise MemoryError('Resident V7 features, signals and captures exceed GPU budget')
                        for day,item in ready:
                            host_cache[day]=item
                            data.append(item)
                        next_cohort=days[first+session_workers:first+2*session_workers]
                        loaded=loader.submit(load_cohort,next_cohort) if next_cohort else None
                        staging.activate(ready,device)
                        load_seconds=perf_counter()-load_start
                        for offset in range(0,len(population),batch_size):
                            members=population[offset:offset+batch_size];shared=shared_batches[offset]
                            status=dict(stage='strategy feature programs',generation=generation,total_generations=generations,
                                sessions=cohort,candidate_offset=offset,candidates=len(members),population=population_size,validation_opened=False)
                            panel.begin_batch(cohort,offset,max(v.clocks for v in data)-1)
                            rule_start=perf_counter();gates=[shared.evaluate(v,maximum_gate_gib=maximum_gate_gib,progress=panel.rules) for v in data]
                            if torch.device(device).type=='cuda':torch.cuda.synchronize()
                            rule_seconds=perf_counter()-rule_start;replay_start=perf_counter()
                            panel.begin_replay()
                            def clock_progress(clock,total):
                                panel.replay(clock,total)
                            metrics=replay_cohort(data,members,gates,execution=execution,backend=backend,progress=clock_progress)
                            timings.append(dict(sessions=cohort,offset=offset,candidates=len(members),load_seconds=load_seconds,
                                rule_seconds=rule_seconds,replay_seconds=perf_counter()-replay_start))
                            for day,result in zip(cohort,metrics):parts[day].append({k:v.detach().cpu().tolist() for k,v in result.items()})
                            for day in cohort:
                                publisher.write(require_runtime(root/day)/f'batch-{offset:06d}.json',dict(day=day,offset=offset,candidates=len(members),
                                    population_sha256=token,metrics=parts[day][-1],full_session=True,validation_opened=False))
                            publisher.write(root/'timings.json',timings);del gates,metrics
                            publisher.flush()
                            if len(shared_batches)>1:shared.close()
                            panel.complete_batch(len(cohort),rule_seconds+timings[-1]['replay_seconds']+(load_seconds if offset==0 else 0))
                        for day,item in zip(cohort,data):
                            merged={k:sum((p[k] for p in parts[day]),[]) for k in parts[day][0]}
                            publisher.write(require_runtime(root/day)/'receipt.json',dict(day=day,metrics=merged,identity=item.identity,
                                population_sha256=token,full_session=True,validation_opened=False,execution=asdict(execution)))
                        publisher.flush()
                        panel.update(completed_sessions=first+len(cohort))
                    finally:
                        for item in data:
                            if hasattr(item,'resident_features'):item.resident_features.deactivate()
                            if hasattr(item,'deactivate'):item.deactivate()
                        del data;gc.collect()
                        if torch.device(device).type=='cuda':
                            torch.cuda.empty_cache()
                            publisher.write(root/(cohort[0]+'-memory.json'),dict(
                                gpu_allocated_bytes=torch.cuda.memory_allocated(),gpu_reserved_bytes=torch.cuda.memory_reserved(),
                                gpu_free_bytes=torch.cuda.mem_get_info()[0],
                                pinned_staging_bytes=sum(s['buffer'].numel() for s in staging.slots if s.get('buffer') is not None),
                                rule_capture_bytes=sum(v.plan.capture_bytes for v in shared_batches.values() if v.plan is not None)))
                if not already_complete and lanes is not None:
                    timings=lanes.generation(days,population,generation,token)
                    publisher.write(root/'timings.json',timings)
                publisher.flush()
                for shared in shared_batches.values():shared.close()
                shared_batches.clear()
                ordered=[json.loads((root/d/'receipt.json').read_text())['metrics'] for d in days]
                panel.update(stage='Score complete training generation')
                matrix=lambda name:torch.tensor([v[name] for v in ordered],dtype=torch.bool if name=='terminal_valid' else torch.float64)
                complexity=torch.tensor([sum(p.validate(CATALOG)['active_nodes'] for p in effective_rules(v).values())+len(v.minimum_age) for v in population],dtype=torch.float64)
                ranking=score(*(matrix(k) for k in ('net_pnl','drawdown','stop_risk_dollar_seconds','capital_dollar_seconds','filled_batches','terminal_valid')),
                    complexity,config=LowerTailDollarObjective(),inactivity=matrix('inactivity_fraction').mean(0))
                eligible=[i for i,v in enumerate(ranking['feasible'].tolist()) if v]
                rank=sorted(eligible,key=lambda i:(-float(ranking['score'][i]),i))
                if not rank:raise ValueError('No valid parent; preserve evaluation and stop')
                table=[dict(candidate=i,score=float(ranking['score'][i]),pnl=float(ranking['total_pnl'][i]),
                    ex_best_pnl=float(ranking['other_days_pnl'][i]),best_session_pnl=float(ranking['best_day_pnl'][i]),
                    tail_session_mean=float(ranking['session_tail_mean_pnl'][i]),profitable_session_fraction=float(ranking['profitable_day_fraction'][i]),
                    worst_position_pnl=min(v['worst_position_pnl'][i] for v in ordered)) for i in rank]
                if not already_complete:
                    write_json(root/'ranking.json',table)
                    write_json(root/'winner.json',population[rank[0]].payload())
                    write_json(root/'complete.json',dict(population_sha256=token,training_days=days,selection_allowed=True,validation_opened=False,
                        session_receipts={d:file_hash(root/d/'receipt.json') for d in days},wall_seconds=perf_counter()-started))
                leaders=[]
                for place,i in enumerate(rank,1):
                    diagnostic={k:float(ranking[k][i]) for k in ('total_pnl','other_days_pnl','best_day_pnl','session_tail_mean_pnl','profitable_day_fraction','median_return','tail_loss')}
                    diagnostic.update(objective_components={k:float(v[i]) for k,v in ranking['components'].items()},
                        worst_pnl=min(v['net_pnl'][i] for v in ordered),worst_drawdown=max(v['drawdown'][i] for v in ordered),
                        positions=sum(v['filled_batches'][i] for v in ordered),closed_positions=sum(v['closed_positions'][i] for v in ordered),
                        stop_risk_hours=sum(v['stop_risk_dollar_seconds'][i] for v in ordered)/3600,
                        capital_hours=sum(v['capital_dollar_seconds'][i] for v in ordered)/3600,
                        worst_position_pnl=min(v['worst_position_pnl'][i] for v in ordered))
                    closed=sum(v['closed_positions'][i] for v in ordered)
                    wins=sum(v['winning_positions'][i] for v in ordered) if all('winning_positions' in v for v in ordered) else None
                    diagnostic['position_win_rate']=wins/closed if wins is not None and closed else None
                    diagnostic['winning_positions']=wins
                    leaders.append(dict(rank=place,candidate=i+1,score=float(ranking['score'][i]),valid=True,metrics=diagnostic))
                if generation<generations:
                    elite=rank[:max(1,len(rank)//10)];next_population=[population[i] for i in elite]
                    fresh=max(1,population_size//10);next_population.extend(sample(rng,fresh))
                    while len(next_population)<population_size:
                        tournament=rng.integers(0,len(rank),size=3)
                        next_population.append(mutate(rng,population[rank[int(tournament.min())]]))
                    population=next_population[:population_size]
                save(generation,file_hash(root/'complete.json'))
                panel.generation_samples.append(perf_counter()-started)
                panel.update(stage='Training complete' if generation==generations else 'Generation sealed',completed_generations=generation,
                    completed_sessions=len(days),completed_batches=len(days)*math.ceil(population_size/batch_size),top_strategies=leaders,
                    evaluation_basis=f'Generation {generation}: all30 training-session search ranking',
                    wall_seconds=perf_counter()-started)
                if (output/'STOP').exists():
                    panel.update(status='interrupted',stage='Stopped at generation boundary',finished_epoch=time());return 130
            panel.update(status='complete',stage='Training complete',completed_generations=generations,
                completed_sessions=len(days),completed_batches=len(days)*math.ceil(population_size/batch_size),finished_epoch=time())
    return 0

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('inputs','history','output'):parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--feature-cache',type=Path,required=True,help='Complete source-bound offline feature tiles')
    parser.add_argument('--population',type=int,required=True);parser.add_argument('--generations',type=int,required=True)
    parser.add_argument('--batch-size',type=int,default=128);parser.add_argument('--session-workers',type=int,default=8)
    parser.add_argument('--device',default='cuda');parser.add_argument('--backend',choices=('eager','compile'),default='compile')
    parser.add_argument('--session-execution',choices=('cohort','independent'),default='cohort')
    parser.add_argument('--seed',type=int,default=2237);parser.add_argument('--cost-bps',type=float,default=0.)
    parser.add_argument('--fee-per-share',type=float,default=0.);parser.add_argument('--minimum-order-fee',type=float,default=0.)
    parser.add_argument('--maximum-data-gib',type=float,default=4.);parser.add_argument('--maximum-gate-gib',type=float,default=2.)
    a=parser.parse_args()
    torch.set_num_threads(1)  # Match the measured V7 execution configuration.
    if a.backend=='compile':configure_caches(require_runtime(a.output)/'cache',recompile_limit=128)
    return run(a.inputs,a.history,a.output,population_size=a.population,generations=a.generations,batch_size=a.batch_size,
        session_workers=a.session_workers,device=a.device,backend=a.backend,seed=a.seed,execution=Execution(cost_bps=a.cost_bps,fee_per_share=a.fee_per_share,minimum_order_fee=a.minimum_order_fee),
        maximum_data_gib=a.maximum_data_gib,maximum_gate_gib=a.maximum_gate_gib,feature_cache=a.feature_cache,session_execution=a.session_execution)

if __name__=='__main__':raise SystemExit(main())
