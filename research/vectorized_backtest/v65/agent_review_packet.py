"""Small training-only review packet; explicit export, never on the GPU lane."""
import argparse,json,os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
from pathlib import Path
import numpy as np
from .agent_intervention import policy,evidence
from .publication import write_json


def prior_reviews(root,generation):
    """First full training outcome for every previously admitted manual cohort."""
    rows=[];root=Path(root)
    for path in sorted((root/'agent-reviews/decisions').glob('generation-*.json')):
        target=int(path.stem.split('-')[-1])
        if target>generation:continue
        folder=root/path.stem;receipt=folder/'agent-intervention.json'
        if not (folder/'complete.json').exists() or not receipt.exists():continue
        decision=json.loads(path.read_bytes());package=decision['package']
        if package is None:continue
        seal=json.loads((folder/'complete.json').read_bytes())
        from .distributed_assets import sha
        if sha(receipt)!=seal['agent_intervention_sha256']:raise ValueError('Intervention receipt changed')
        admitted=json.loads(receipt.read_bytes())
        if admitted['decision_sha256']!=sha(path):raise ValueError('Intervention decision changed')
        evidence(root,target);source,_=evidence(root,package['source_generation'])
        current=json.loads((folder/'training.json').read_bytes());parent=json.loads((source/'training.json').read_bytes())
        ci={s:i for i,s in enumerate(current['ids'])};pi={s:i for i,s in enumerate(parent['ids'])}
        for serial,item in zip(admitted['new_ids'],package['offspring']):
            i,j=ci[serial],pi[item['parent']]
            values={k:dict(offspring=current['panel'][k][i],parent=parent['panel'][k][j])
                    for k in ('score','net_pnl','closed_positions','profitable_sessions','net_excluding_best_session')}
            rows.append(dict(review_epoch=package['review_epoch'],generation=target,serial=serial,
                parent=item['parent'],hypothesis=item['hypothesis'],training=values,
                beat_parent_score=values['score']['offspring']>values['score']['parent']))
    return rows


def select_candidates(ids,panel,sessions,records,target):
    """Training-only behavioral coverage, including evolved and losing parents."""
    from hashlib import sha256
    days=sorted(sessions)
    net=np.asarray([sessions[d]['net_pnl'] for d in days],dtype=float)
    eligible=np.asarray(panel['eligible'],bool)&(np.asarray(panel['closed_positions'])>=5)
    score=np.asarray(panel['score'],float);serials=np.asarray(ids)
    def order(values):return np.lexsort((serials,-np.asarray(values)))
    # Exact observed behavior includes activity; equal profit alone is insufficient.
    fields=('net_pnl','closed_positions','winning_positions','blocked_requests')
    behavior=np.stack([np.asarray([sessions[d][k] for d in days],float) for k in fields],axis=-1)
    labels=[sha256(np.round(behavior[:,i],6).tobytes()).hexdigest() for i in range(len(ids))]
    quarters=np.array_split(net,min(4,len(days)))
    pools=[order(score),order(net.sum(0)),order(net.sum(0)-net.max(0)),
           order((net>0).sum(0)),order(np.min([q.sum(0) for q in quarters],axis=0)),
           np.argmax(np.where(eligible[None],net,-np.inf),axis=1),
           np.argmax(np.where(eligible[None],np.asarray([sessions[d]['score'] for d in days]),-np.inf),axis=1)]
    sizes=np.asarray([r.get('node_count',1) for r in records])
    buckets=np.floor(np.log2(np.maximum(1,sizes))).astype(int)
    # One pool per complexity bucket prevents large champion relatives from
    # excluding small, active strategies that may have repairable weaknesses.
    pools.extend(order(score)[buckets[order(score)]==b] for b in np.unique(buckets[eligible]))
    selected=[];seen=set();seen_behavior=set()
    for rank in range(max(map(len,pools))):
        for pool in pools:
            if rank>=len(pool):continue
            i=int(pool[rank])
            if i in seen or not eligible[i] or labels[i] in seen_behavior:continue
            selected.append(i);seen.add(i);seen_behavior.add(labels[i])
            if len(selected)==target:break
        if len(selected)==target:break
    return selected,dict(requested=target,selected=len(selected),eligible_active=int(eligible.sum()),
        distinct_active_behaviors=len({labels[i] for i in np.flatnonzero(eligible)}),
        shortfall=target-len(selected),selection='score/net/robustness/breadth/session champions/complexity; observed-behavior deduplication',
        behavior_keys={str(ids[i]):labels[i] for i in selected})


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
    rules=policy(job)
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
    selection=None
    if rules.get('review_candidates'):
        selected,selection=select_candidates(ids,panel,sessions['sessions'],records,rules['review_candidates'])
    rows=[]
    for i in selected:
        order=np.argsort(net[:,i]);worst=order[:3];best=order[-3:][::-1]
        rows.append(dict(serial=ids[i],program_sha256=records[i]['program_sha256'],
            family=records[i].get('founder_groups',[]),nodes=records[i].get('node_count'),
            score=float(score[i]),net=float(net[:,i].sum()),without_best=float(net[:,i].sum()-net[:,i].max()),
            profitable_sessions=int((net[:,i]>0).sum()),active_sessions=int((net[:,i]!=0).sum()),
            trades=panel['closed_positions'][i],blocked=panel.get('blocked_requests',[None]*len(ids))[i],
            best=[dict(day=days[j],net=float(net[j,i])) for j in best],
            worst=[dict(day=days[j],net=float(net[j,i])) for j in worst],
            months={month:float(net[[d.startswith(month) for d in days],i].sum()) for month in sorted({d[:7] for d in days})},
            operation=records[i].get('operation'),parents=records[i].get('parents')))
    packet=dict(scope='training',source_generation=generation,review_epoch=generation//rules['interval']*rules['interval'],
        training_sha256=job['training_sha256'],evidence=binding,candidates=rows,
        population=len(ids),sessions=len(days),profitable_candidates=int((net.sum(0)>0).sum()))
    if selection is not None:packet['review_selection']=selection
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    write_json(out/'packet.json',packet)
    write_json(out/'prior-interventions.json',prior_reviews(root,generation))
    write_json(out/'parents.json',[dict(serial=ids[i],payload=population['population'][i],record=records[i],
        sessions={d:{k:v[i] for k,v in sessions['sessions'][d].items() if isinstance(v,list) and len(v)==len(ids)} for d in days}) for i in selected])
    write_json(out/'package-template.json',dict(version=rules['version'],scope='training',
        review_epoch=packet['review_epoch'],source_generation=generation,training_sha256=job['training_sha256'],
        evidence=binding,rationale='',offspring=[]))
    return packet


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--job',required=True);p.add_argument('--generation',type=int,required=True);p.add_argument('--out',required=True)
    a=p.parse_args();job=json.loads(Path(a.job).read_bytes())
    result=export(Path(job['campaign'])/'phase-2/global',job,a.generation,a.out)
    print(json.dumps({k:result[k] for k in ('source_generation','population','sessions','profitable_candidates')}))
