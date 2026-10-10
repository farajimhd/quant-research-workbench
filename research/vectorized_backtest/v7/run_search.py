"""V7 all-training assumed-fill search; no sealed-validation entry point."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json,gc,math
from concurrent.futures import ThreadPoolExecutor
from .io_pipeline import Publisher,prepare_host,activate_nonblocking
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path
from time import perf_counter
import numpy as np
import torch
from rich.progress import Progress,BarColumn,TextColumn,TimeElapsedColumn,TimeRemainingColumn
from rich.table import Table
from research.vectorized_backtest.v6.torch_backtest.runtime import require_runtime,write_json,file_hash,configure_caches
from research.vectorized_backtest.v6.torch_backtest.materialize import owned_run
from research.vectorized_backtest.v6.torch_backtest.run_structure import training_days
from research.vectorized_backtest.v6.torch_backtest.stability import LowerTailDollarObjective,score
from .features import CATALOG,VERSION as FEATURE_VERSION
from .genome import Individual,sample,mutate
from .data import SessionData
from .evaluator import PopulationPrograms,Execution,replay_cohort

VERSION='v7-state-conditional-assumed-fill-search-v2'

def digest(value):return sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()

def source_hash():
    # Include reused program execution and certified data contracts, not just V7.
    root=Path(__file__).resolve().parents[1];h=sha256()
    for path in sorted(root.rglob('*.py')):
        h.update(str(path.relative_to(root)).encode());h.update(path.read_bytes())
    return h.hexdigest()

def run(inputs,history,output,*,population_size,generations,batch_size=128,session_workers=8,
        device='cuda',backend='compile',seed=2237,execution=Execution(),maximum_data_gib=4.,maximum_gate_gib=2.,feature_cache=None):
    if type(population_size) is not int or population_size<10 or generations<1 or not 1<=batch_size<=1024 or not 1<=session_workers<=30:
        raise ValueError('Explicit bounded search budget required')
    inputs=Path(inputs);history=Path(history);output=require_runtime(output);execution.validate()
    days=training_days(inputs)
    contract=dict(version=VERSION,source_sha256=source_hash(),feature_version=FEATURE_VERSION,
        input_receipts={d:file_hash(inputs/d/'complete.json') for d in days},
        history_receipts={d:file_hash(history/d/'complete.json') for d in days},
        feature_receipts={d:file_hash(Path(feature_cache)/d/'complete.json') for d in days} if feature_cache else None,
        population=population_size,generations=generations,batch_size=batch_size,session_workers=session_workers,
        device=device,backend=backend,seed=seed,execution=asdict(execution),maximum_data_gib=maximum_data_gib,maximum_gate_gib=maximum_gate_gib,
        objective=asdict(LowerTailDollarObjective()),timing='signals at second close; assumed fill at next observed close; terminal liquidation at last known mark')
    checkpoint=output/'checkpoint.json';rng=np.random.default_rng(seed);completed=0
    with Publisher() as publisher,ThreadPoolExecutor(max_workers=1,thread_name_prefix="v7-session") as loader,owned_run(output,version=VERSION),Progress(TextColumn('{task.description}'),BarColumn(),TextColumn('{task.completed}/{task.total}'),TimeElapsedColumn(),TimeRemainingColumn()) as panel:
        generation_task=panel.add_task('Generations sealed',total=generations)
        batch_task=panel.add_task('Session cohorts × strategy batches',total=math.ceil(len(days)/session_workers)*math.ceil(population_size/batch_size))
        clock_task=panel.add_task('Current cohort replay clocks',total=1)
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
        host_cache={}
        for generation in range(completed+1,generations+1):
            started=perf_counter();root=require_runtime(output/f'generation-{generation:04d}');token=digest([v.payload() for v in population])
            panel.update(generation_task,completed=generation-1);panel.update(batch_task,completed=0)
            already_complete=(root/'complete.json').exists()
            if already_complete:
                record=json.loads((root/'complete.json').read_text())
                if record['population_sha256']!=token or record['training_days']!=days or record.get('validation_opened') is not False:
                    raise ValueError('Completed generation identity changed')
                for day,checksum in record['session_receipts'].items():
                    if file_hash(root/day/'receipt.json')!=checksum:raise ValueError('Completed generation session changed')
            parts={d:[] for d in days};timings=[]
            shared_batches={offset:PopulationPrograms(population[offset:offset+batch_size],device)
                            for offset in range(0,len(population),batch_size)} if not already_complete else {}
            # Cohorts share one replay kernel over the session axis. No separate
            # thread invokes Python transitions for every session.
            def load_cohort(cohort):
                return [(day,prepare_host(host_cache[day] if day in host_cache else SessionData(inputs/day,history/day,device='cpu',maximum_gib=maximum_data_gib,
                    feature_cache=Path(feature_cache)/day if feature_cache else None),pin=torch.device(device).type=='cuda')) for day in cohort]
            loaded=loader.submit(load_cohort,days[:session_workers]) if not already_complete else None
            for first in ([] if already_complete else range(0,len(days),session_workers)):
                cohort=days[first:first+session_workers];data=[]
                try:
                    if torch.device(device).type=='cuda':
                        free,_=torch.cuda.mem_get_info()
                        reserve=len(cohort)*(maximum_data_gib+maximum_gate_gib+2.)*1024**3
                        if reserve>free*.8:raise MemoryError('Proposed V7 cohort exceeds GPU memory budget')
                    load_start=perf_counter()
                    for day,item in loaded.result():
                        host_cache[day]=item
                        activate_nonblocking(item,device)
                        data.append(item)
                    next_cohort=days[first+session_workers:first+2*session_workers]
                    loaded=loader.submit(load_cohort,next_cohort) if next_cohort else None
                    load_seconds=perf_counter()-load_start
                    for offset in range(0,len(population),batch_size):
                        members=population[offset:offset+batch_size];shared=shared_batches[offset]
                        status=dict(stage='strategy feature programs',generation=generation,total_generations=generations,
                            sessions=cohort,candidate_offset=offset,candidates=len(members),population=population_size,validation_opened=False)
                        publisher.write(output/'status.json',status)
                        rule_start=perf_counter();gates=[shared.evaluate(v,maximum_gate_gib=maximum_gate_gib) for v in data]
                        if torch.device(device).type=='cuda':torch.cuda.synchronize()
                        rule_seconds=perf_counter()-rule_start;replay_start=perf_counter()
                        publisher.write(output/'status.json',dict(status,stage='batched position replay'))
                        panel.update(clock_task,total=max(v.clocks for v in data)-1,completed=0)
                        def clock_progress(clock,total):
                            panel.update(clock_task,completed=clock,total=total)
                            publisher.write(output/'status.json',dict(status,stage='batched position replay',replay_clock=clock,replay_clocks=total))
                        metrics=replay_cohort(data,members,gates,execution=execution,backend=backend,progress=clock_progress)
                        timings.append(dict(sessions=cohort,offset=offset,candidates=len(members),load_seconds=load_seconds,
                            rule_seconds=rule_seconds,replay_seconds=perf_counter()-replay_start))
                        for day,result in zip(cohort,metrics):parts[day].append({k:v.detach().cpu().tolist() for k,v in result.items()})
                        publisher.write(root/'timings.json',timings);del gates,metrics
                        panel.advance(batch_task)
                    for day,item in zip(cohort,data):
                        merged={k:sum((p[k] for p in parts[day]),[]) for k in parts[day][0]}
                        publisher.write(require_runtime(root/day)/'receipt.json',dict(day=day,metrics=merged,identity=item.identity,
                            population_sha256=token,full_session=True,validation_opened=False,execution=asdict(execution)))
                finally:
                    for item in data:
                        if hasattr(item,'deactivate'):item.deactivate()
                    del data;gc.collect()
                    if torch.device(device).type=='cuda':torch.cuda.empty_cache()
            publisher.flush()
            shared_batches.clear()
            ordered=[json.loads((root/d/'receipt.json').read_text())['metrics'] for d in days]
            matrix=lambda name:torch.tensor([v[name] for v in ordered],dtype=torch.bool if name=='terminal_valid' else torch.float64)
            complexity=torch.tensor([sum(p.validate(CATALOG)['active_nodes'] for p in (*v.rules.values(),*(v.open_rules or {}).values()))+len(v.minimum_age) for v in population],dtype=torch.float64)
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
            display=Table(title=f'V7 generation {generation} • first 50 strategies')
            for name in ('Strategy','Score','P&L','Ex-best P&L','Best session','Tail mean','Winning sessions','Worst position'):display.add_column(name)
            for row in table[:50]:display.add_row(str(row['candidate']),*(f"{row[k]:.2f}" for k in ('score','pnl','ex_best_pnl','best_session_pnl','tail_session_mean','profitable_session_fraction','worst_position_pnl')))
            panel.console.print(display)
            if generation<generations:
                elite=rank[:max(1,len(rank)//10)];next_population=[population[i] for i in elite]
                fresh=max(1,population_size//10);next_population.extend(sample(rng,fresh))
                while len(next_population)<population_size:
                    tournament=rng.integers(0,len(rank),size=3)
                    next_population.append(mutate(rng,population[rank[int(tournament.min())]]))
                population=next_population[:population_size]
            save(generation,file_hash(root/'complete.json'))
            panel.update(generation_task,completed=generation)
            publisher.write(output/'status.json',dict(stage='complete' if generation==generations else 'generation sealed',completed_generations=generation,
                total_generations=generations,wall_seconds=perf_counter()-started,validation_opened=False))
            if (output/'STOP').exists():return 130
    return 0

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('inputs','history','output'):parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--feature-cache',type=Path,required=True,help='Complete source-bound offline feature tiles')
    parser.add_argument('--population',type=int,required=True);parser.add_argument('--generations',type=int,required=True)
    parser.add_argument('--batch-size',type=int,default=128);parser.add_argument('--session-workers',type=int,default=8)
    parser.add_argument('--device',default='cuda');parser.add_argument('--backend',choices=('eager','compile'),default='compile')
    parser.add_argument('--seed',type=int,default=2237);parser.add_argument('--cost-bps',type=float,default=0.)
    parser.add_argument('--maximum-data-gib',type=float,default=4.);parser.add_argument('--maximum-gate-gib',type=float,default=2.)
    a=parser.parse_args()
    if a.backend=='compile':configure_caches(require_runtime(a.output)/'cache',recompile_limit=128)
    return run(a.inputs,a.history,a.output,population_size=a.population,generations=a.generations,batch_size=a.batch_size,
        session_workers=a.session_workers,device=a.device,backend=a.backend,seed=a.seed,execution=Execution(cost_bps=a.cost_bps),
        maximum_data_gib=a.maximum_data_gib,maximum_gate_gib=a.maximum_gate_gib,feature_cache=a.feature_cache)

if __name__=='__main__':raise SystemExit(main())
