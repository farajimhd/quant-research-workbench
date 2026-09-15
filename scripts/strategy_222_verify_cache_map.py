"""Resolve replay caches by exact V7 input contents, not source headers alone."""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import sys
sys.dont_write_bytecode = True
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import argparse
from datetime import datetime, time, timedelta
import hashlib
import json
import subprocess
from zoneinfo import ZoneInfo

import numpy as np

from strategy_222_supervised_research import digest, save
from strategy_222_macd_episodes import closed_connection
from src.runtime_paths import runtime_root


def prepared_identity(connection, symbol, day):
    """Same ordered float64 OHLCV identity used by QMD PreparedStream receipts."""
    begin = datetime.combine(datetime.fromisoformat(day).date(), time(4), ZoneInfo('America/New_York')).timestamp()
    end = begin + timedelta(hours=16).total_seconds()
    values = []
    for micros, raw in connection.execute("select as_of_us,bar_json from strategy_frames where ticker=? and timeframe='1s' order by as_of_us,sequence", (symbol,)):
        at = micros / 1e6
        if not begin < at <= end: continue
        bar = json.loads(raw)
        if (bar.get('sym') != symbol or bar.get('timeframe') != '1s'
                or datetime.fromisoformat(bar['bar_end']).timestamp() != at or at != int(at)
                or values and at <= values[-1][0]):
            raise ValueError('Invalid or unordered prepared bar identity')
        values.append([at, *[float(bar[k]) for k in ('open', 'high', 'low', 'close', 'volume')]])
    array = np.asarray(values, dtype=np.float64).reshape((-1, 6))
    if not np.isfinite(array).all(): raise ValueError('Nonfinite prepared bar')
    return hashlib.sha256(array.tobytes()).hexdigest(), len(values)


def run(previous_map, cache_dir, output):
    output = output.resolve(); output.relative_to(runtime_root().resolve())
    previous = json.loads(previous_map.read_text())
    if previous['missing']: raise ValueError('Incomplete original mapping')
    wanted = {}
    for rid, row in previous['runs'].items():
        path = Path(row['summary'])
        if digest(path) != row['summary_sha256']: raise ValueError('Replay summary changed')
        summary = json.loads(path.read_text()); symbol = row['symbol']
        source = summary['data_authority']['sources'][f"v7:{symbol}:{summary['session_date']}:prepared-source"]['source_revision']
        wanted[rid] = (row, summary, source)
    paths = [Path(p) for p in subprocess.check_output(['rg', '--files', str(cache_dir), '-g', '*.sqlite3'], text=True).splitlines()]
    matches = {rid: [] for rid in wanted}; mismatches = []
    for index, path in enumerate(sorted(paths)):
        connection = closed_connection(path)
        try:
            for rid, (row, summary, expected) in wanted.items():
                symbol = row['symbol']
                headers = {tf: json.loads(raw) for tf, raw in connection.execute(
                    "select timeframe,authority_json from strategy_frame_streams where ticker=? and timeframe in ('1s','5s')", (symbol,))}
                if headers.get('1s') != expected['prepared_authority']: continue
                if headers.get('5s') != summary['data_authority']['sources'][f'derived:{symbol}:5s']: continue
                sha, count = prepared_identity(connection, symbol, summary['session_date'])
                item = dict(cache=str(path.resolve()), cache_sha256=digest(path), v7_input_hash=sha, bars=count)
                if sha == expected['input_hash']: matches[rid].append(item)
                else: mismatches.append(dict(run_id=rid, symbol=symbol, **item))
        finally: connection.close()
        if (index + 1) % 20 == 0:
            print(f"Verified caches {index+1}/{len(paths)}; matched runs={sum(bool(v) for v in matches.values())}/{len(wanted)}", flush=True)
    missing = [rid for rid, items in matches.items() if not items]
    if missing: raise ValueError(f'No exact prepared input for {missing}')
    result = dict(previous, parent_map=dict(path=str(previous_map.resolve()), sha256=digest(previous_map)),
        cache_files_scanned=len(paths), header_matches_rejected=mismatches,
        matching_contract='exact prepared OHLCV hash plus original 1s and 5s authority', runs={})
    for rid, items in matches.items():
        prior = wanted[rid][0]
        choice = next((item for item in items if item['cache'] == prior['cache']), items[0])
        result['runs'][rid] = dict(prior, **choice)
    if output.exists() and json.loads(output.read_text()) != result:
        raise ValueError('Output exists with different evidence; use a successor path')
    save(output, result)
    changed = sum(result['runs'][rid]['cache'] != row['cache'] for rid, row in previous['runs'].items())
    print(f'Complete: {len(wanted)} exact mappings; {changed} cache selections corrected; {len(mismatches)} header-only matches rejected.', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('previous-map', 'cache-dir', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args(); run(args.previous_map, args.cache_dir, args.output)
