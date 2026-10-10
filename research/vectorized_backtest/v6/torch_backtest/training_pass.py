"""Bounded concurrent full-training evaluation with a strict selection barrier.

The caller supplies an independently owned session evaluator. Evaluators must
return complete candidate coverage in original population order. This module
never opens validation inputs or changes a population before the full barrier.
"""
from concurrent.futures import ThreadPoolExecutor,wait,FIRST_COMPLETED
from copy import deepcopy
from dataclasses import asdict
import json
from hashlib import sha256
import torch
from .runtime import write_json,file_hash
from .stability import score,LowerTailDollarObjective


def population_hash(population):
    return sha256(json.dumps([v.payload() for v in population],sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def full_training_pass(training,validation_days,population,evaluate,output,*,workers=2,objective=LowerTailDollarObjective()):
    days=[s['day'] for s in training]
    if len(days)!=30 or len(set(days))!=30 or set(days)&set(validation_days):raise ValueError('Exactly thirty disjoint training sessions required')
    if type(workers) is not int or not 1<=workers<=8 or not population:raise ValueError('Invalid bounded evaluator concurrency/population')
    objective.validate();token=population_hash(population);n=len(population)
    results={};receipts={};cursor=0;pending={}
    metric_names=('net_pnl','drawdown','stop_risk_dollar_seconds','capital_dollar_seconds','filled_batches','terminal_valid')
    def submit(pool):
        nonlocal cursor
        session=training[cursor];cursor+=1
        # Each evaluator receives its own population copy and session contract.
        future=pool.submit(evaluate,deepcopy(session),deepcopy(population),output/session['day'])
        pending[future]=session['day']
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for _ in range(min(workers,len(training))):submit(pool)
        while pending:
            finished=wait(pending,return_when=FIRST_COMPLETED)[0]
            for future in finished:
                day=pending.pop(future)
                try:
                    record=future.result()
                    if record.get('day')!=day or record.get('population_sha256')!=token or record.get('candidate_indices')!=list(range(n)):
                        raise ValueError('Session/candidate/population receipt mismatch')
                    if not record.get('full_session') or record.get('validation_opened',True):raise ValueError('Partial or sealed-session result cannot reach selection')
                    metrics=record['metrics']
                    if any(torch.as_tensor(metrics[k]).shape!=(n,) for k in metric_names):raise ValueError('Incomplete all-candidate financial metrics')
                    path=output/day/'receipt.json';write_json(path,record)
                    results[day]=metrics;receipts[day]=file_hash(path)
                except BaseException:
                    for other in pending:other.cancel()
                    raise
                write_json(output/'status.json',dict(completed=len(results),active=len(pending),queued=30-cursor,total=30,population_sha256=token,selection_allowed=False,validation_opened=False))
                if cursor<30:submit(pool)
    if population_hash(population)!=token:raise ValueError('Population changed during the full-training barrier')
    ordered=[results[day] for day in days]
    matrix=lambda name:torch.stack([torch.as_tensor(r[name],dtype=torch.bool if name=='terminal_valid' else torch.float64) for r in ordered])
    complexity=torch.tensor([sum(len(p.nodes) for p in v.programs().values()) for v in population],dtype=torch.float64)
    if any('inactivity_fraction' not in r for r in ordered):raise ValueError('Elapsed inactivity must be supplied by each evaluator')
    inactivity=matrix('inactivity_fraction').mean(0)
    ranked=score(*(matrix(k) for k in metric_names),complexity,config=objective,inactivity=inactivity)
    def serial(value):
        if isinstance(value,torch.Tensor):return value.tolist()
        if isinstance(value,dict):return {k:serial(v) for k,v in value.items()}
        return value
    record=dict(status='complete',training_days=days,population_sha256=token,session_receipts=receipts,objective=asdict(objective),ranking=serial(ranked),selection_allowed=True,validation_opened=False)
    write_json(output/'complete.json',record)
    return ranked,ordered
