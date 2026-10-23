"""Separate recency and personal-bonus diagnostics on the fixed five sessions.

Reuses hash-verified canonical-derived encounters. No new source acquisition,
band discovery, notebook, or alteration to the original model implementation.
"""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import sys
sys.dont_write_bytecode = True
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import numpy as np
import polars as pl

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from research.level_book.v8.incremental_prediction import predict_online, metrics


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, data):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, indent=2), encoding='utf-8')
    temporary.replace(path)


def seed_history(groups, half_life=None):
    """Oldest first; immediately prior session has lag 1, current outcomes lag 0.

    Each prior session's counts AND duration sums get weight 2**(-lag/H).
    No intraday decay: new resolved outcomes enter with unit weight. Personal
    history still resets each session and receives no recency weighting.
    """
    lag = np.arange(len(groups), 0, -1, dtype=float)
    weights = np.ones(len(groups)) if half_life is None else 2.**(-lag/half_life)
    return np.einsum('i,ijkl->jkl', weights, np.asarray(groups)), weights


def bonus_score(shared, current, multiplier):
    """Scale only the existing personal increment, holding background fixed."""
    return shared + multiplier*(current-shared)


def load_verified(source):
    config = json.loads((source/'config.json').read_text())
    assert json.loads((source/'status.json').read_text())['status'] == 'complete'
    for name, expected in config['implementation'].items():
        assert sha(Path(__file__).with_name(name)) == expected, f'Changed source implementation: {name}'
    cache, receipts = {}, {}
    for day in config['days']:
        folder = source/'sessions'/day
        receipt = json.loads((folder/'replay_receipt.json').read_text())
        provenance = json.loads((folder/'source_receipt.json').read_text())
        assert provenance['ticker'] == config['ticker'] and provenance['day'] == day
        assert receipt['implementation'] == config['implementation']
        assert sha(folder/'source.parquet') == receipt['source_sha256'] == provenance['parquet_sha256']
        assert sha(folder/'encounters.parquet') == receipt['encounters_sha256']
        rows = pl.read_parquet(folder/'encounters.parquet')
        assert rows.height == receipt['encounters']
        # Independently reconstruct the seed from saved labels rather than trusting the receipt.
        groups = np.zeros((2,2,4))
        for (role, age, outcome), part in rows.filter(pl.col('outcome').is_not_null()).group_by('role','age_group','outcome'):
            k = 0 if outcome == 1 else 1
            groups[role,age,k] = part.height
            groups[role,age,k+2] = part['duration_seconds'].sum()
        assert np.allclose(groups, receipt['groups'])
        cache[day] = (rows, receipt, groups)
        receipts[day] = dict(source_sha256=receipt['source_sha256'], encounters_sha256=receipt['encounters_sha256'])
    return config, cache, receipts


def self_check():
    groups = np.zeros((2,2,2,4))
    groups[0,1,0] = [4,0,8,0]
    groups[1,1,0] = [0,2,0,6]
    seed, weights = seed_history(groups, 1)
    assert np.array_equal(weights, [.25,.5])
    assert np.array_equal(seed[1,0], [1,1,2,3])
    assert np.array_equal(seed_history(groups)[0], groups.sum(axis=0))
    assert bonus_score(.4,.8,0) == .4 and bonus_score(.4,.8,1) == .8
    assert np.isclose(bonus_score(.4,.8,.5),.6)


def run(source, output):
    self_check()
    config, cache, inputs = load_verified(source)
    days = config['days']
    evaluation_days = days[-config['evaluation_sessions']:]
    assert evaluation_days == ['2026-08-14','2026-08-17','2026-08-18','2026-08-19','2026-08-20']
    assert config['histories'] == [1,5,20] and config['distance_units'] == 200
    manifest = dict(source=str(source), output=str(output), evaluation_days=evaluation_days,
        histories=[1,5,20], half_lives_sessions=[1,5], bonus_multipliers=[.5,0],
        primary_history=20, input_hashes=inputs, implementation_sha256=sha(Path(__file__)),
        git_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=REPO,text=True).strip(),
        design='One-factor-at-a-time. Recency keeps full personal bonus. Bonus changes keep uniform history. No combined variants.',
        decay='Prior lag d has weight 2**(-d/H); current-session resolved outcomes have weight 1; no intraday decay.',
        bonus='p_shared + multiplier*(p_original-p_shared); p_shared excludes this role period as in the original model.',
        limitation='Previously inspected five-session evaluation; diagnostic reuse, not untouched validation.')
    write_json(output/'manifest.json', manifest)
    all_daily, pooled, validations = [], {}, []
    units = [(day,h) for day in evaluation_days for h in config['histories']]
    for unit, (day, history) in enumerate(units):
        print(f'ACTIVE {unit+1}/{len(units)} {day} history={history}; completed={unit} queued={len(units)-unit-1}',flush=True)
        write_json(output/'status.json',dict(status='running',active=[day,history],completed=unit,queued=len(units)-unit-1))
        index = days.index(day)
        prior = days[index-history:index]
        assert len(prior) == history and all(d < day for d in prior)
        groups = np.asarray([cache[d][2] for d in prior])
        table, receipt, current_groups = cache[day]
        reference = pl.read_parquet(source/f'predictions_{day}_h{history}.parquet')
        names = {}
        for half_life in [None,1,5]:
            seed, weights = seed_history(groups, half_life)
            predicted, model = predict_online(table, 0, receipt['events'], initial_groups=seed)
            assert np.allclose(model.groups, seed+current_groups)
            assert predicted.filter(pl.col('latest_training_event') >= pl.col('touch_event')).is_empty()
            identities = ['period_id','touch_event','resolve_event','outcome']
            assert predicted.select(identities).equals(reference.select(identities))
            cols = ['prediction','group_prediction','role_prediction','global_prediction','latest_training_event']
            if half_life is None:
                assert predicted.select(cols).equals(reference.select(cols)), 'Original prediction parity failed'
                names['current'] = predicted['prediction']
                names['baseline'] = predicted['global_prediction']
                names['bonus_half'] = bonus_score(predicted['group_prediction'],predicted['prediction'],.5)
                names['bonus_zero'] = predicted['group_prediction']
            else:
                names[f'recency_h{half_life}'] = predicted['prediction']
                names[f'recency_baseline_h{half_life}'] = predicted['global_prediction']
                # Remove future outcomes in a shorter prefix, then reproduce all saved earlier predictions.
                cutoff = 10000
                prefix = table.filter(pl.col('touch_event') <= cutoff).with_columns(*[
                    pl.when(pl.col('resolve_event') <= cutoff).then(pl.col(c)).otherwise(None).alias(c)
                    for c in ['resolve_event','outcome','duration_seconds']])
                p, _ = predict_online(prefix,0,cutoff,initial_groups=seed)
                assert p.select(cols).equals(predicted.filter(pl.col('touch_event') <= cutoff).select(cols))
            validations.append(dict(day=day,history=history,half_life=half_life,
                weighted_prior_counts=float(seed[:,:,:2].sum()),weights=weights.tolist(),
                prior_days=prior,predictions=predicted.height,checks='passed'))
        # Store only probabilities and unchanged identities; fractional recency counts
        # are recorded in validations, avoiding the original viewer's integer count casts.
        wide = table.with_columns([series.alias(name) for name,series in names.items()])
        wide.write_parquet(output/f'predictions_{day}_h{history}.parquet')
        resolved = wide.filter(pl.col('outcome').is_not_null())
        for variant in names:
            scored = metrics(resolved,variant)
            all_daily.append(dict(day=day,history_sessions=history,variant=variant,**scored,
                mean_prediction=float(resolved[variant].mean())))
        pooled.setdefault(history,[]).append(resolved)
        print(f'COMPLETED {day} history={history}; resolved={resolved.height:,}',flush=True)
    daily = pl.DataFrame(all_daily)
    daily.write_parquet(output/'daily_metrics.parquet')
    summaries, calibrations = [], []
    for history, frames in pooled.items():
        rows = pl.concat(frames)
        for variant in names:
            per_day = daily.filter((pl.col('history_sessions')==history)&(pl.col('variant')==variant))
            current = daily.filter((pl.col('history_sessions')==history)&(pl.col('variant')=='current')).select('day',pl.col('brier').alias('current_brier'))
            baseline = daily.filter((pl.col('history_sessions')==history)&(pl.col('variant')=='baseline')).select('day',pl.col('brier').alias('baseline_brier'))
            paired = per_day.join(current,on='day').join(baseline,on='day')
            summaries.append(dict(history_sessions=history,variant=variant,**metrics(rows,variant),
                mean_daily_auc=float(per_day['roc_auc'].mean()),mean_prediction=float(rows[variant].mean()),
                days_better_than_current=int((paired['brier']<paired['current_brier']).sum()),
                days_better_than_baseline=int((paired['brier']<paired['baseline_brier']).sum())))
            bins = rows.with_columns((pl.col(variant)*10).floor().clip(0,9).cast(pl.Int64).alias('bin')).group_by('bin').agg(
                pl.len().alias('count'),pl.col(variant).mean().alias('mean_prediction'),pl.col('outcome').mean().alias('observed_rejection')).with_columns(
                pl.lit(history).alias('history_sessions'),pl.lit(variant).alias('variant'))
            calibrations.append(bins)
    pl.concat(calibrations).write_parquet(output/'calibration.parquet')
    summary = pl.DataFrame(summaries)
    summary.write_parquet(output/'summary.parquet')
    write_json(output/'results.json',dict(summary=summaries,daily=all_daily,
        evaluation_encounters=86686,resolved=86682,pending=4,
        note='Same five previously inspected sessions. Mean daily AUC measures within-session ranking. Brier and accuracy pooled.'))
    assert all(s['encounters'] == 86682 for s in summaries)
    write_json(output/'validation.json',dict(variants=validations,unweighted_exact_parity_checks=15,
        weighted_prefix_checks=30,verified_source_sessions=25,toy_weight_and_bonus_checks='passed'))
    write_json(output/'status.json',dict(status='complete',completed=len(units),queued=0))
    print(summary.filter(pl.col('history_sessions')==20).select('variant','accuracy','mean_daily_auc','brier','mean_prediction','days_better_than_baseline'),flush=True)
    print('RESULTS:',output/'results.json',flush=True)


def main():
    runtime = Path('D:/TradingML/runtimes')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,default=runtime/'level-book-v8/history-comparison/aapl-20260820-full')
    parser.add_argument('--run',type=Path,default=runtime/'level-book-v8/score-ablation'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    args = parser.parse_args()
    if not runtime.is_dir() or not args.run.resolve().is_relative_to(runtime.resolve()):
        raise ValueError('Required runtime root unavailable or output outside runtime root')
    args.run.mkdir(parents=True,exist_ok=False)
    print('COMMAND:',sys.executable,'-B',__file__,'--source',args.source,'--run',args.run,flush=True)
    try:
        run(args.source,args.run)
    except Exception:
        write_json(args.run/'status.json',dict(status='failed'))
        raise


if __name__=='__main__':
    main()
