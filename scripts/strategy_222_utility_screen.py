"""Compare binary and cost-aware supervision; no model export or strategy orders."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import sys
sys.dont_write_bytecode=True
import argparse
from collections import Counter
import json
from pathlib import Path
import numpy as np
import sklearn
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from threadpoolctl import threadpool_limits
from strategy_222_supervised_research import digest,save
from strategy_222_feature_combinations import families


def feature_names():
    names=families()['combined']
    for role in ('support','resistance','transition'):
        names.extend(f'levels.{role}.{suffix}' for suffix in (
            'above.lower_distance_atr','below.upper_distance_atr','containing_count',
            'containing.price_location_in_band','above.available','below.available'))
    return names


def predictions(rows,labels,names,objective):
    if objective not in ('binary','cost_weighted','expected_return'):raise ValueError('Unknown objective')
    if len(rows)!=len(labels) or any(r['id']!=l['id'] for r,l in zip(rows,labels)):
        raise ValueError('Feature/label identity mismatch')
    x=np.array([[r['features'].get(n,np.nan) for n in names] for r in rows],dtype=float)
    if np.isinf(x).any():raise ValueError('Infinite feature')
    if any(not np.isfinite([l['net'],l['entry_ask']]).all() or l['entry_ask']<=0 for l in labels):
        raise ValueError('Invalid outcome utility')
    utility=np.array([l['net']/(100*l['entry_ask']) for l in labels])
    groups=np.array([r['symbol'] for r in rows]);scores=np.full(len(rows),np.nan);folds=[]
    settings=dict(max_leaf_nodes=7,max_iter=100,learning_rate=.05,min_samples_leaf=30,
                  l2_regularization=10,early_stopping=False,random_state=0)
    with threadpool_limits(limits=1):
        for symbol in sorted(set(groups)):
            train=np.flatnonzero(groups!=symbol);test=np.flatnonzero(groups==symbol)
            if len(set(utility[train]>0))!=2:raise ValueError('Training fold lacks both outcomes')
            counts=Counter(groups[train]);weights=np.array([1/counts[s] for s in groups[train]])
            if objective=='cost_weighted':weights*=np.abs(utility[train])
            weights*=len(train)/weights.sum()
            if objective=='expected_return':
                model=HistGradientBoostingRegressor(**settings).fit(x[train],utility[train],sample_weight=weights)
                scores[test]=model.predict(x[test])
            else:
                model=HistGradientBoostingClassifier(**settings).fit(x[train],utility[train]>0,sample_weight=weights)
                scores[test]=model.predict_proba(x[test])[:,1]-.5
            folds.append(dict(held_out_symbol=str(symbol),train_symbols=sorted(set(groups[train])),
                train_count=len(train),test_count=len(test)))
    if not np.isfinite(scores).all():raise ValueError('Incomplete predictions')
    return scores,folds


def run(source,output):
    output=output.resolve();output.relative_to(Path('D:/TradingML/runtimes').resolve())
    manifest=json.loads((source/'manifest.json').read_text())
    if manifest['status']!='completed':raise ValueError('Incomplete source')
    for name in ('features.json','labels.json'):
        if digest(source/name)!=manifest['outputs'][name]:raise ValueError('Source changed')
    if manifest['label_policy']['quantity']!=100:raise ValueError('Unsupported label quantity')
    rows=json.loads((source/'features.json').read_text());labels=json.loads((source/'labels.json').read_text())
    if len(rows)!=len(labels) or len({r['id'] for r in rows})!=len(rows):raise ValueError('Invalid population')
    if any(r['id']!=l['id'] for r,l in zip(rows,labels)):raise ValueError('Label join mismatch')
    excluded=Counter(l.get('reason','invalid') for l in labels if not l['valid'])
    pairs=[(r,l) for r,l in zip(rows,labels) if l['valid']];rows,labels=map(list,zip(*pairs))
    names=feature_names()
    unavailable=[n for n in names if not any(n in r['features'] for r in rows)]
    if all(n in unavailable for n in names if n.startswith('levels.')):
        raise ValueError('Level feature family unavailable')
    identity={str(p):digest(p) for p in (source/'manifest.json',source/'features.json',source/'labels.json',Path(__file__),Path(__file__).with_name('strategy_222_feature_combinations.py'))}
    identity.update(sklearn=sklearn.__version__,numpy=np.__version__)
    output.mkdir(parents=True,exist_ok=True);destination=output/'comparison.json'
    if destination.exists():
        old=json.loads(destination.read_text())
        if old['identity']!=identity:raise ValueError('Use successor output directory')
        print('Verified existing comparison retained.',flush=True);return
    report=dict(identity=identity,features=names,unavailable_features=unavailable,population=len(rows),excluded=dict(excluded),results=[],
        method='Fixed seven-leaf, 100-iteration histogram boosting; learning rate .05, L2=10, leaf minimum30, seed0. Whole-ticker exclusion, base training weights equal per ticker. Binary objective uses profitable/not-profitable; cost-weighted classification multiplies base weights by absolute after-cost return; regression predicts after-cost return. Return is label net divided by 100 shares times entry ask. Decision threshold zero for expected return, .5 for classification. No threshold or hyperparameter search. Native missing values. No model export.',
        limitations='Selected one-session research decisions, not independent trades or portfolio profit. Hypothetical prior-low stop/2R labels, 100ms latency, 5bps each side and fees. Repeated overlapping labels are dependent. Opportunity coverage is label-defined, not execution or original-position coverage; no unseen robustness proof.')
    opportunities={l['opportunity_id'] for l in labels if l.get('major_good')}
    for objective in ('binary','cost_weighted','expected_return'):
        scores,folds=predictions(rows,labels,names,objective);keep=scores>=0
        covered={l['opportunity_id'] for l,k in zip(labels,keep) if k and l.get('major_good')}
        result=dict(objective=objective,kept=int(sum(keep)),kept_profitable=sum(bool(k and l['profitable']) for l,k in zip(labels,keep)),
            kept_nonprofitable=sum(bool(k and not l['profitable']) for l,k in zip(labels,keep)),major_covered=len(covered),major_total=len(opportunities),
            missed_major=sorted(opportunities-covered),folds=folds,predictions=[dict(id=r['id'],symbol=r['symbol'],at=r['at'],score=float(s),keep=bool(k)) for r,s,k in zip(rows,scores,keep)])
        report['results'].append(result)
        print(f"Completed={len(report['results'])}/3 active=0 queued={3-len(report['results'])} failed=0 {objective}: kept={result['kept']}, profitable={result['kept_profitable']}, major={len(covered)}/{len(opportunities)}",flush=True)
    save(destination,report)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();run(args.source,args.output)
