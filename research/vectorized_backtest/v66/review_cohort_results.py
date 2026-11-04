"""Verified, streaming cohort evidence aggregation; never grants review approval."""
import argparse
from pathlib import Path
import numpy as np
from .progress_sessions import read_verified
from .distributed_assets import sha
from .identity import digest
from .publication import write_json


def combine(root, job_path):
    root=Path(root);plan=read_verified(root/'plan.json');job=read_verified(job_path)
    if plan['scope']!='training' or plan['validation_used'] or plan['job_sha256']!=sha(job_path):
        raise ValueError('Training identity changed')
    n=len(plan['members']);rows=[dict(index=i,net=0.,trades=0,wins=0,months={},sessions=[],tickers={},admissions={}) for i in range(n)]
    endpoints={};days=set();listing_ids=set();evidence=[];code=None;equivalence={str(r['serial']):[] for r in plan['records'] if r['child_index'] is not None}
    for folder in sorted(root.glob('shard-*')):
        spec=read_verified(folder/'spec.json');state=read_verified(folder/'state.json')
        if state['status']!='complete' or set(state['completed'])!=set(spec['days']) or state['parent_parity']!=len(spec['days']):
            raise ValueError('Incomplete shard: '+folder.name)
        if spec['plan_sha256']!=sha(root/'plan.json') or spec['job_sha256']!=sha(job_path):raise ValueError('Shard identity changed')
        if code is not None and code!=spec['code_sha256']:raise ValueError('Mixed review code')
        code=spec['code_sha256']
        for day in spec['days']:
            path=folder/'days'/f'{day}.json';saved=read_verified(path)
            if saved['spec_sha256']!=sha(folder/'spec.json') or saved['parent_parity']!=len(plan['records']):raise ValueError('Unverified parent replay')
            receipt=read_verified(saved['receipt'],saved['receipt_sha256'])
            if day in days or receipt['day']!=day or day not in job['training_days']:raise ValueError('Duplicate or foreign session')
            if receipt['split']!='training' or receipt['validation_opened'] or receipt['execution']!=job['execution'] or receipt['population_sha256']!=digest(plan['members']):raise ValueError('Replay contract changed')
            days.add(day);listing_ids.update(receipt['listing_ids']);m=receipt['metrics'];evidence.append(dict(day=day,path=str(path),sha256=sha(path),seconds=saved['seconds']))
            for i,row in enumerate(rows):
                net=m['net_pnl'][i];trades=m['closed_positions'][i]
                row['net']+=net;row['trades']+=trades;row['wins']+=m['winning_positions'][i]
                row['sessions'].append(dict(day=day,net=net,trades=trades))
                month=row['months'].setdefault(day[:7],dict(net=0.,sessions=0,traded=0,profitable=0))
                month['net']+=net;month['sessions']+=1;month['traded']+=int(trades>0);month['profitable']+=int(net>0)
                for ticker,pnl in m['ticker_pnl'][i].items():row['tickers'][str(ticker)]=row['tickers'].get(str(ticker),0.)+pnl
                for key,value in m.items():
                    if key.startswith('admission_'):row['admissions'][key]=row['admissions'].get(key,0)+value[i]
            for record in plan['records']:
                p=record['parent_index'];c=record['child_index']
                if c is None:continue
                differences=[]
                for key in ('net_pnl','closed_positions','winning_positions','episode_profits','episode_listings','episode_entry_clocks','episode_exit_clocks','episode_branch'):
                    if not np.array_equal(np.asarray(m[key][p]),np.asarray(m[key][c])):differences.append(key)
                if any(m['execution_orders'][p][key]!=m['execution_orders'][c][key] for key in ('orders','sha256')):differences.append('order_journal')
                if differences:equivalence[str(record['serial'])].append(dict(day=day,differences=differences))
            for key,value in saved['endpoints'].items():
                aggregate=endpoints.setdefault(key,dict(sessions=0,totals={},causes={},actions={},nodes={},tickers={},traces=[]))
                aggregate['sessions']+=1
                for field in ('eligible','entry_signals','price_rejected','guard_pass'):aggregate['totals'][field]=aggregate['totals'].get(field,0)+value[field]
                for field in ('causes','actions'):
                    for name,count in value[field].items():aggregate[field][name]=aggregate[field].get(name,0)+count
                for stage,counts in value['nodes'].items():
                    target=aggregate['nodes'].setdefault(stage,{})
                    for name,count in counts.items():target[name]=target.get(name,0)+count
                for ticker,counts in value['tickers'].items():
                    target=aggregate['tickers'].setdefault(ticker,{})
                    for name,count in counts.items():target[name]=target.get(name,0)+count
                # Keep actual traces for each chronological subperiod; full traces remain in hashed daily evidence.
                if not any(t['day'][:7]==day[:7] for t in aggregate['traces']):
                    aggregate['traces'].append(dict(day=day,columns=value['trace_columns'],events=value['bounded_traces']))
    if days!=set(job['training_days']):raise ValueError('Full training union missing')
    for row in rows:
        row['sessions'].sort(key=lambda s:s['day'])
        row['breadth']=dict(all_sessions=len(days),traded_sessions=sum(s['trades']>0 for s in row['sessions']),profitable_sessions=sum(s['net']>0 for s in row['sessions']),all_listing_ids=len(listing_ids),traded_tickers=len(row['tickers']),profitable_tickers=sum(v>0 for v in row['tickers'].values()))
        row['without_best_session']=row['net']-max(s['net'] for s in row['sessions'])
        row['fixed_trade_cost_stress']={str(cost):row['net']-cost*row['trades'] for cost in (1,5)}
    result=dict(scope='training',validation_used=False,sessions=len(days),source_generation=plan['source_generation'],review_epoch=plan['review_epoch'],parent_count=len(plan['records']),members=rows,comparisons=equivalence,evidence=evidence,code_sha256=code,limitations=['Fixed-trade cost stress is accounting sensitivity, not perturbed execution replay.','Observed episode/order equality is not a proof of universal semantic equivalence.','All endpoint checks still require manual interpretation; aggregation does not approve any candidate.'])
    write_json(root/'combined-results.json',result);write_json(root/'endpoint-observations.json',endpoints)
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--job',required=True);a=p.parse_args()
    result=combine(a.root,a.job);print({'sessions':result['sessions'],'parents':result['parent_count']})
