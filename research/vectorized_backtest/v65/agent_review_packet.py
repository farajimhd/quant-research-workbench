"""Small training-only review packet; explicit export, never on the GPU lane."""
import argparse,json,os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
from pathlib import Path
import numpy as np
from .agent_intervention import POLICY,evidence
from .publication import write_json


def export(root,job,generation,out):
    folder,binding=evidence(root,generation)
    training=json.loads((folder/'training.json').read_bytes())
    sessions=json.loads((folder/'session-metrics.json').read_bytes())
    population=json.loads((folder/'population.json').read_bytes())
    records=json.loads((folder/'evaluated-pedigree.json').read_bytes())
    ids=training['ids'];panel=training['panel']
    if ids!=population['ids'] or ids!=sessions['ids'] or [r['serial'] for r in records]!=ids:
        raise ValueError('Review candidate alignment differs')
    if set(sessions['sessions'])!=set(job['training_days']):raise ValueError('Training scope differs')
    days=sorted(sessions['sessions'])
    net=np.asarray([sessions['sessions'][d]['net_pnl'] for d in days])
    score=np.asarray(panel['score']);eligible=np.asarray(panel['eligible'],bool)
    active=np.asarray(panel['closed_positions'])>=5
    # Mixed global robustness and session champions; capped family concentration.
    pools=[np.argsort(-score),np.argsort(-net.sum(0)),np.argsort(-(net.sum(0)-net.max(0))),
           np.argsort(-(net>0).sum(0)),np.argmax(net,axis=1)]
    selected=[];family_counts={};seen=set()
    for rank in range(max(map(len,pools))):
        for pool in pools:
            if rank>=len(pool):continue
            i=int(pool[rank]);family=tuple(records[i].get('founder_groups',[]))
            if not family:
                weights=records[i].get('family_contributions',[])
                family=('dominant-family',int(np.argmax(weights))) if weights else ('unknown',ids[i])
            if i in seen or not eligible[i] or not active[i] or family_counts.get(family,0)>=4:continue
            selected.append(i);seen.add(i);family_counts[family]=family_counts.get(family,0)+1
            if len(selected)==16:break
        if len(selected)==16:break
    rows=[]
    for i in selected:
        order=np.argsort(net[:,i]);worst=order[:3];best=order[-3:][::-1]
        rows.append(dict(serial=ids[i],program_sha256=records[i]['program_sha256'],
            family=records[i].get('founder_groups',[]),nodes=records[i].get('node_count'),
            score=float(score[i]),net=float(net[:,i].sum()),without_best=float(net[:,i].sum()-net[:,i].max()),
            profitable_sessions=int((net[:,i]>0).sum()),active_sessions=int((net[:,i]!=0).sum()),
            trades=panel['closed_positions'][i],blocked=panel.get('blocked_requests',[None]*len(ids))[i],
            best=[dict(day=days[j],net=float(net[j,i])) for j in best],
            worst=[dict(day=days[j],net=float(net[j,i])) for j in worst]))
    packet=dict(scope='training',source_generation=generation,review_epoch=generation//10*10,
        training_sha256=job['training_sha256'],evidence=binding,candidates=rows,
        population=len(ids),sessions=len(days),profitable_candidates=int((net.sum(0)>0).sum()))
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    write_json(out/'packet.json',packet)
    write_json(out/'parents.json',[dict(serial=ids[i],payload=population['population'][i],record=records[i],
        sessions={d:{k:v[i] for k,v in sessions['sessions'][d].items() if isinstance(v,list) and len(v)==len(ids)} for d in days}) for i in selected])
    write_json(out/'package-template.json',dict(version=POLICY['version'],scope='training',
        review_epoch=packet['review_epoch'],source_generation=generation,training_sha256=job['training_sha256'],
        evidence=binding,rationale='',offspring=[]))
    return packet


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--job',required=True);p.add_argument('--generation',type=int,required=True);p.add_argument('--out',required=True)
    a=p.parse_args();job=json.loads(Path(a.job).read_bytes())
    result=export(Path(job['campaign'])/'phase-2/global',job,a.generation,a.out)
    print(json.dumps({k:result[k] for k in ('source_generation','population','sessions','profitable_candidates')}))
