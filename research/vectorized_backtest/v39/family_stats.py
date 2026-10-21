"""Observer metadata only; bounded vectorized aggregation, never RNG input."""
from time import perf_counter
import numpy as np
from .templates import FAMILIES


def annotate(record,parents,*,family='random'):
    contributions=[0]*len(FAMILIES)
    if parents:
        for parent in parents:
            contributions=[a+b for a,b in zip(contributions,parent['family_contributions'])]
    else:contributions[FAMILIES.index(family)]=1
    record['family_contributions']=contributions
    record['template_ancestry']=sum(contributions[:-1]);record['random_ancestry']=contributions[-1]
    # Serial and existing mutation/crossover suffix preserve unique identity.
    label=record['label'].split('-')
    record['label']='-'.join([label[0],f"T{100*record['template_ancestry']/sum(contributions):04.1f}",f"R{100*record['random_ancestry']/sum(contributions):04.1f}",*label[1:]])


def seed(pedigree,ids,families):
    if len(ids)!=len(families):raise ValueError('Seed provenance alignment changed')
    for serial,family in zip(ids,families):
        record=pedigree.records[int(serial)]
        # Replace initial default random annotation, without touching genotype.
        parts=record['label'].split('-');record['label']='-'.join([parts[0],*parts[3:]])
        annotate(record,[],family=family)


def aggregate(records,metrics):
    started=perf_counter()
    matrix=np.asarray([r['family_contributions'] for r in records],dtype=np.float64)
    weights=matrix/matrix.sum(1,keepdims=True)
    pnl=np.asarray(metrics['net_pnl'],dtype=np.float64)
    trades=np.asarray(metrics['closed_positions'],dtype=np.float64)
    if len(pnl)!=len(records) or not np.isfinite(pnl).all():raise ValueError('Family metric alignment changed')
    counts=weights.sum(0);profits=weights.T@pnl
    profitable=weights.T@(pnl>0);active=weights.T@(trades>0)
    rows=[]
    for j,name in enumerate(FAMILIES):
        selected=weights[:,j]>0
        rows.append(dict(family=name,candidate_equivalents=float(counts[j]),
            candidates_with_ancestry=int(selected.sum()),total_profit=float(profits[j]),
            mean_profit=float(profits[j]/counts[j]) if counts[j] else None,
            profitable_percent=float(100*profitable[j]/counts[j]) if counts[j] else None,
            active_percent=float(100*active[j]/counts[j]) if counts[j] else None,
            best_profit=float(pnl[selected].max()) if selected.any() else None))
    return dict(rows=rows,total_candidates=len(records),total_profit=float(pnl.sum()),
        attribution='ancestry-share weighted; initial seeds have exactly one family',
        aggregation_seconds=perf_counter()-started)
