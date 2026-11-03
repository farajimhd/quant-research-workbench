"""Reproduce the diverse sixfold parent shortlist into the first 1024 cohort."""
import numpy as np
from .full_common import aggregate
from .behavior_sketch import sketch
from .behavior_selection import session_keys

VERSION='sixfold-parent-selection-then-v64-reproduction-v1'


def subset(ordered,selected):
    selected=np.asarray(selected,dtype=np.int64);result=[]
    for m in ordered:
        n=len(m['net_pnl']);values,active=sketch(m,n)
        keys=m.get('selection_behavior_keys')
        if keys is None:keys=session_keys(m)
        panel=aggregate([m])
        result.append(dict(net_pnl=np.asarray(m['net_pnl'])[selected],
            closed_positions=np.asarray(m['closed_positions'])[selected],
            terminal_valid=np.asarray(m['terminal_valid'])[selected],
            ticker_score=[m['ticker_score'][i] for i in selected],
            selection_behavior_keys=[keys[i] for i in selected],
            _behavior_sketch=(values[selected],active[selected]),
            _session_panel={k:v[selected] for k,v in panel.items()},_summary_objective=(0.,0.)))
    return result


def reproduce(selected,members,ids,pedigree,ordered,rng,selection,species,probe):
    from .lineage import next_population
    parents=[members[i] for i in selected];parent_ids=ids[selected]
    metrics=subset(ordered,selected);panel=aggregate(metrics)
    successor,newids,report=next_population(rng,parents,parent_ids,pedigree,[],1,selection,
        session_values=panel['score'][None],session_eligible=panel['eligible'][None],
        global_values=panel['score'],global_eligible=panel['eligible'],target_size=1024,
        species=species,ordered=metrics,probe=probe)
    if len(successor)!=1024 or len(set(map(int,newids))-set(map(int,ids)))==0:
        raise ValueError('Initial reproduction must create a 1024-member bred population')
    report.update(initial_reproduction=VERSION,initial_parent_count=len(parents),
                  initial_new_candidates=len(set(map(int,newids))-set(map(int,ids))))
    return successor,newids,report
