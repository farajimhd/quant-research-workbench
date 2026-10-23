"""Causal fixed-pair boundary races, one outstanding prediction per session.

No notebook, database writer or new source acquisition. Replays the existing
band engine for one training session plus the five fixed evaluation sessions.
"""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import sys
sys.dont_write_bytecode = True
import argparse
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import numpy as np
import polars as pl
from numba import njit

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0,str(REPO))
from research.level_book.v8.pressure_engine import run_fast
from research.level_book.v8.incremental_prediction import CountModel, metrics
from research.level_book.v8.run_score_ablation import load_verified, sha, write_json


@njit(cache=False)
def bit_add(tree, index, delta):
    index += 1
    while index < len(tree):
        tree[index] += delta
        index += index & -index


@njit(cache=False)
def bit_sum(tree, count):
    value = 0
    while count > 0:
        value += tree[count]
        count -= count & -count
    return value


@njit(cache=False)
def bit_kth(tree, k):
    index = 0
    step = 1
    while step*2 < len(tree):
        step *= 2
    while step:
        other = index+step
        if other < len(tree) and tree[other] < k:
            index = other
            k -= tree[other]
        step //= 2
    return index


@njit(cache=False)
def pair_kernel(prices, periods, ends, lower, upper):
    """O((events+role periods) log bands) causal pair selection.

    Fenwick trees index only currently active roles. Full-session coordinates
    merely compress prices: future bands have zero membership until activation.
    Output columns: start, end, upper-first, support_pid, resistance_pid,
    lower_break_price, upper_break_price, start_price, last_nonzero_direction.
    """
    order = np.argsort(lower)
    inverse = np.empty(len(order),np.int64)
    for i in range(len(order)):
        inverse[order[i]] = i
    lo = lower[order]; hi = upper[order]
    starts_order = np.argsort(periods[:,3])
    end_order = np.argsort(ends)
    trees = np.zeros((2,len(order)+1),np.int64)
    active_pid = np.full(len(order),-1,np.int64)
    result = np.zeros((len(prices),9),np.int64)
    n = 0; start_index = 0; end_index = 0; pending = False; direction = 0
    no_pair = 0; bracketed = 0; idle_no_pair = 0
    while end_index < len(end_order) and ends[end_order[end_index]] == 0:
        end_index += 1
    for i in range(len(prices)):
        event = i+1; price = prices[i]
        if i and prices[i] != prices[i-1]:
            direction = 1 if prices[i]>prices[i-1] else -1
        while end_index < len(end_order) and ends[end_order[end_index]] <= event:
            pid = end_order[end_index]; band = periods[pid,0]; r = inverse[band]
            bit_add(trees[periods[pid,1]],r,-1)
            active_pid[r] = -1
            end_index += 1
        while start_index < len(starts_order) and periods[starts_order[start_index],3] <= event:
            pid = starts_order[start_index]; band = periods[pid,0]; r = inverse[band]
            bit_add(trees[periods[pid,1]],r,1)
            active_pid[r] = pid
            start_index += 1
        if event < 200:
            continue
        if pending and (price < result[n-1,5] or price > result[n-1,6]):
            result[n-1,1] = event
            result[n-1,2] = 1 if price > result[n-1,6] else 0
            pending = False
        s_count = bit_sum(trees[0],np.searchsorted(hi,price,side='left'))
        r_before = bit_sum(trees[1],np.searchsorted(lo,price,side='right'))
        r_count = bit_sum(trees[1],len(order))
        valid = s_count > 0 and r_count > r_before
        if valid:
            bracketed += 1
        else:
            no_pair += 1
            if not pending:
                idle_no_pair += 1
        if not pending and valid:
            s = bit_kth(trees[0],s_count)
            r = bit_kth(trees[1],r_before+1)
            result[n,0] = event; result[n,1] = 0; result[n,2] = -1
            result[n,3] = active_pid[s]; result[n,4] = active_pid[r]
            result[n,5] = lo[s]; result[n,6] = hi[r]
            result[n,7] = price; result[n,8] = direction
            n += 1; pending = True
    return result[:n].copy(),np.array([bracketed,no_pair,idle_no_pair])


def make_features(pairs, periods, times, reaction_rows, seed):
    """Use only barrier reactions resolved strictly before the pair decision."""
    model = CountModel()
    model.groups = seed.copy(); model.total = seed.sum(axis=(0,1))
    completed = reaction_rows.filter(pl.col('resolve_event').is_not_null()).sort('resolve_event').to_dicts()
    next_outcome = 0; rows = []
    for race, row in enumerate(pairs):
        start,end,outcome,sp,rp,lower,upper,price,direction = map(int,row)
        while next_outcome < len(completed) and completed[next_outcome]['resolve_event'] < start:
            model.update(completed[next_outcome]); next_outcome += 1
        ages = [(times[start-1]-times[int(periods[p,3])-1])/1e6 for p in (sp,rp)]
        age_groups = [int(age>=60) for age in ages]
        reaction = []
        for pid,role,group in [(sp,0,age_groups[0]),(rp,1,age_groups[1])]:
            pred = model.predict(pid,role,group)
            reaction.append(pred['group_prediction']+.5*(pred['prediction']-pred['group_prediction']))
        position = (price-lower)/(upper-lower)
        assert 0 < position < 1
        position_bin = min(int(position*5),4)
        basic = position_bin*3+direction+1
        strength_bins = [min(int(p*3),2) for p in reaction]
        full = (((basic*2+age_groups[0])*2+age_groups[1])*3+strength_bins[0])*3+strength_bins[1]
        rows.append(dict(race_id=race,start_event=start,resolve_event=end or None,
            outcome=outcome if end else None,support_period=sp,resistance_period=rp,
            lower_units=lower,upper_units=upper,start_price_units=price,direction=direction,
            position=position,support_age=ages[0],resistance_age=ages[1],
            support_reaction=reaction[0],resistance_reaction=reaction[1],
            basic_cell=basic,full_cell=full,
            latest_reaction_event=completed[next_outcome-1]['resolve_event'] if next_outcome else 0,
            duration_seconds=(times[end-1]-times[start-1])/1e6 if end else None))
    return pl.DataFrame(rows)


def aggregates(rows):
    basic = np.zeros((15,2),np.int64); full = np.zeros((540,2),np.int64); total = np.zeros(2,np.int64)
    for row in rows.filter(pl.col('outcome').is_not_null()).iter_rows(named=True):
        y = row['outcome']; basic[row['basic_cell'],y]+=1; full[row['full_cell'],y]+=1; total[y]+=1
    return basic,full,total


def learn(rows, prior):
    basic,full,total = [a.copy() for a in prior]
    pending = None; output = []
    for row in rows.iter_rows(named=True):
        # Consecutive races may meet at one event. Save that next prediction
        # before admitting the just-resolved result, matching earlier experiments.
        if pending is not None and pending['resolve_event'] < row['start_event']:
            y=pending['outcome'];basic[pending['basic_cell'],y]+=1;full[pending['full_cell'],y]+=1;total[y]+=1
            pending = None
        b = basic[row['basic_cell']]; f = full[row['full_cell']]
        geometry = (b[1]+20*row['position'])/(b.sum()+20)
        prediction = (f[1]+20*geometry)/(f.sum()+20)
        output.append(dict(**row,model=prediction,geometry_model=geometry,
            distance_baseline=row['position'],frequency_baseline=(total[1]+1)/(total.sum()+2),
            direction_baseline=(row['direction']+1)/2,coin_baseline=.5))
        # A prior outcome withheld on a same-event start can be admitted after
        # this prediction, before the following episode is ever forecast.
        if pending is not None:
            y=pending['outcome'];basic[pending['basic_cell'],y]+=1;full[pending['full_cell'],y]+=1;total[y]+=1
        pending = row if row['resolve_event'] is not None else None
    return pl.DataFrame(output)


def validate_pairs(pairs,prices,periods,ends,lower,upper):
    count = 0
    for i in np.linspace(0,len(pairs)-1,min(200,len(pairs)),dtype=int):
        start,end,outcome,sp,rp,lo,hi,price,_ = map(int,pairs[i])
        future = prices[start:]
        hit = np.flatnonzero((future<lo)|(future>hi))
        assert (start+int(hit[0])+1 if len(hit) else 0) == end
        if len(hit):assert outcome == int(future[hit[0]]>hi)
        active = (periods[:,3]<=start)&((ends==0)|(ends>start))
        support = np.flatnonzero(active&(periods[:,1]==0)&(upper[periods[:,0]]<price))
        resistance = np.flatnonzero(active&(periods[:,1]==1)&(lower[periods[:,0]]>price))
        assert sp == support[np.argmax(upper[periods[support,0]])]
        assert rp == resistance[np.argmin(lower[periods[resistance,0]])]
        count += 1
    # Fixed-boundary sampling must survive a price/timeline prefix unchanged.
    cutoff = min(10000,len(prices))
    mask = periods[:,3]<=cutoff
    short_periods = periods[mask];short_ends = ends[mask].copy();short_ends[short_ends>cutoff]=0
    short,_ = pair_kernel(prices[:cutoff],short_periods,short_ends,lower,upper)
    expected = pairs[pairs[:,0]<=cutoff].copy()
    unresolved = expected[:,1]>cutoff;expected[unresolved,1]=0;expected[unresolved,2]=-1
    assert np.array_equal(short,expected)
    return count


def validate_predictions(rows, prior, predictions):
    """Independent prefix aggregation checks strict admission and probabilities."""
    for i in np.linspace(0,rows.height-1,min(100,rows.height),dtype=int):
        row = rows.row(int(i),named=True)
        admitted = rows.head(int(i)).filter(pl.col('resolve_event') < row['start_event'])
        added = aggregates(admitted)
        basic,full,total = [a+b for a,b in zip(prior,added)]
        b=basic[row['basic_cell']];f=full[row['full_cell']]
        geometry=(b[1]+20*row['position'])/(b.sum()+20)
        expected=(f[1]+20*geometry)/(f.sum()+20)
        actual=predictions.row(int(i),named=True)
        assert abs(actual['model']-expected)<1e-12
        assert abs(actual['frequency_baseline']-(total[1]+1)/(total.sum()+2))<1e-12
    assert predictions.filter((pl.col('model')<0)|(pl.col('model')>1)).is_empty()
    return min(100,rows.height)


def race_metrics(rows, column):
    result=metrics(rows,column)
    result['upper_first_rate']=result.pop('rejection_rate')
    return result


def run(source,output):
    config,cache,inputs = load_verified(source)
    days = config['days'];evaluation=days[-5:];chosen=days[-6:]
    signature={name:sha(Path(__file__).with_name(name)) for name in
        ['run_boundary_race.py','pressure_engine.py','incremental_prediction.py']}
    write_json(output/'manifest.json',dict(source=str(source),inputs=inputs,implementation=signature,
        evaluation_days=evaluation,training_history_sessions=1,pair_policy='One pending race; nearest active fully-below support and fully-above resistance; fixed outer band boundaries; strict break',
        model='15 geometry/direction cells; 540 cells adding two age groups and two reaction-score fixed thirds. Hierarchical shrinkage weight 20; previous session plus resolved current races.',
        boundary_reactions='Existing half-bonus reaction model, previous-session shared history, current resolved outcomes only',
        cohort='New non-overlapping pair episodes on the same sessions; no fixed time limit; pending close unresolved',
        git_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=REPO,text=True).strip()))
    tables={};coverage=[];validations=[]
    for index,day in enumerate(chosen):
        print(f'ACTIVE {index+1}/6 {day}',flush=True)
        folder=output/day;folder.mkdir(exist_ok=True)
        receipt_path=folder/'receipt.json'
        if receipt_path.exists():
            import json
            receipt=json.loads(receipt_path.read_text())
            assert receipt['implementation']==signature and sha(folder/'races.parquet')==receipt['races_sha256']
            tables[day]=pl.read_parquet(folder/'races.parquet')
            coverage.append(receipt['coverage']);validations.append(receipt['validation'])
            print('REUSED',day,flush=True);continue
        import json,time
        started=time.perf_counter()
        frame=pl.read_parquet(source/'sessions'/day/'source.parquet')
        provenance=json.loads((source/'sessions'/day/'source_receipt.json').read_text())['provenance']
        opening=int(datetime.fromisoformat(provenance['window_start']).timestamp()*1e6)
        replay=run_fast(frame,opening)
        assert len(replay.lower)==cache[day][1]['bands'] and len(replay.periods)==cache[day][1]['role_periods']
        lower=replay.values[replay.lower];upper=replay.values[replay.upper]
        prices=frame['price_units'].to_numpy()
        pairs,counts=pair_kernel(prices,replay.periods,replay.ends,lower,upper)
        checked=validate_pairs(pairs,prices,replay.periods,replay.ends,lower,upper)
        prior_day=days[days.index(day)-1]
        races=make_features(pairs,replay.periods,replay.times,cache[day][0],cache[prior_day][2])
        assert races.filter(pl.col('latest_reaction_event')>=pl.col('start_event')).is_empty()
        races.write_parquet(folder/'races.parquet')
        np.savez_compressed(folder/'timeline.npz',periods=replay.periods,ends=replay.ends,lower=lower,upper=upper)
        record=dict(day=day,events=frame.height,races=races.height,resolved=races['outcome'].count(),
            pending=races['outcome'].null_count(),bracketed_events=int(counts[0]),no_pair_events=int(counts[1]),
            idle_no_pair_events=int(counts[2]),seconds=time.perf_counter()-started)
        validation=dict(day=day,independent_first_break_and_nearest_checks=checked,prefix='passed')
        receipt=dict(implementation=signature,races_sha256=sha(folder/'races.parquet'),coverage=record,validation=validation)
        write_json(receipt_path,receipt)
        tables[day]=races;coverage.append(record);validations.append(validation)
        print('COMPLETED',record,flush=True)
    daily=[];pooled=[]
    variants=['model','geometry_model','distance_baseline','frequency_baseline','direction_baseline','coin_baseline']
    for day in evaluation:
        previous=chosen[chosen.index(day)-1]
        predictions=learn(tables[day],aggregates(tables[previous]))
        checks=validate_predictions(tables[day],aggregates(tables[previous]),predictions)
        validations.append(dict(day=day,independent_prediction_checks=checks))
        predictions.write_parquet(output/f'predictions_{day}.parquet')
        resolved=predictions.filter(pl.col('outcome').is_not_null())
        for variant in variants:daily.append(dict(day=day,variant=variant,**race_metrics(resolved,variant)))
        pooled.append(resolved)
    all_rows=pl.concat(pooled);daily_frame=pl.DataFrame(daily)
    summary=[dict(variant=v,**race_metrics(all_rows,v),mean_daily_auc=float(daily_frame.filter(pl.col('variant')==v)['roc_auc'].mean())) for v in variants]
    daily_frame.write_parquet(output/'daily_metrics.parquet');pl.DataFrame(summary).write_parquet(output/'summary.parquet')
    write_json(output/'results.json',dict(summary=summary,daily=daily,coverage=coverage,
        outcome='1 = resistance upper boundary broke first; 0 = support lower boundary broke first'))
    write_json(output/'validation.json',validations)
    write_json(output/'status.json',dict(status='complete'))
    print(summary,flush=True);print('RESULTS',output/'results.json',flush=True)


def main():
    root=Path('D:/TradingML/runtimes')
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,default=root/'level-book-v8/history-comparison/aapl-20260820-full')
    p.add_argument('--run',type=Path,default=root/'level-book-v8/boundary-race/aapl-fixed-pairs-v2')
    a=p.parse_args()
    if not root.is_dir() or not a.run.resolve().is_relative_to(root.resolve()):raise ValueError('Invalid runtime root')
    a.run.mkdir(parents=True,exist_ok=True)
    try:run(a.source,a.run)
    except Exception:
        write_json(a.run/'status.json',dict(status='failed'));raise


if __name__=='__main__':main()
