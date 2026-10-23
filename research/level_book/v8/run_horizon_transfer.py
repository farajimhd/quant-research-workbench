"""Test saved reaction scores against fixed-time price direction, without refitting.

Only canonical-derived cached trades/encounters are read. Horizons are 1/2/5/10
seconds, on the same five sessions. Generated evidence stays in the runtime root.
"""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import sys
sys.dont_write_bytecode = True
import argparse
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import json
import numpy as np
import polars as pl

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0,str(REPO))
from research.level_book.v8.run_score_ablation import load_verified, seed_history, sha, write_json
from research.level_book.v8.incremental_prediction import predict_online, metrics

HORIZONS = [1,2,5,10]
VARIANTS = ['current','baseline','bonus_half','bonus_zero','recency_h1',
            'recency_baseline_h1','recency_h5','recency_baseline_h5']


def label_horizon(source, encounters, seconds, close_us):
    """Last available eligible trade at deadline; inclusive timestamp/ordinal ties.

    Within a certified full session the last trade remains the known price until
    another arrives. A stale quote is not invented: trade staleness is reported.
    Deadlines at/after the regular-session close are unavailable, not unchanged.
    """
    ts = source['time_us'].to_numpy()
    prices = source['price_units'].to_numpy()
    touch = encounters['touch_event'].to_numpy()-1
    assert np.array_equal(ts[touch],encounters['time_us'].to_numpy())
    due = ts[touch]+seconds*1000000
    endpoint = np.searchsorted(ts,due,side='right')-1
    valid = due < close_us
    delta = prices[endpoint]-prices[touch]
    incoming = np.sign(prices[touch]-prices[touch-1]).astype(np.int8)
    assert (incoming != 0).all()
    assert (ts[endpoint] <= due).all()
    return encounters.select('period_id','touch_event','time_us','role').with_columns(
        pl.Series('deadline_us',due),pl.Series('available',valid),
        pl.Series('direction',np.sign(delta).astype(np.int8)),
        pl.Series('change_units',delta),pl.Series('incoming',incoming),
        pl.Series('endpoint_event',endpoint+1),
        pl.Series('stale_seconds',(due-ts[endpoint])/1e6),
        pl.Series('no_new_trade',endpoint<=touch))


def direction_counts(labels):
    counts = np.zeros((2,3),np.int64)  # role, down/unchanged/up
    rows = labels.filter(pl.col('available'))
    np.add.at(counts,(rows['role'].to_numpy(),rows['direction'].to_numpy()+1),1)
    return counts


def frequency_baselines(labels, seed):
    """Admit a horizon label only when its deadline is strictly before the touch.

    At a fixed horizon deadlines are ordered like touches. Prefix sums and
    searchsorted replace a Python event loop; equal-deadline labels wait.
    Probabilities condition on a price change; unchanged outcomes stay separate.
    """
    times = labels['time_us'].to_numpy()
    due = labels['deadline_us'].to_numpy()
    roles = labels['role'].to_numpy()
    directions = labels['direction'].to_numpy()
    valid = labels['available'].to_numpy()
    assert (np.diff(due) >= 0).all()
    known = np.searchsorted(due,times,side='left')
    counts = np.empty((len(labels),2,3),dtype=np.int64)
    for role in range(2):
        for outcome in range(3):
            increments = valid & (roles==role) & (directions==outcome-1)
            prefix = np.r_[0,np.cumsum(increments)]
            counts[:,role,outcome] = seed[role,outcome]+prefix[known]
    own = counts[np.arange(len(labels)),roles]
    total = counts.sum(axis=1)
    return ((own[:,2]+1)/(own[:,0]+own[:,2]+2),
            (total[:,2]+1)/(total[:,0]+total[:,2]+2),known)


def score(labels, probability_up):
    valid = labels['available'].to_numpy()
    direction = labels['direction'].to_numpy()
    changed = valid & (direction != 0)
    probability_up = np.asarray(probability_up)
    predicted = np.where(probability_up >= .5,1,-1)
    m = metrics(pl.DataFrame({'outcome':(direction[changed]>0).astype(int),
                             'p':probability_up[changed]}),'p')
    m.pop('model')
    m['up_rate_changed'] = m.pop('rejection_rate')
    m['changed_encounters'] = m.pop('encounters')
    m['accuracy_changed'] = m.pop('accuracy')
    return dict(**m,valid_encounters=int(valid.sum()),unchanged=int((valid & (direction==0)).sum()),
        accuracy_all_available=float((predicted[valid]==direction[valid]).mean()))


def self_check():
    source = pl.DataFrame({'time_us':[0,1000000,2000000,2000000,3000000],
                          'price_units':[99,100,101,102,103]})
    touch = pl.DataFrame({'period_id':[0],'touch_event':[2],'time_us':[1000000],'role':[1]})
    a = label_horizon(source,touch,1,4000000)
    assert a['endpoint_event'][0] == 4 and a['change_units'][0] == 2
    assert not label_horizon(source,touch,3,4000000)['available'][0]
    # A label maturing exactly at a new touch is still withheld.
    labels = pl.DataFrame({'time_us':[0,1000000,2000000,2000001],
        'deadline_us':[1000000,2000000,3000000,3000001],
        'role':[0,0,0,0],'direction':[1,-1,1,1],'available':[True]*4})
    p,_,known = frequency_baselines(labels,np.zeros((2,3),np.int64))
    assert known.tolist() == [0,0,1,2] and np.allclose(p,[.5,.5,2/3,.5])


def run(source, scores, output):
    self_check()
    config, cache, inputs = load_verified(source)
    old = json.loads((scores/'manifest.json').read_text())
    assert json.loads((scores/'status.json').read_text())['status']=='complete'
    assert old['input_hashes']==inputs
    assert sha(Path(__file__).with_name('run_score_ablation.py'))==old['implementation_sha256']
    days = config['days']; evaluation = old['evaluation_days']
    manifest = dict(source=str(source),saved_scores=str(scores),input_hashes=inputs,
        horizons=HORIZONS,evaluation_days=evaluation,histories=config['histories'],
        primary='history=1, bonus_half (selected from previous reaction experiment)',
        score_rule='p(up)=p(rejection) at support, 1-p(rejection) at resistance; cutoff 0.5; no refitting',
        endpoint='Last eligible trade at or before deadline; final ordinal when timestamps tie',
        missing='Deadline at or after regular-session close excluded',
        cohort='All 86,686 original touches, including four unresolved reaction labels',
        unchanged='Reported separately; binary metrics conditional on change, and accuracy including unchanged also reported',
        baseline='Past 1/5/20 sessions plus matured current-day fixed-horizon directions; uniform weights; Laplace smoothing',
        limitations='Five previously inspected sessions; overlapping encounters are correlated. Transferred scores are not calibrated horizon probabilities.',
        implementation_sha256=sha(Path(__file__)),
        git_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=REPO,text=True).strip())
    write_json(output/'manifest.json',manifest)
    labels, counts, coverage = {}, {}, []
    scan_checks = 0
    for day in days:
        frame = pl.read_parquet(source/'sessions'/day/'source.parquet')
        provenance = json.loads((source/'sessions'/day/'source_receipt.json').read_text())['provenance']
        close = int(datetime.fromisoformat(provenance['window_end']).timestamp()*1e6)
        for horizon in HORIZONS:
            target = label_horizon(frame,cache[day][0],horizon,close)
            labels[day,horizon] = target
            counts[day,horizon] = direction_counts(target)
            if day in evaluation:
                target.write_parquet(output/f'labels_{day}_{horizon}s.parquet')
                valid = target.filter(pl.col('available'))
                coverage.append(dict(day=day,horizon=horizon,total=target.height,available=valid.height,
                    unavailable=target.height-valid.height,unchanged=valid.filter(pl.col('direction')==0).height,
                    up=valid.filter(pl.col('direction')==1).height,down=valid.filter(pl.col('direction')==-1).height,
                    no_new_trade=int(valid['no_new_trade'].sum()),stale_over_1s=valid.filter(pl.col('stale_seconds')>1).height))
                # Independent as-of scan checks both endpoint price and last-ordinal tie handling.
                ts = frame['time_us'].to_numpy();price = frame['price_units'].to_numpy()
                for row in target.sample(n=min(25,target.height),seed=2026).iter_rows(named=True):
                    endpoint = np.flatnonzero(ts<=row['deadline_us'])[-1]
                    assert endpoint+1 == row['endpoint_event']
                    assert price[endpoint]-price[row['touch_event']-1] == row['change_units']
                    scan_checks += 1
        print('LABELED',day,flush=True)
    daily, pooled, score_hashes = [], {}, {}
    for day in evaluation:
        index = days.index(day)
        for history in config['histories']:
            path = scores/f'predictions_{day}_h{history}.parquet'
            saved = pl.read_parquet(path);score_hashes[path.name] = sha(path)
            identity = ['period_id','touch_event','time_us','role']
            assert saved.select(identity).equals(labels[day,1].select(identity))
            prior = days[index-history:index]
            assert len(prior)==history and all(d<day for d in prior)
            # Reproduce saved probabilities without changing or refitting their model.
            groups = np.asarray([cache[d][2] for d in prior])
            for half_life in [None,1,5]:
                seed,_ = seed_history(groups,half_life)
                reproduced,_ = predict_online(cache[day][0],0,cache[day][1]['events'],initial_groups=seed)
                columns = [('current','prediction'),('baseline','global_prediction'),('bonus_zero','group_prediction')] if half_life is None else [(f'recency_h{half_life}','prediction'),(f'recency_baseline_h{half_life}','global_prediction')]
                for dst,src in columns:
                    assert np.array_equal(saved[dst].to_numpy(),reproduced[src].to_numpy())
            assert np.array_equal(saved['bonus_half'].to_numpy(),(saved['bonus_zero']+.5*(saved['current']-saved['bonus_zero'])).to_numpy())
            role = saved['role'].to_numpy()
            for horizon in HORIZONS:
                target = labels[day,horizon]
                seed = sum((counts[d,horizon] for d in prior),start=np.zeros((2,3),np.int64))
                p_role,p_global,known = frequency_baselines(target,seed)
                # Independent scalar counting verifies the vectorized baseline and temporal cutoffs.
                for j in np.linspace(0,len(target)-1,20,dtype=int):
                    j = int(j)
                    observed = target.filter(pl.col('available') & (pl.col('deadline_us')<int(target['time_us'][j])))
                    manual = seed+direction_counts(observed)
                    r = int(role[j]);expected=(manual[r,2]+1)/(manual[r,0]+manual[r,2]+2)
                    assert p_role[j]==expected
                probabilities = {name:np.where(role==0,saved[name].to_numpy(),1-saved[name].to_numpy()) for name in VARIANTS}
                probabilities.update(direction_frequency_role=p_role,direction_frequency_global=p_global,
                                     incoming=(target['incoming'].to_numpy()>0).astype(float))
                wide = target.with_columns([pl.Series(name,p) for name,p in probabilities.items()])
                wide.write_parquet(output/f'predictions_{day}_h{history}_{horizon}s.parquet')
                for name,p in probabilities.items():
                    daily.append(dict(day=day,history_sessions=history,horizon=horizon,variant=name,**score(target,p)))
                pooled.setdefault((history,horizon),[]).append(wide)
            print(f'EVALUATED {day} history={history}',flush=True)
    summary = []
    daily_frame = pl.DataFrame(daily)
    daily_frame.write_parquet(output/'daily_metrics.parquet')
    for (history,horizon),frames in pooled.items():
        frame = pl.concat(frames)
        for variant in probabilities:
            subset = daily_frame.filter((pl.col('history_sessions')==history)&(pl.col('horizon')==horizon)&(pl.col('variant')==variant))
            summary.append(dict(history_sessions=history,horizon=horizon,variant=variant,
                **score(frame,frame[variant].to_numpy()),mean_daily_auc=float(subset['roc_auc'].mean())))
    summary_frame = pl.DataFrame(summary)
    summary_frame.write_parquet(output/'summary.parquet')
    write_json(output/'results.json',dict(summary=summary,daily=daily,coverage=coverage))
    write_json(output/'validation.json',dict(saved_score_sets_reproduced=15,
        independent_endpoint_scans=scan_checks,scalar_baseline_checks=1200,timestamp_ties_and_deadlines='passed',
        score_file_hashes=score_hashes,verified_source_sessions=25))
    write_json(output/'status.json',dict(status='complete'))
    print(summary_frame.filter((pl.col('history_sessions')==1)&pl.col('variant').is_in(['current','bonus_half','direction_frequency_role','direction_frequency_global','incoming'])).select('horizon','variant','changed_encounters','accuracy_changed','accuracy_all_available','mean_daily_auc','brier'),flush=True)
    print('RESULTS:',output/'results.json',flush=True)


def main():
    root = Path('D:/TradingML/runtimes')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,default=root/'level-book-v8/history-comparison/aapl-20260820-full')
    parser.add_argument('--scores',type=Path,default=root/'level-book-v8/score-ablation/20261023T150151337431Z')
    parser.add_argument('--run',type=Path,default=root/'level-book-v8/horizon-transfer'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    args = parser.parse_args()
    if not root.is_dir() or not args.run.resolve().is_relative_to(root.resolve()):
        raise ValueError('Required runtime root unavailable or output outside runtime root')
    args.run.mkdir(parents=True,exist_ok=False)
    try:
        run(args.source,args.scores,args.run)
    except Exception:
        write_json(args.run/'status.json',dict(status='failed'))
        raise


if __name__=='__main__':
    main()
