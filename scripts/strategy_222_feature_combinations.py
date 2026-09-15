"""Ticker-grouped development checks for combinations of causal entry features.

No model is exported or deployed. Static rejection of recorded trades is not a
counterfactual strategy replay. This selected same-session sample is not an
unseen holdout and cannot establish live profitability.
"""
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
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score, balanced_accuracy_score, brier_score_loss
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from strategy_222_supervised_research import digest,save


def families():
    indicator=[]; candles=[]
    for tf in ('1s','5s'):
        indicator.extend(f'completed_{tf}.{k}' for k in (
            'histogram_bps_per_atr_bps','histogram_slope_bps_per_second_per_atr_bps',
            'histogram_fraction_of_peak','observed_candles','pullback_pct','progress_pct'))
        candles.extend(f'candle_{tf}.{k}' for k in (
            'body_fraction','upper_wick_fraction','lower_wick_fraction','close_location',
            'higher_high','higher_low','lower_high','lower_low','inside_bar','outside_bar',
            'bullish_body_engulfing','bearish_body_engulfing','atr_pct',
            'price_vs_execution_vwap_pct_per_atr_pct','price_change_1_bar_pct_per_atr_pct'))
    levels=['price_to_hod_pct','price_to_resistance_pct','base_price_location','base_range_pct',
        'base_risk_pct','support_age_s','stop_clearance_spreads','level_state_count.warning',
        'level_state_count.failed','price_to_previous_stop_pct','price_to_previous_body_high_pct',
        'previous_stop_protected','since_previous_exit_s']
    return dict(indicators=indicator,price_action=candles,levels_and_risk=levels,
                combined=indicator+candles+levels)


def population(rows):
    selected=[]; excluded=Counter(); seen=set()
    for row in rows:
        if row['kind']!='actual_entry' or row['offset_s']!=0:
            continue
        if row['label'] not in ('winner','nonwinner'):
            excluded['open_outcome']+=1;continue
        if row['status']!='sampled':
            excluded[row['status']]+=1;continue
        if row['decision_at']>row['at'] or row['decision_sequence']>=row['sequence_limit']:
            raise ValueError('Entry features violate pre-fill clock/sequence')
        if row['group'] in seen:
            raise ValueError('Duplicate entry group')
        seen.add(row['group']);selected.append(row)
    if not selected:raise ValueError('No closed entry observations')
    return selected,dict(excluded)


def grouped_predictions(rows,names):
    # Identity, outcome, absolute timestamp and hindsight labels never enter X.
    x=np.array([[r['features'].get(k,np.nan) for k in names] for r in rows],dtype=float)
    if np.isinf(x).any():raise ValueError('Infinite feature')
    y=np.array([int(r['label']=='winner') for r in rows])
    groups=np.array([r['symbol'] for r in rows])
    scores=np.full(len(rows),np.nan);baseline=np.full(len(rows),np.nan);folds=[]
    for symbol in sorted(set(groups)):
        train=np.flatnonzero(groups!=symbol);test=np.flatnonzero(groups==symbol)
        if len(set(y[train]))<2:raise ValueError('Training fold lacks both outcomes')
        counts=Counter(groups[train]);weights=np.array([1/counts[s] for s in groups[train]])
        weights*=len(train)/weights.sum()
        model=Pipeline([('impute',SimpleImputer(strategy='median',add_indicator=True,keep_empty_features=True)),
            ('scale',StandardScaler()),('classifier',LogisticRegression(C=.1,max_iter=2000,random_state=0))])
        model.fit(x[train],y[train],classifier__sample_weight=weights)
        if max(model.named_steps['classifier'].n_iter_)>=2000:
            raise ValueError('Logistic model did not converge')
        scores[test]=model.predict_proba(x[test])[:,1]
        baseline[test]=np.average(y[train],weights=weights)
        folds.append(dict(held_out_symbol=str(symbol),train_groups=sorted(set(groups[train])),
                          train_count=len(train),test_count=len(test)))
    if not np.isfinite(scores).all():raise ValueError('Incomplete grouped predictions')
    return y,scores,baseline,folds


def measures(y,scores):
    passed=scores>=.5
    return dict(rank_auc=float(roc_auc_score(y,scores)),
        balanced_accuracy=float(balanced_accuracy_score(y,passed)),
        brier_score=float(brier_score_loss(y,scores)),
        kept_winners=int(sum(passed & (y==1))),rejected_winners=int(sum(~passed & (y==1))),
        kept_nonwinners=int(sum(passed & (y==0))),rejected_nonwinners=int(sum(~passed & (y==0))))


def run(source,output):
    output=output.resolve();output.relative_to(Path('D:/TradingML/runtimes').resolve())
    manifest=json.loads((source/'manifest.json').read_text())
    if manifest['status']!='completed':raise ValueError('Incomplete feature study')
    for name,sha in manifest['outputs'].items():
        if digest(source/name)!=sha:raise ValueError('Feature study changed')
    parents=[Path(k) for k in manifest['identity'] if Path(k).name=='comparison.json']
    if len(parents)!=1 or digest(parents[0])!=manifest['identity'][str(parents[0])]:
        raise ValueError('Missing or changed execution comparison authority')
    actual={f"{trial['run_id']}:{i}":episode for trial in json.loads(parents[0].read_text())
            for i,episode in enumerate(trial['episodes'])}
    inputs={str(p.resolve()):digest(p) for p in (source/'samples.json',source/'manifest.json',Path(__file__),Path(__file__).with_name('strategy_222_supervised_research.py'))}
    identity=dict(inputs=inputs,sklearn_version=sklearn.__version__,numpy_version=np.__version__)
    output.mkdir(parents=True,exist_ok=True)
    destination=output/'comparison.json'
    if destination.exists():
        old=json.loads(destination.read_text())
        if old['identity']!=identity:raise ValueError('Inputs changed; use a successor directory')
        if digest(output/'report.md')!=old['report_sha256']:raise ValueError('Completed report changed')
        print('Existing completed comparison retained.',flush=True);return
    rows,excluded=population(json.loads((source/'samples.json').read_text()))
    report=dict(identity=identity,method='Fixed regularized logistic models (C=0.1, threshold=0.5), with training-only median imputation, missingness flags and standardization. Every ticker is held out in turn; training tickers receive equal total weight. Four predeclared feature families, no threshold search. All observations are existing selected entries from one development session. Ticker holdout is a sensitivity check, not unseen/time-forward validation. Rejected-trade counts do not estimate counterfactual portfolio results. No model artifact or strategy update is produced.',
        population=len(rows),excluded=excluded,models=[])
    for name,names in families().items():
        y,scores,baseline,folds=grouped_predictions(rows,names)
        result=dict(family=name,features=names,metrics=measures(y,scores),
                    training_prevalence_baseline=measures(y,baseline),folds=folds,
                    predictions=[dict(group=r['group'],symbol=r['symbol'],entry_at=r['at'],
                        label=r['label'],score=float(score),keep=bool(score>=.5)) for r,score in zip(rows,scores)])
        result['large_recorded_winners_rejected']=[dict(group=p['group'],symbol=p['symbol'],
            entry=actual[p['group']]['opened_at'],recorded_net=actual[p['group']]['net'],score=p['score'])
            for p in result['predictions'] if not p['keep'] and actual[p['group']].get('net',0)>=200]
        report['models'].append(result)
        print(f"Completed={len(report['models'])}/4 active=0 queued={4-len(report['models'])} failed=0: {name} AUC={result['metrics']['rank_auc']:.3f}, rejected winners={result['metrics']['rejected_winners']}",flush=True)
    lines=['# Strategy 222 feature combinations','',report['method'],'',
        f"Closed entries: {len(rows)}. Exclusions: {excluded}.",'',
        '| Feature family | Grouped AUC | Balanced accuracy | Winners kept / rejected | Nonwinners kept / rejected |',
        '|---|---:|---:|---:|---:|']
    for r in report['models']:
        m=r['metrics'];lines.append(f"| {r['family']} | {m['rank_auc']:.3f} | {m['balanced_accuracy']:.3f} | {m['kept_winners']} / {m['rejected_winners']} | {m['kept_nonwinners']} / {m['rejected_nonwinners']} |")
    lines.extend(['','## Large recorded winners rejected','','Recorded episode profit is shown only to identify harmed winners. It is not a counterfactual portfolio calculation. The existing audit definition of a large winner is at least $200.',''])
    for r in report['models']:
        lines.append(f"### {r['family']}")
        lines.append('')
        for e in r['large_recorded_winners_rejected']:
            lines.append(f"- {e['symbol']} {e['entry']}: recorded net ${e['recorded_net']:.2f}, score {e['score']:.3f}.")
        lines.append('')
    (output/'report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    report['report_sha256']=digest(output/'report.md')
    save(destination,report)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();run(args.source,args.output)
