"""Bounded, restartable full-session history-length experiment; no notebook.

Run: python -B research/level_book/v8/run_history_comparison.py
Generated sources, labels, receipts and predictions stay under the runtime root.
"""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import sys
sys.dont_write_bytecode = True
import argparse
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
import hashlib
import json
import subprocess
import time

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
import numpy as np
import polars as pl
from research.level_book.v8.pressure_source import load_pressure_sample
from research.level_book.v8.pressure_engine import run_fast
from research.level_book.v8.incremental_prediction import encounters, predict_online, metrics
from research.mlops.env import load_env_files
from research.mlops.clickhouse import (ClickHouseHttpClient, discover_clickhouse_env_files,
    default_clickhouse_url, default_clickhouse_user, default_clickhouse_password)
from research.level_book.v7.campaign_source import literal


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2), encoding='utf-8')
    temporary.replace(path)


def session_days(ticker, end, count):
    load_env_files(discover_clickhouse_env_files(), verbose=False)
    client = ClickHouseHttpClient(default_clickhouse_url(), default_clickhouse_user(),
        default_clickhouse_password(), timeout_seconds=90, persistent=True,
        default_query_params=dict(readonly=1, max_threads=2, max_result_rows=100,
                                  result_overflow_mode='throw'))
    try:
        sql = ("SELECT source_date FROM market_sip_compact.events_ordinal_continuity FINAL "
               f"WHERE ticker={literal(ticker)} AND source_date<={literal(end)} "
               f"ORDER BY source_date DESC LIMIT {count} FORMAT JSONEachRow")
        days = sorted(json.loads(line)['source_date'] for line in client.execute(sql).splitlines())
        if len(days) != count or len(set(days)) != count:
            raise ValueError('Insufficient unique canonical source sessions')
        return days
    finally:
        client.close()


def prepare_day(root, ticker, day, implementation):
    folder = root/day
    folder.mkdir(parents=True, exist_ok=True)
    source_path = folder/'source.parquet'
    source_receipt = folder/'source_receipt.json'
    if source_receipt.exists():
        receipt = json.loads(source_receipt.read_text())
        if receipt['ticker'] != ticker or receipt['day'] != day or digest(source_path) != receipt['parquet_sha256']:
            raise ValueError(f'Source cache identity/hash mismatch: {day}')
        frame = pl.read_parquet(source_path)
    else:
        started = time.perf_counter()
        rows, provenance = load_pressure_sample(ticker, day, count=None)
        frame = pl.DataFrame(rows).with_row_index('event', offset=1)
        frame.write_parquet(source_path)
        receipt = dict(ticker=ticker, day=day, parquet_sha256=digest(source_path),
                       provenance=provenance, load_seconds=time.perf_counter()-started)
        save_json(source_receipt, receipt)
    expected = int(receipt['provenance']['audit'][0]['eligible'])
    if frame.height != expected:
        raise ValueError('Full-session row count mismatch')
    label_path = folder/'encounters.parquet'
    replay_receipt = folder/'replay_receipt.json'
    if replay_receipt.exists():
        checked = json.loads(replay_receipt.read_text())
        if (checked['implementation'] != implementation or checked['source_sha256'] != digest(source_path)
                or checked['encounters_sha256'] != digest(label_path)):
            raise ValueError(f'Replay cache identity mismatch: {day}; use a new run directory')
        return pl.read_parquet(label_path), checked
    started = time.perf_counter()
    opening = datetime.fromisoformat(day+'T09:30:00').replace(tzinfo=ZoneInfo('America/New_York'))
    replay = run_fast(frame, int(opening.timestamp()*1e6))
    labels = encounters(replay, distance=200)
    # First-break labels and timestamp/ordinal ordering are checked by shared code.
    assert labels.filter(pl.col('resolve_event') <= pl.col('touch_event')).is_empty()
    resolved = labels.filter(pl.col('outcome').is_not_null())
    # Native aggregate directly builds the shared seed without retaining personal IDs.
    groups = np.zeros((2, 2, 4))
    for (role, age, outcome), part in resolved.group_by('role', 'age_group', 'outcome'):
        col = 0 if outcome == 1 else 1
        groups[role, age, col] = part.height
        groups[role, age, col+2] = part['duration_seconds'].sum()
    labels.write_parquet(label_path)
    checked = dict(day=day, events=frame.height, encounters=labels.height,
        resolved=resolved.height, pending=labels.height-resolved.height,
        bands=len(replay.lower), role_periods=len(replay.periods),
        groups=groups.tolist(), implementation=implementation,
        source_sha256=digest(source_path), encounters_sha256=digest(label_path),
        replay_seconds=time.perf_counter()-started, load_seconds=receipt['load_seconds'])
    save_json(replay_receipt, checked)
    return labels, checked


def evaluate(days, cache, root, histories, evaluation_sessions):
    summaries, coverage, pooled = [], [], {h: [] for h in histories}
    columns = ['prediction', 'group_prediction', 'role_prediction',
               'global_prediction', 'frozen_prediction']
    for index in range(len(days)-evaluation_sessions, len(days)):
        day = days[index]
        labels, receipt = cache[day]
        coverage.append(dict(day=day, **{k: receipt[k] for k in ['events','encounters','resolved','pending']}))
        identity = None
        for history in histories:
            prior_days = days[index-history:index]
            assert len(prior_days) == history and all(d < day for d in prior_days)
            seed = sum((np.asarray(cache[d][1]['groups']) for d in prior_days), start=np.zeros((2,2,4)))
            predicted, model = predict_online(labels, 0, receipt['events'], initial_groups=seed)
            frozen = (seed[:,:,0].sum()+1)/(seed[:,:,:2].sum()+2)
            predicted = predicted.with_columns(pl.lit(frozen).alias('frozen_prediction'),
                pl.lit(day).alias('day'), pl.lit(history).alias('history_sessions'))
            assert predicted.filter(pl.col('latest_training_event') >= pl.col('touch_event')).is_empty()
            assert np.allclose(model.groups, seed+np.asarray(receipt['groups']))
            ids = predicted.select('period_id','touch_event','resolve_event','outcome')
            if identity is not None:
                assert ids.equals(identity), 'History lengths evaluated on different encounters'
            identity = ids
            predicted.write_parquet(root/f'predictions_{day}_h{history}.parquet')
            resolved = predicted.filter(pl.col('outcome').is_not_null())
            for column in columns:
                summaries.append(dict(day=day, history_sessions=history,
                    training_resolved=int(seed[:,:,:2].sum()), **metrics(resolved, column)))
            pooled[history].append(resolved)
            print(f'EVALUATED {day} history={history} train={int(seed[:,:,:2].sum()):,} test={resolved.height:,}', flush=True)
    daily = pl.DataFrame(summaries)
    daily.write_parquet(root/'daily_metrics.parquet')
    total = []
    calibration = []
    for history, frames in pooled.items():
        rows = pl.concat(frames)
        for column in columns:
            total.append(dict(history_sessions=history, **metrics(rows, column)))
        bins = rows.with_columns((pl.col('prediction')*10).floor().clip(0,9).cast(pl.Int64).alias('bin')).group_by('bin').agg(
            pl.len().alias('encounters'), pl.col('prediction').mean().alias('mean_prediction'),
            pl.col('outcome').mean().alias('observed_rejection')).sort('bin').with_columns(pl.lit(history).alias('history_sessions'))
        calibration.append(bins)
    aggregate = pl.DataFrame(total)
    aggregate.write_parquet(root/'pooled_metrics.parquet')
    pl.concat(calibration).write_parquet(root/'calibration.parquet')
    save_json(root/'results.json', dict(coverage=coverage, pooled=total, daily=summaries,
        note='Five paired full sessions; correlated encounters, no significance claim. Pending close outcomes excluded.'))
    return aggregate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ticker', default='AAPL')
    parser.add_argument('--end', default='2026-08-20')
    parser.add_argument('--evaluation-sessions', type=int, default=5)
    parser.add_argument('--run', type=Path, default=Path('D:/TradingML/runtimes/level-book-v8/history-comparison/aapl-20260820-full'))
    args = parser.parse_args()
    if not 1 <= args.evaluation_sessions <= 10:
        parser.error('Evaluation sessions must be 1..10')
    runtime = Path('D:/TradingML/runtimes')
    if not runtime.is_dir() or not args.run.resolve().is_relative_to(runtime.resolve()):
        raise ValueError('Required runtime root unavailable or output outside runtime root')
    args.run.mkdir(parents=True, exist_ok=True)
    implementation = {name: digest(Path(__file__).with_name(name)) for name in
        ['pressure_engine.py','pressure_source.py','incremental_prediction.py','run_history_comparison.py']}
    histories = [1,5,20]
    days = session_days(args.ticker, args.end, max(histories)+args.evaluation_sessions)
    config = dict(ticker=args.ticker, days=days, histories=histories, evaluation_sessions=args.evaluation_sessions,
                  distance_units=200, age_seconds=60, shared_weight=4, implementation=implementation)
    config_path = args.run/'config.json'
    if config_path.exists() and json.loads(config_path.read_text()) != config:
        raise ValueError('Run configuration changed; choose a new run path')
    save_json(config_path, config)
    print('COMMAND:', sys.executable, '-B', __file__, '--run', args.run, flush=True)
    print('CONFIG:', json.dumps(config), flush=True)
    cache = {}
    for i, day in enumerate(days):
        print(f'ACTIVE {i+1}/{len(days)} {day}; completed={i} queued={len(days)-i-1}', flush=True)
        try:
            cache[day] = prepare_day(args.run/'sessions', args.ticker, day, implementation)
        except Exception:
            save_json(args.run/'status.json', dict(status='failed', day=day, completed=list(cache), queued=days[i+1:]))
            raise
        r = cache[day][1]
        print(f"COMPLETED {day} events={r['events']:,} resolved={r['resolved']:,} pending={r['pending']} replay={r['replay_seconds']:.2f}s", flush=True)
        save_json(args.run/'status.json', dict(status='running', completed=list(cache), queued=days[i+1:]))
    summary = evaluate(days, cache, args.run, histories, args.evaluation_sessions)
    save_json(args.run/'status.json', dict(status='complete', completed=days, queued=[],
        git_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=REPO,text=True).strip()))
    print(summary, flush=True)
    print('RESULTS:', args.run/'results.json', flush=True)


if __name__ == '__main__':
    main()
