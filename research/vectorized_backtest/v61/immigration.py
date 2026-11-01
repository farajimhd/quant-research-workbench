"""Deterministic, disjoint score/profit quotas for unchanged session experts."""

def select(ids,panel,hashes,capacity=128,policy='score-only'):
    if policy not in ('score-only','score75-net25-v1'):raise ValueError('Unknown immigration policy')
    valid=[i for i,s in enumerate(ids) if panel['eligible'][i] and panel['closed_positions'][i]>0]
    score=sorted(valid,key=lambda i:(-panel['score'][i],ids[i]))
    profit=sorted(valid,key=lambda i:(-panel['net_pnl'][i],ids[i]))
    ranks={kind:{i:n+1 for n,i in enumerate(order)} for kind,order in [('score',score),('net',profit)]}
    quotas=[('score',capacity,score)] if policy=='score-only' else [('score',capacity*3//4,score),('net-profit',capacity-capacity*3//4,profit)]
    seen=set();out=[]
    for kind,count,order in quotas:
        accepted=0
        for i in order:
            key=hashes[ids[i]]
            if key in seen:continue
            seen.add(key);accepted+=1
            out.append(dict(index=i,selection=kind,score_rank=ranks['score'][i],net_rank=ranks['net'][i],source_score=panel['score'][i],source_net=panel['net_pnl'][i]))
            if accepted==count:break
        if accepted<count and policy!='score-only':raise ValueError('Insufficient unique eligible immigration candidates')
    return out
