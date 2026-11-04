"""Verify and combine disjoint training investigations without averaging scores."""
import argparse
import json
from pathlib import Path
import numpy as np
from .distributed_assets import sha
from .identity import digest
from .progress_sessions import read_verified
from .publication import write_json
from .full_common import aggregate,serializable
from .objectives import Objective


def combine(root,job_path):
    root=Path(root);job=read_verified(job_path);days={};spec=None;panels=[]
    for folder in sorted(root.glob('shard-*')):
        current=read_verified(folder/'spec.json')
        state=read_verified(folder/'state.json')
        if state['status']!='complete' or state.get('parent_parity')!=len(current['days']):
            raise ValueError('Investigation incomplete or parent parity unverified: '+folder.name)
        if current['validation_used'] or current['job_sha256']!=sha(job_path):raise ValueError('Investigation scope changed')
        if spec is not None and any(digest(current[k])!=digest(spec[k]) for k in
                ('members','source_generation','evidence','execution','training_sha256','runner_sha256')):
            raise ValueError('Shard comparison identity differs')
        spec=current
        for file in (folder/'days').glob('*.json'):
            saved=read_verified(file)
            if saved['spec_sha256']!=sha(folder/'spec.json'):raise ValueError('Session identity changed')
            receipt=read_verified(saved['receipt'],saved['receipt_sha256']);day=receipt['day']
            if day in days or day not in current['days']:raise ValueError('Duplicate or unassigned training session')
            if receipt['split']!='training' or receipt['validation_opened'] or receipt['execution']!=job['execution']:
                raise ValueError('Replay boundary changed')
            if receipt['population_sha256']!=digest([m['payload'] for m in spec['members']]):raise ValueError('Payload mismatch')
            days[day]=(saved,receipt)
    if set(days)!=set(job['training_days']):raise ValueError(f'Investigation incomplete: {len(days)}/{len(job["training_days"])} sessions')
    labels=[m['label'] for m in spec['members']];rows=[]
    for day in sorted(days):
        metrics=days[day][1]['metrics'];panel=aggregate([metrics],Objective(**job['objective']))
        panels.append(dict(_session_panel=panel,_summary_objective=(job['objective']['drawdown_weight'],job['objective']['loss_weight'])))
    totals=serializable(aggregate(panels,Objective(**job['objective'])))
    for i,label in enumerate(labels):
        nets=np.array([days[d][1]['metrics']['net_pnl'][i] for d in sorted(days)])
        trades=np.array([days[d][1]['metrics']['closed_positions'][i] for d in sorted(days)])
        by_ticker={};all_listings=set();monthly={};counters={}
        for day,(saved,receipt) in sorted(days.items()):
            m=receipt['metrics'];month=day[:7]
            monthly.setdefault(month,dict(net=0.,sessions=0,traded=0,profitable=0))
            monthrow=monthly[month];monthrow['net']+=m['net_pnl'][i];monthrow['sessions']+=1
            monthrow['traded']+=int(m['closed_positions'][i]>0);monthrow['profitable']+=int(m['net_pnl'][i]>0)
            all_listings.update(receipt['listing_ids'])
            for listing,pnl in m['ticker_pnl'][i].items():
                key=str(listing);by_ticker[key]=by_ticker.get(key,0.)+pnl
            for key,value in m.items():
                if key.startswith('admission_'):counters[key]=counters.get(key,0)+value[i]
        rows.append(dict(label=label,program_sha256=spec['members'][i]['program_sha256'],metrics={k:v[i] for k,v in totals.items()},
            breadth=dict(all_sessions=len(days),traded_sessions=int((trades>0).sum()),profitable_sessions=int((nets>0).sum()),
                all_listing_ids=len(all_listings),profitable_listing_ids=sum(v>0 for v in by_ticker.values()),
                traded_listing_ids=len(by_ticker)),months=monthly,
            without_best_session=float(nets.sum()-nets.max()),accounting_stress={str(cost):float(nets.sum()-cost*trades.sum()) for cost in (1,5)},
            admissions=counters,ticker_pnl=by_ticker,
            limitations=['All-listing denominator is replay universe, not a guarantee of entry eligibility.',
                        'Fixed-trade accounting stress is not execution replay under perturbed spreads.']))
    equivalence={}
    for i,label in enumerate(labels[1:],1):
        bad=[]
        for day,(_,receipt) in sorted(days.items()):
            for key in ('net_pnl','closed_positions','winning_positions','episode_profits','episode_listings','episode_entry_clocks','episode_exit_clocks','episode_branch'):
                a=np.asarray(receipt['metrics'][key][0]);b=np.asarray(receipt['metrics'][key][i])
                if a.shape!=b.shape or not np.array_equal(a,b):bad.append(dict(day=day,metric=key))
            orders=receipt['metrics']['execution_orders']
            if any(orders[0][k]!=orders[i][k] for k in ('orders','sha256')):
                bad.append(dict(day=day,metric='execution_order_journal'))
        equivalence[label]=dict(exact_episode_parity=not bad,differences=bad)
    correction=read_verified(root/'interpretation-correction.json') if (root/'interpretation-correction.json').exists() else None
    result=dict(scope='training',source_generation=spec['source_generation'],sessions=len(days),members=rows,equivalence=equivalence,interpretation_correction=correction,
                evidence=[dict(day=d,path=v[0]['receipt'],sha256=v[0]['receipt_sha256']) for d,v in sorted(days.items())],
                qualification='Measured comparisons only; endpoint interpretation and checked publication still required.')
    write_json(root/'combined-results.json',result)
    return result


def history(root):
    """Carry successful and failed completed experiments into subsequent reviews."""
    rows=[]
    for path in sorted((Path(root)/'agent-reviews/investigations').glob('*/*/combined-results.json')):
        result=read_verified(path)
        if result['scope']!='training':raise ValueError('Non-training investigation history')
        rows.append(dict(path=str(path),sha256=sha(path),sessions=result['sessions'],
            source_generation=result['source_generation'],interpretation_correction=result.get('interpretation_correction'),
            comparisons=[dict(label=m['label'],program_sha256=m['program_sha256'],metrics=m['metrics'],breadth=m['breadth'])
                         for m in result['members']]))
    return rows


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--job',required=True);a=p.parse_args()
    result=combine(a.root,a.job);print(json.dumps(dict(sessions=result['sessions'],candidates=len(result['members']))))
