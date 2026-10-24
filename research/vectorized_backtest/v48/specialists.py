"""Adaptive positive case ranks; bounded training-only historical parent archive."""
import numpy as np
from .genome import Individual
from .lineage import program_key


def rank_rounds(scores, eligible, ids, capacity, generation, keys=None):
    """One positive rank per case per round; canonical duplicates never consume slots."""
    scores=np.asarray(scores,float);eligible=np.asarray(eligible,bool);ids=np.asarray(ids)
    if scores.ndim!=2 or scores.shape[1]!=len(ids) or eligible.shape!=(len(ids),) or not np.isfinite(scores).all():
        raise ValueError('Aligned finite adaptive case panel required')
    if not 1<=capacity<=1024:raise ValueError('Bounded case parent capacity required')
    keys=list(map(int,ids)) if keys is None else list(keys)
    groups=[]
    for c,row in enumerate(scores):
        order=np.lexsort((ids,-row));seen=set();ranked=[]
        for i in order:
            if eligible[i] and row[i]>0 and keys[i] not in seen:
                ranked.append(int(i));seen.add(keys[i])
        if ranked:groups.append((c,ranked))
    if groups:
        offset=(generation-1)%len(groups);groups=groups[offset:]+groups[:offset]
    selected=[];selected_keys=set();representatives={};niches=[]
    for level in range(max((len(r) for _,r in groups),default=0)):
        for c,rows in groups:
            if level>=len(rows):continue
            i=rows[level]
            if keys[i] not in selected_keys:
                selected.append(i);selected_keys.add(keys[i]);representatives[keys[i]]=i
            niches.append(dict(case_index=c,candidate=representatives[keys[i]],rank=level+1))
            if len(selected)==capacity:return selected,niches
    return selected,niches


def case_groups(scores, eligible, ids, admitted, niches):
    """Every case uses the top-ranked admitted positive programs, without global-score feedback."""
    scores=np.asarray(scores,float);eligible=np.asarray(eligible,bool)
    result={}
    for c,row in enumerate(scores):
        indexes=sorted({r['candidate'] for r in niches if r['case_index']==c and r['candidate'] in admitted and eligible[r['candidate']] and row[r['candidate']]>0})
        if indexes:result[c]=np.asarray(sorted(indexes,key=lambda i:(-row[i],ids[i])),dtype=int)
    return result


def case_quotas(cases, slots, generation):
    """Equal offspring opportunities per occupied case; rotate integer remainders."""
    cases=sorted(cases)
    if not cases:return {}
    offset=(generation-1)%len(cases);cases=cases[offset:]+cases[:offset]
    base,remainder=divmod(slots,len(cases))
    return {c:base+int(i<remainder) for i,c in enumerate(cases)}


def update(previous, members, ids, pedigree, metrics, panel, generation, capacity=1024):
    candidates={r['serial']:r for r in previous}
    for i in np.flatnonzero(panel['eligible'][0]):
        if panel['specialist'][i]<=0:continue
        candidates[int(ids[i])]=dict(serial=int(ids[i]),payload=members[i].payload(),
            pedigree=dict(pedigree.records[int(ids[i])]),source_generation=candidates.get(int(ids[i]),{}).get('source_generation',generation),case_owner=members[i].case_owner,first_seen_generation=candidates.get(int(ids[i]),{}).get('first_seen_generation',generation),
            metrics={k:[v[i]] for k,v in metrics.items()},case_scores=panel['case_scores'][:,i].tolist())
    for row in candidates.values():
        if row['case_owner'] is None:raise ValueError('Only owned phase0 specialists can enter the historical archive')
    rows=list(candidates.values())
    if not rows:return [],[]
    ids2=np.asarray([r['serial'] for r in rows])
    scores=np.asarray([r['case_scores'] for r in rows],float).T
    keys=[program_key(Individual.restore(r['payload'])) for r in rows]
    selected,niches=rank_rounds(scores,np.ones(len(rows),bool),ids2,capacity,generation,keys)
    return [rows[i] for i in selected],[dict(case=panel['case_ids'][r['case_index']],serial=rows[r['candidate']]['serial'],rank=r['rank']) for r in niches]


def admit(members, ids, pedigree, metrics, archive):
    """Reuse exact same-session metrics for archived parents, never new children."""
    members = list(members); ids = list(map(int, ids)); known = set(ids)
    merged = {k: list(v) for k, v in metrics.items()}
    for row in archive:
        serial = row['serial']
        if serial in known:
            continue
        members.append(Individual.restore(row['payload'])); ids.append(serial); known.add(serial)
        pedigree.records[serial] = dict(row['pedigree'])
        if set(row['metrics']) != set(merged):
            raise ValueError('Archived training metric schema changed')
        for k in merged:
            merged[k].extend(row['metrics'][k])
    return members, np.asarray(ids, dtype=np.int64), merged
