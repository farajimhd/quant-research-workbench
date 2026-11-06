"""Explicit price-domain/missing-input probes; never labels a review approved."""
import argparse
import numpy as np
from .price_program import Prices,REFERENCES,entry_guard
from .genome import Individual
from .strategy_mix import leaves
from .lineage import program_key
from .progress_sessions import read_verified
from .publication import write_json
from .distributed_assets import sha


def probe(prices,anchors):
    bps=np.array([0.,100.,499.9,500.,500.1,1000.])
    bid=np.repeat(np.asarray(anchors,float),len(bps));spread=np.tile(bps,len(anchors))
    if not np.isfinite(bid).all() or (bid<=0).any():raise ValueError('Finite positive training quote anchors required')
    ask=bid*(1+spread/10000.)
    values={r:(bid*.95 if '.low.' in r else ask*1.05 if '.high.' in r else bid).copy() for r in REFERENCES}
    values.update(bid=bid,ask=ask,close=(bid+ask)*.5,volatility=bid*.01)
    evaluated,guard=prices.evaluate(values)
    if guard[spread>500].any():raise ValueError('Mandatory spread guard bypass')
    rows={}
    for name,expression in vars(prices).items():
        value=np.asarray(expression.evaluate(values))
        if not np.isfinite(value).all():raise ValueError('Nonfinite output with finite price inputs')
        missing={}
        for key in sorted({expression.reference,expression.other,'volatility'}):
            changed=dict(values);changed[key]=np.full_like(bid,np.nan)
            result=np.asarray(expression.evaluate(changed))
            missing[key]=dict(finite=int(np.isfinite(result).sum()),nonfinite=int((~np.isfinite(result)).sum()))
        rows[name]=dict(cases=len(bid),finite=int(np.isfinite(value).sum()),positive=int((value>0).sum()),minimum=float(value.min()),maximum=float(value.max()),missing_input_results=missing)
    return dict(endpoints=rows,spread_bps=bps.tolist(),anchors=list(map(float,anchors)),guard_pass=int(guard.sum()),above_500_guard_pass=0,
        interpretation='Synthetic price and missing-input perturbations anchored to observed training quotes; not historical orders, execution replay, or profitability evidence.')


def run(plan_path,observations_path,out):
    plan=read_verified(plan_path);observations=read_verified(observations_path)
    if plan['scope']!='training' or plan['validation_used']:raise ValueError('Training plan required')
    quotes=[event[2] for leaf in observations.values() for trace in leaf['traces'] for event in trace['events'].values() if event[2] is not None and event[2]>0]
    if not quotes:raise ValueError('No observed training quote anchors')
    anchors=np.quantile(quotes,[.01,.5,.99]);rows={}
    for payload in plan['members']:
        for leaf in leaves(Individual.restore(payload)):
            key=program_key(leaf)
            if key not in rows:rows[key]=probe(Prices.restore(leaf.policy.price_program),anchors)
    result=dict(scope='training',validation_used=False,plan_sha256=sha(plan_path),observations_sha256=sha(observations_path),leaves=rows)
    write_json(out,result);return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--plan',required=True);p.add_argument('--observations',required=True);p.add_argument('--out',required=True);a=p.parse_args()
    result=run(a.plan,a.observations,a.out);print({'leaves':len(result['leaves'])})
