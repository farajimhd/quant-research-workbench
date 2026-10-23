"""Evaluate frozen top-N nonredundant level selections on the fixed sessions."""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import sys
sys.dont_write_bytecode = True
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import time
import numpy as np
import polars as pl
from numba import njit

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from research.level_book.v8.pressure_engine import run_fast
from research.level_book.v8.incremental_prediction import CountModel
from research.level_book.v8.run_score_ablation import load_verified, sha, write_json

DISTANCE = 200  # Existing $0.02 rejection definition, also minimum same-side gap.
METHODS = ['model', 'nearest', 'swing', 'count_volume']
SIZES = [2, 6, 10]  # Total slots; equally divided between support and resistance.
KINDS = ['untouched', 'gap_before_touch', 'rejection', 'break', 'unresolved_touch']


@njit(cache=False)
def confirmed_swings(prices, distance):
    """Directional-change extrema; the reversal event is availability time.

    Output: confirmation event, pivot event, pivot price, role. Equal prices
    retain the first extreme. No centered window or future confirmation leaks.
    """
    output = np.zeros((len(prices), 4), np.int64)
    count = 0; direction = 0; low = prices[0]; high = prices[0]
    low_at = 0; high_at = 0
    for i in range(1, len(prices)):
        price = prices[i]
        if direction == 0:
            if price < low: low = price; low_at = i
            if price > high: high = price; high_at = i
            if price-low >= distance:
                output[count] = (i+1, low_at+1, low, 0); count += 1
                direction = 1; high = price; high_at = i
            elif high-price >= distance:
                output[count] = (i+1, high_at+1, high, 1); count += 1
                direction = -1; low = price; low_at = i
        elif direction == 1:
            if price > high: high = price; high_at = i
            if high-price >= distance:
                output[count] = (i+1, high_at+1, high, 1); count += 1
                direction = -1; low = price; low_at = i
        else:
            if price < low: low = price; low_at = i
            if price-low >= distance:
                output[count] = (i+1, low_at+1, low, 0); count += 1
                direction = 1; high = price; high_at = i
    return output[:count]


@njit(cache=False)
def label_selection(prices, event, window_end, lo, hi, role, distance):
    """First fresh touch during the next minute; freeze until reaction resolves.

    event/window_end are prefix lengths, and returned trade IDs are one-based.
    Gaps before a touch are explicit failures of reachability, not rejections.
    """
    touch = -1
    for i in range(event, window_end):
        broken = prices[i] < lo if role == 0 else prices[i] > hi
        if broken:
            return 1, 0, i+1
        approach = prices[i-1] > hi if role == 0 else prices[i-1] < lo
        if approach and lo <= prices[i] <= hi:
            touch = i; break
    if touch < 0:
        return 0, 0, 0
    for i in range(touch+1, len(prices)):
        broken = prices[i] < lo if role == 0 else prices[i] > hi
        rejected = prices[i] >= hi+distance if role == 0 else prices[i] <= lo-distance
        if broken: return 3, touch+1, i+1
        if rejected: return 2, touch+1, i+1
    return 4, touch+1, 0


def candidates(replay, reactions, seed, snapshot_times):
    """One pass through known trades, confirmed swings and resolved outcomes."""
    prices = replay.frame['price_units'].to_numpy()
    times = replay.times; volume = replay.volumes
    lower = replay.values[replay.lower]; upper = replay.values[replay.upper]
    order = np.argsort(lower)
    assert np.all(upper[order][:-1] < lower[order][1:])
    swings = confirmed_swings(prices, DISTANCE)
    swing_pointer = 0; latest_swing = np.zeros((len(lower), 2), np.int64)
    completed = reactions.filter(pl.col('resolve_event').is_not_null()).sort('resolve_event').to_dicts()
    outcome_pointer = 0
    model = CountModel(); model.groups = seed.copy(); model.total = seed.sum(axis=(0, 1))
    trade_counts = np.zeros(len(replay.values), np.int64)
    trade_volume = np.zeros(len(replay.values))
    previous = 0; rows = []; snapshots = []
    for number, timestamp in enumerate(snapshot_times):
        event = int(np.searchsorted(times, timestamp, side='right'))
        end = int(np.searchsorted(times, timestamp+60_000_000, side='left'))
        if event < 200: continue
        snapshots.append(dict(snapshot=number, time_us=int(timestamp), event=event, window_end=end))
        while outcome_pointer < len(completed) and completed[outcome_pointer]['resolve_event'] < event:
            model.update(completed[outcome_pointer]); outcome_pointer += 1
        while swing_pointer < len(swings) and swings[swing_pointer, 0] <= event:
            confirmed, pivot, price, role = swings[swing_pointer]
            at = int(np.searchsorted(lower[order], price, side='right'))-1
            if at >= 0 and price <= upper[order[at]]:
                latest_swing[order[at], role] = confirmed
            swing_pointer += 1
        trade_counts += np.bincount(replay.rank[previous:event], minlength=len(trade_counts))
        trade_volume += np.bincount(replay.rank[previous:event], weights=volume[previous:event], minlength=len(trade_counts))
        previous = event
        count_prefix = np.r_[0, np.cumsum(trade_counts)]
        volume_prefix = np.r_[0., np.cumsum(trade_volume)]
        active = (replay.periods[:, 3] <= event) & ((replay.ends == 0) | (replay.ends > event))
        price = prices[event-1]
        batch = []
        for pid in np.flatnonzero(active):
            band, role, _, start, _ = map(int, replay.periods[pid])
            if not (upper[band] < price if role == 0 else lower[band] > price): continue
            age = (timestamp-times[start-1])/1e6
            pred = model.predict(int(pid), role, int(age >= 60))
            probability = pred['group_prediction']+.5*(pred['prediction']-pred['group_prediction'])
            batch.append(dict(snapshot=number, time_us=int(timestamp), event=event, window_end=end,
                period_id=int(pid), band_id=band, role=role, lower_units=int(lower[band]), upper_units=int(upper[band]),
                age_seconds=age, probability=probability,
                distance_units=int(price-upper[band] if role == 0 else lower[band]-price),
                trade_count=int(count_prefix[replay.upper[band]+1]-count_prefix[replay.lower[band]]),
                traded_volume=float(volume_prefix[replay.upper[band]+1]-volume_prefix[replay.lower[band]]),
                swing_confirm_event=int(latest_swing[band, role]),
                latest_training_event=int(completed[outcome_pointer-1]['resolve_event']) if outcome_pointer else 0))
        for role in (0, 1):
            part = [row for row in batch if row['role'] == role]
            max_count = max([row['trade_count'] for row in part]+[1])
            max_volume = max([row['traded_volume'] for row in part]+[1.])
            for row in part:
                row['activity_score'] = .5*(row['trade_count']/max_count+row['traded_volume']/max_volume)
        rows.extend(batch)
    return pl.DataFrame(rows), pl.DataFrame(snapshots)


def select_levels(rows, method, limit):
    """Greedy score order, suppressing same-side gaps smaller than $0.02."""
    eligible = [r for r in rows if method != 'swing' or r['swing_confirm_event'] > 0]
    def key(r):
        value = {'model': r['probability'], 'nearest': -r['distance_units'],
                 'swing': r['swing_confirm_event'], 'count_volume': r['activity_score']}[method]
        return -value, r['distance_units'], r['band_id']
    kept = []
    for row in sorted(eligible, key=key):
        if all(max(row['lower_units']-k['upper_units'], k['lower_units']-row['upper_units']) >= DISTANCE for k in kept):
            kept.append(row)
            if len(kept) == limit: break
    return kept


def evaluate(day, frame, table, snapshots):
    prices = frame['price_units'].to_numpy(); times = frame['time_us'].to_numpy()
    groups = table.partition_by('snapshot', 'role', as_dict=True)
    rows = []; counts = []
    for snap in snapshots.iter_rows(named=True):
        for role in (0, 1):
            part = groups.get((snap['snapshot'], role))
            available = part.to_dicts() if part is not None else []
            labels = {}
            for method in METHODS:
                selected = select_levels(available, method, max(SIZES)//2)
                for n in SIZES:
                    chosen = selected[:n//2]
                    counts.append(dict(day=day, snapshot=snap['snapshot'], role=role, method=method,
                                       n=n, available=len(available), selected=len(chosen), requested=n//2))
                    for rank, row in enumerate(chosen, 1):
                        pid = row['period_id']
                        if pid not in labels:
                            labels[pid] = label_selection(prices, row['event'], row['window_end'],
                                row['lower_units'], row['upper_units'], role, DISTANCE)
                        kind, touch, resolved = labels[pid]
                        rows.append(dict(day=day, method=method, n=n, rank=rank, **row,
                            status=KINDS[kind], touch_event=int(touch), resolve_event=int(resolved),
                            reaction_seconds=float((times[resolved-1]-times[touch-1])/1e6) if touch and resolved else None))
    return pl.DataFrame(rows), pl.DataFrame(counts)


def validate(replay, table, selected, reactions, seed, snapshots):
    prices = replay.frame['price_units'].to_numpy()
    checked = 0
    for i in np.linspace(0, selected.height-1, min(250, selected.height), dtype=int):
        r = selected.row(int(i), named=True); event=r['event']; end=r['window_end']; role=r['role']
        lo=r['lower_units']; hi=r['upper_units']
        ix=np.arange(event, end)
        touched=(prices[ix]>=lo)&(prices[ix]<=hi)&((prices[ix-1]>hi) if role==0 else (prices[ix-1]<lo))
        broken=(prices[ix]<lo) if role==0 else (prices[ix]>hi)
        t=np.flatnonzero(touched); b=np.flatnonzero(broken)
        first_t=int(ix[t[0]]) if len(t) else len(prices)
        first_b=int(ix[b[0]]) if len(b) else len(prices)
        if first_b < first_t: expected=('gap_before_touch', 0, first_b+1)
        elif first_t == len(prices): expected=('untouched', 0, 0)
        else:
            future=prices[first_t+1:]
            reject=future>=hi+DISTANCE if role==0 else future<=lo-DISTANCE
            breaks=future<lo if role==0 else future>hi
            hit=np.flatnonzero(reject|breaks)
            expected=(('rejection' if reject[hit[0]] else 'break'),first_t+1,first_t+int(hit[0])+2) if len(hit) else ('unresolved_touch',first_t+1,0)
        assert (r['status'], r['touch_event'], r['resolve_event']) == expected
        inside=(prices[:event]>=lo)&(prices[:event]<=hi)
        assert r['trade_count']==int(inside.sum())
        assert np.isclose(r['traded_volume'], replay.volumes[:event][inside].sum())
        checked += 1
    for part in selected.partition_by('snapshot', 'method', 'n', 'role'):
        bounds=part.sort('lower_units').select('lower_units', 'upper_units').to_numpy()
        assert np.all(bounds[1:, 0]-bounds[:-1, 1]>=DISTANCE)
        assert part.height<=int(part['n'][0])//2
    assert table.filter(pl.col('latest_training_event')>=pl.col('event')).is_empty()
    # Independently reconstruct shared/personal sufficient statistics.
    for i in np.linspace(0, table.height-1, min(40, table.height), dtype=int):
        r=table.row(int(i), named=True); age=int(r['age_seconds']>=60)
        past=reactions.filter(pl.col('resolve_event')<r['event'])
        shared=past.filter((pl.col('role')==r['role'])&(pl.col('age_group')==age)&(pl.col('period_id')!=r['period_id']))
        own=past.filter(pl.col('period_id')==r['period_id'])
        sr=seed[r['role'], age, 0]+shared.filter(pl.col('outcome')==1).height
        sb=seed[r['role'], age, 1]+shared.filter(pl.col('outcome')==0).height
        sp=(sr+1)/(sr+sb+2)
        personal=(own.filter(pl.col('outcome')==1).height+4*sp)/(own.height+4)
        assert np.isclose(r['probability'], sp+.5*(personal-sp))
    # Rebuild actual causal bands on a truncated source, not just a label mask.
    cutoff=snapshots.row(min(4, snapshots.height-1), named=True)['event']
    prefix=run_fast(replay.frame.head(cutoff), replay.session_open)
    short_times=snapshots.filter(pl.col('event')<=cutoff)['time_us'].to_numpy()
    short,_=candidates(prefix, reactions, seed, short_times)
    columns=[c for c in table.columns if c!='window_end']
    expected=table.filter(pl.col('event')<=cutoff).select(columns)
    assert short.select(columns).equals(expected)
    return dict(independent_labels_and_activity=checked, independent_scores=min(40,table.height),
                nonredundancy='all selections passed', source_prefix='passed')


def summary(selected, counts):
    rows=[]
    for n in SIZES:
        for method in METHODS:
            part=selected.filter((pl.col('n')==n)&(pl.col('method')==method))
            slots=counts.filter((pl.col('n')==n)&(pl.col('method')==method))
            statuses={kind:part.filter(pl.col('status')==kind).height for kind in KINDS}
            resolved=statuses['rejection']+statuses['break']; touched=resolved+statuses['unresolved_touch']
            rows.append(dict(n=n, method=method, selected=part.height, requested=int(slots['requested'].sum()),
                fill_rate=part.height/slots['requested'].sum(), touches=touched,
                touch_rate=touched/part.height if part.height else None,
                rejection_rate=statuses['rejection']/resolved if resolved else None,
                rejection_per_selected=statuses['rejection']/part.height if part.height else None,
                median_distance_dollars=float(part['distance_units'].median()/10000) if part.height else None,
                unique_bands=part.select('day','band_id').unique().height,
                unique_touches=part.filter(pl.col('touch_event')>0).select('day','period_id','touch_event').unique().height,
                **statuses))
    return rows


def self_check():
    prices=np.array([105,103,102,104,101,100,98,100],dtype=np.int64)
    assert label_selection(prices,1,4,102,103,0,1)==(2,2,4)
    assert label_selection(prices,4,8,102,103,0,1)==(1,0,5)
    assert label_selection(np.array([105,103,101]),1,3,102,103,0,2)==(3,2,3)
    assert label_selection(np.array([105,103,103]),1,3,102,103,0,2)==(4,2,0)
    assert label_selection(np.array([105,106,107]),1,3,102,103,0,2)==(0,0,0)
    swing=confirmed_swings(np.array([100,103,105,104,103,101]),2)
    assert np.array_equal(swing,np.array([[2,1,100,0],[5,3,105,1]]))
    assert np.array_equal(confirmed_swings(np.array([100,103,105,104]),2),swing[:1])


def run(source, output):
    self_check()
    config, cache, inputs=load_verified(source)
    days=config['days'][-5:]
    assert days==['2026-08-14','2026-08-17','2026-08-18','2026-08-19','2026-08-20']
    implementation={name:sha(Path(__file__).with_name(name)) for name in
                    ['run_top_levels.py','pressure_engine.py','incremental_prediction.py','run_score_ablation.py']}
    identity=dict(inputs=inputs,implementation=implementation,days=days,sizes=SIZES,
                  distance_units=DISTANCE,selection_seconds=60,history_sessions=1)
    if (output/'manifest.json').exists():
        assert json.loads((output/'manifest.json').read_text())['identity']==identity, 'Run identity changed; use a new runtime directory'
    else:
        write_json(output/'manifest.json',dict(identity=identity,source=str(source),output=str(output),
            git_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=REPO,text=True).strip(),
            primary_n=6,model='Prior-session plus resolved current-session half-bonus rejection probability',
            nonredundancy='Same-role band edge gaps >= $0.02; greedy rank; at most N/2 each side',
            swing='Most recently confirmed $0.02 directional-change pivot in an active band; no fill with non-swings',
            target='First fresh entry in next minute; $0.02 outer-edge rejection before strict break, following to close',
            limitations='Previously inspected sessions; correlated repeated selections; conditional on this candidate generator'))
    all_selected=[];all_counts=[];validation=[];daily=[]
    for index,day in enumerate(days):
        write_json(output/'status.json',dict(status='running',active=day,completed=index,queued=len(days)-index-1))
        print(f'ACTIVE {index+1}/5 {day}',flush=True)
        folder=output/day;folder.mkdir(exist_ok=True)
        if (folder/'receipt.json').exists():
            receipt=json.loads((folder/'receipt.json').read_text())
            for name,digest in receipt['files'].items(): assert sha(folder/name)==digest
            selected=pl.read_parquet(folder/'selected.parquet');counts=pl.read_parquet(folder/'slots.parquet')
            validation.append(dict(day=day,**receipt['validation']))
            print('REUSED',day,flush=True)
        else:
            start=time.perf_counter()
            frame=pl.read_parquet(source/'sessions'/day/'source.parquet')
            provenance=json.loads((source/'sessions'/day/'source_receipt.json').read_text())['provenance']
            opening=int(datetime.fromisoformat(provenance['window_start']).timestamp()*1e6)
            close=int(datetime.fromisoformat(provenance['window_end']).timestamp()*1e6)
            replay=run_fast(frame,opening)
            assert len(replay.lower)==cache[day][1]['bands'] and len(replay.periods)==cache[day][1]['role_periods']
            prior=config['days'][config['days'].index(day)-1]
            times=np.arange(opening+60_000_000,close,60_000_000,dtype=np.int64)
            table,snapshots=candidates(replay,cache[day][0],cache[prior][2],times)
            selected,counts=evaluate(day,frame,table,snapshots)
            checks=validate(replay,table,selected,cache[day][0],cache[prior][2],snapshots)
            files={'candidates.parquet':table,'snapshots.parquet':snapshots,'selected.parquet':selected,'slots.parquet':counts}
            for name,data in files.items(): data.write_parquet(folder/name)
            receipt=dict(files={name:sha(folder/name) for name in files},validation=checks,
                         events=frame.height,snapshots=snapshots.height,seconds=time.perf_counter()-start)
            write_json(folder/'receipt.json',receipt)
            validation.append(dict(day=day,**checks))
            print('COMPLETED',day,'snapshots',snapshots.height,'seconds',round(receipt['seconds'],2),flush=True)
        all_selected.append(selected);all_counts.append(counts)
        daily.extend(dict(day=day,**row) for row in summary(selected,counts))
    selected=pl.concat(all_selected);counts=pl.concat(all_counts)
    pooled=summary(selected,counts)
    write_json(output/'results.json',dict(pooled=pooled,daily=daily))
    write_json(output/'validation.json',validation)
    write_json(output/'status.json',dict(status='complete',completed=5))
    print('PRIMARY N=6', [r for r in pooled if r['n']==6],flush=True)
    print('RESULTS',output/'results.json',flush=True)


def main():
    root=Path('D:/TradingML/runtimes')
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,default=root/'level-book-v8/history-comparison/aapl-20260820-full')
    parser.add_argument('--run',type=Path,default=None)
    args=parser.parse_args()
    output=args.run or root/'level-book-v8/top-levels'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    if not root.is_dir() or not output.resolve().is_relative_to(root.resolve()): raise ValueError('Invalid runtime root')
    output.mkdir(parents=True,exist_ok=True)
    print('RUN',output,flush=True)
    try: run(args.source,output)
    except Exception:
        write_json(output/'status.json',dict(status='failed'));raise


if __name__=='__main__':main()
