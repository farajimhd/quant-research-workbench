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
from research.vectorized_backtest.v6.torch_backtest.runtime import require_runtime,write_json,file_hash
from research.vectorized_backtest.v6.torch_backtest.materialize import owned_run
from .genome import Individual,sample
from .lineage import Pedigree,Selection,next_population
from .objectives import Objective,score
from .evaluator import Execution
from .session_lanes import SessionLanes
from .terminal import CampaignProgress

VERSION='v8-robust-pedigree-observer-v1'


def digest(value):
    return sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def source_hash():
    root=Path(__file__).resolve().parents[1];h=sha256()
    for version in ('v6','v7','v8'):
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
    return record


def ranking(ordered,population,ids,pedigree,objective,*,selection_required=True):
    complexity=[pedigree.records[int(i)]['complexity'] for i in ids]
    result=score(ordered,complexity,objective)
    eligible=np.flatnonzero(result['feasible']) if selection_required else np.arange(len(population))
    rank=sorted(eligible.tolist(),key=lambda i:(-float(result['score'][i]),-pedigree.records[int(ids[i])]['generation'],int(ids[i])))
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
        record=pedigree.records[int(ids[i])]
        rows.append(dict(rank=place,candidate=i+1,candidate_id=record['label'],serial=int(ids[i]),
            score=float(result['score'][i]),valid=bool(result['feasible'][i]),metrics=diagnostic))
    return rank,rows


def read_panel(root,days,token):
    result=[];hashes={}
    for day in days:
        path=root/day/'receipt.json';record=json.loads(path.read_text(encoding='utf-8-sig'))
        if record['population_sha256']!=token or not record['full_session']:
            raise ValueError('Session/population identity mismatch')
        result.append(record['metrics']);hashes[day]=file_hash(path)
    return result,hashes


def run(training,validation,output,*,population_size=1024,generations=50,batch_size=512,
        session_workers=2,seed=2237,device='cuda',backend='compile',execution=Execution(fee_per_share=.005,minimum_order_fee=1),
        objective=Objective(),selection=Selection(),episode_capacity=4096,maximum_gate_gib=6.665611267089844,
        maximum_data_gib=4.,plan_limit=2,initial_population=None):
    if not 10<=population_size<=65536 or not 1<=batch_size<=1024 or not 1<=session_workers<=8 or generations<1:
        raise ValueError('Explicit bounded campaign required')
    if not 1<=plan_limit<=4 or maximum_gate_gib<=0 or maximum_data_gib<=0:raise ValueError('Invalid memory/plan bounds')
    objective.validate();selection.validate();execution.validate()
    train=bind_dataset(training,'training');valid=bind_dataset(validation,'validation')
    if set(train['days'])&set(valid['days']) or max(train['days'])>=min(valid['days']):
        raise ValueError('Chronologically disjoint validation required')
    output=require_runtime(Path(output));version_hash=source_hash()
    contract=dict(version=VERSION,source_sha256=version_hash,training=train,validation=valid,
        population=population_size,generations=generations,batch_size=batch_size,session_workers=session_workers,
        seed=seed,device=device,backend=backend,execution=asdict(execution),objective=asdict(objective),selection=asdict(selection),
        episode_capacity=episode_capacity,maximum_gate_gib=maximum_gate_gib,maximum_data_gib=maximum_data_gib,
        plan_limit=plan_limit,validation_feedback=False,input_receipts=train['receipts'],session_execution='independent',
        initial_population_sha256=file_hash(initial_population) if initial_population else None)
    def lane_config(data,role):
        return dict(inputs=data['inputs'],history=data['history'],feature_cache=data.get('feature_cache'),
            output=str(output/role),device=device,backend=backend,batch_size=batch_size,
            maximum_data_gib=maximum_data_gib,maximum_gate_gib=maximum_gate_gib,execution=asdict(execution),
            source_sha256=version_hash,split=role,episode_capacity=episode_capacity,plan_limit=plan_limit)
    rng=np.random.default_rng(seed);checkpoint=output/'checkpoint.json';completed=0
    with owned_run(output,version=VERSION),CampaignProgress(output,contract) as panel:
        if checkpoint.exists():
            saved=json.loads(checkpoint.read_text(encoding='utf-8-sig'))
            if saved['contract']!=contract:raise ValueError('Exact V8 resume contract changed')
            if digest(saved['state'])!=saved['state_sha256']:raise ValueError('Checkpoint state changed')
            state=saved['state'];population=[Individual.restore(v) for v in state['population']]
            ids=np.asarray(state['ids'],dtype=np.int64);pedigree=Pedigree(selection.ancestry_generations,state['pedigree'])
            rng.bit_generator.state=state['rng'];completed=state['completed']
            for generation in range(1,completed+1):
                for role,dataset in (('training',train),('validation',valid)):
                    root=output/role/f'generation-{generation:04d}'
                    seal=json.loads((root/'complete.json').read_text(encoding='utf-8-sig'))
                    if seal.get('validation_feedback') is not False or set(seal['session_receipts'])!=set(dataset['days']):
                        raise ValueError('Sealed split coverage or feedback contract changed')
                    for day,h in seal['session_receipts'].items():
                        if file_hash(root/day/'receipt.json')!=h:raise ValueError('Sealed '+role+' receipt changed')
            panel.update(top_strategies=state.get('leaders',[]),validation_ranking=state.get('validation_ranking',[]),
                         validation_generation=completed,completed_generations=completed)
        else:
            population=sample(rng,population_size) if initial_population is None else [Individual.restore(v) for v in json.loads(Path(initial_population).read_text(encoding='utf-8-sig'))['population']]
            if len(population)!=population_size:raise ValueError('Initial population size differs from contract')
            pedigree=Pedigree(selection.ancestry_generations)
            ids=pedigree.birth(population,1,[(-1,-1)]*population_size,['fresh']*population_size)
        def save(index,leaders=None,validation_rows=None):
            state=dict(completed=index,population=[m.payload() for m in population],ids=ids.tolist(),
                pedigree=pedigree.records,rng=rng.bit_generator.state,leaders=leaders or [],validation_ranking=validation_rows or [])
            write_json(checkpoint,dict(contract=contract,state=state,state_sha256=digest(state)))
        if not checkpoint.exists():save(0)
        with SessionLanes(lane_config(train,'training'),session_workers,panel) as trainers,SessionLanes(lane_config(valid,'validation'),session_workers,panel) as observers:
            for generation in range(completed+1,generations+1):
                started=perf_counter();panel.begin_generation(generation)
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
                ordered,hashes=read_panel(root,train['days'],token)
                rank,leaders=ranking(ordered,population,ids,pedigree,objective)
                if not training_seal.exists():
                    write_json(root/'ranking.json',leaders)
                    write_json(root/'population.json',dict(population=[m.payload() for m in population],ids=ids.tolist()))
                    write_json(training_seal,dict(population_sha256=token,session_receipts=hashes,
                        training_days=train['days'],validation_feedback=False,training_wall_seconds=perf_counter()-started))
                panel.update(top_strategies=leaders,evaluation_basis=f'Generation {generation}: training only')
                # Freeze shortlist and successor BEFORE any validation evaluation.
                shortlist=rank[:batch_size];members=[population[i] for i in shortlist];short_ids=ids[shortlist]
                successor,new_ids,diversity=next_population(rng,population,ids,pedigree,rank,generation+1,selection) if generation<generations else (population,ids,{})
                rng_before_validation=digest(rng.bit_generator.state)
                trainers.release_plans()
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
                heldout,validation_hashes=read_panel(validation_root,valid['days'],validation_token)
                _,validation_rows=ranking(heldout,members,short_ids,pedigree,objective,selection_required=False)
                training_ranks={int(ids[i]):place for place,i in enumerate(rank,1)}
                for row in validation_rows:row['training_rank']=training_ranks[row['serial']]
                if rng_before_validation!=digest(rng.bit_generator.state):raise RuntimeError('Validation altered training RNG')
                if not validation_seal.exists():
                    write_json(validation_root/'ranking.json',validation_rows)
                    write_json(validation_seal,dict(population_sha256=validation_token,session_receipts=validation_hashes,
                        validation_feedback=False,display_only=True,wall_seconds=perf_counter()-validation_start))
                observers.release_plans()
                population,ids=successor,new_ids
                write_json(root/'diversity.json',diversity)
                save(generation,leaders,validation_rows)
                panel.publish()
                wall=perf_counter()-started
                write_json(root/'wall.json',dict(full_generation_seconds=wall,validation_seconds=perf_counter()-validation_start,
                    includes_selection_validation_publication=True,training_timings=train_timings))
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
    for name,default in (('population',1024),('generations',50),('batch-size',512),('session-workers',2),('seed',2237),('episode-capacity',4096),('plan-limit',2)):
        parser.add_argument('--'+name,type=int,default=default)
    parser.add_argument('--device',default='cuda');parser.add_argument('--backend',choices=('compile','eager'),default='compile')
    parser.add_argument('--objective',type=Path,help='JSON with explicit Objective fields')
    parser.add_argument('--selection',type=Path,help='JSON with explicit Selection fields')
    parser.add_argument('--initial-population',type=Path,help='Explicit immutable population/checkpoint for matched-workload profiling')
    parser.add_argument('--maximum-gate-gib',type=float,default=6.665611267089844)
    parser.add_argument('--maximum-data-gib',type=float,default=4.)
    args=vars(parser.parse_args());args['population_size']=args.pop('population')
    args['objective']=Objective(**json.loads(args['objective'].read_text(encoding='utf-8-sig'))) if args['objective'] else Objective()
    args['selection']=Selection(**json.loads(args['selection'].read_text(encoding='utf-8-sig'))) if args['selection'] else Selection()
    torch.set_num_threads(1)
    return run(**args)


if __name__=='__main__':raise SystemExit(main())
