"""Add causal nearby-level context to every frozen supervised decision.

Uses each original replay's approved settings, canonical prepared frames and
checkpoint identity. Compressed batches bound memory and permit exact restart.
Hindsight labels are copied byte-for-byte and never enter feature extraction.
"""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import sys
sys.dont_write_bytecode = True
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import argparse
from collections import defaultdict
from datetime import datetime, timezone
import gzip
import json
import sqlite3
from uuid import uuid4, UUID

from strategy_222_supervised_research import digest, save
from strategy_222_level_features import decision_rows, level_features
from strategy_222_feature_comparison import epoch
from strategy_222_macd_episodes import closed_connection
from src.runtime_paths import runtime_root


def attach(row, record, snapshot, summary, settings):
    stamp, raw = record
    if row['id'] != f"{summary['run_id']}:{row['sequence']}" or epoch(stamp) != row['at']:
        raise ValueError('Decision identity/time mismatch')
    decision = json.loads(raw)
    symbol = row['symbol']
    if decision['ticker'] != symbol or not any(str(s).startswith(f'qmd-derived:{symbol}:1s:') for s in decision['source_signal_ids']):
        raise ValueError('Wrong decision ticker or timeframe')
    extra, evidence = level_features(snapshot, symbol=symbol, at=row['at'],
        price=decision['metadata']['reference_price'], atr_pct=row['features'].get('candle_1s.atr_pct'),
        summary=summary, settings=settings)
    return dict(id=row['id'], features=extra, evidence=evidence)


def save_batch(path, data):
    temporary = path.with_suffix('.tmp')
    temporary.write_bytes(gzip.compress(json.dumps(data, allow_nan=False, separators=(',', ':')).encode(), mtime=0))
    temporary.replace(path)


def run(source, cache_map, output, stop_request=None):
    from src.backend.v7_book_cursor import PreparedV7Cursors
    from src.backend.qmd_gateway_client import qmd_history_post_json
    output = output.resolve(); output.relative_to(runtime_root().resolve())
    parent = json.loads((source / 'manifest.json').read_text())
    if parent['status'] != 'completed': raise ValueError('Incomplete source features')
    for name, sha in parent['outputs'].items():
        if digest(source / name) != sha: raise ValueError('Source features changed')
    if parent['identity'].get(str(cache_map.resolve())) != digest(cache_map):
        raise ValueError('Cache map differs from source feature authority')
    mapping = json.loads(cache_map.read_text())
    originals = [Path(k) for k in parent['identity'] if Path(k).name == 'dataset-manifest.json']
    if len(originals) != 1 or digest(originals[0]) != mapping['dataset_manifest_sha256']:
        raise ValueError('Original supervision manifest changed')
    original = json.loads(originals[0].read_text())
    pinned = {str(Path(k).resolve()): v for k, v in original['inputs'].items()}
    rows = json.loads((source / 'features.json').read_text())
    labels = json.loads((source / 'labels.json').read_text())
    ids = [r['id'] for r in rows]
    if len(set(ids)) != len(ids) or ids != [r['id'] for r in labels]:
        raise ValueError('Label identity/order mismatch')
    grouped = defaultdict(list)
    for row in rows:
        UUID(row['run_id'])
        grouped[row['run_id']].append(row)
    if mapping['missing'] or set(grouped) != set(mapping['runs']): raise ValueError('Run coverage mismatch')
    files = [source / 'manifest.json', source / 'features.json', source / 'labels.json', cache_map, originals[0], Path(__file__)]
    files += [Path(__file__).with_name(name) for name in ('strategy_222_level_features.py',
        'strategy_222_supervised_research.py', 'strategy_222_macd_episodes.py', 'strategy_222_feature_comparison.py')]
    files += [Path(__file__).resolve().parents[1] / p for p in ('src/trading_runtime/structure_level_contract.py',
        'src/trading_runtime/historical_hod.py', 'src/backend/v7_book_cursor.py', 'src/market_engine/v7_snapshot_transport.py')]
    configurations = {}; summaries = {}
    for rid, authority in mapping['runs'].items():
        summary_path = Path(authority['summary']); cache = Path(authority['cache'])
        journal = summary_path.parent / 'journal.sqlite3'; configuration = summary_path.parent / 'approved-configuration.json'
        if (digest(summary_path) != authority['summary_sha256'] or digest(cache) != authority['cache_sha256']
                or digest(journal) != pinned.get(str(journal.resolve()))):
            raise ValueError('Replay authority changed')
        summary = json.loads(summary_path.read_text()); config = json.loads(configuration.read_text())
        if (summary['status'] != 'completed' or summary['run_id'] != rid
                or summary['experimental_structure_book'] != 'level-book-v7'
                or config['content_hash'] != summary['configuration_content_hash']
                or config['revision_id'] != summary['configuration_revision_id']):
            raise ValueError('Wrong or incomplete replay configuration')
        summaries[rid] = summary; configurations[rid] = config['payload']['strategy']['parameters']['historical_hod']
        files.extend((summary_path, cache, journal, configuration))
    identity = {str(p.resolve()): digest(p) for p in set(files)}
    tasks = []
    for rid in sorted(grouped):
        ordered = sorted(grouped[rid], key=lambda r: (r['at'], r['sequence']))
        for start in range(0, len(ordered), 32):
            tasks.append((f'{rid}-{start:06d}', rid, ordered[start:start + 32]))
    output.mkdir(parents=True, exist_ok=True); manifest = output / 'manifest.json'
    state = json.loads(manifest.read_text()) if manifest.exists() else dict(identity=identity, batches={})
    if state['identity'] != identity: raise ValueError('Inputs changed; use a successor directory')
    for item in state['batches'].values():
        if digest(output / item['file']) != item['sha256']: raise ValueError('Completed batch changed')
    if state.get('status') == 'completed':
        for name, sha in state['outputs'].items():
            if digest(output / name) != sha: raise ValueError('Completed aggregate changed')
        print('Completed supervised level study verified; no recomputation.', flush=True)
        return
    state.update(status='running', active=0, completed=len(state['batches']), total=len(tasks),
        queued=len(tasks) - len(state['batches']), skipped=len(state['batches']), retried=int(manifest.exists()), failed=0)
    save(manifest, state)
    lease = sqlite3.connect(runtime_root() / 'analysis/strategy-222-refinement/_prepared-run-lease.sqlite3', timeout=.1, isolation_level=None)
    pool = None; connection = None; current_run = None
    try:
        lease.execute('BEGIN EXCLUSIVE')
        for key, rid, batch in tasks:
            if key in state['batches']: continue
            if stop_request is not None and stop_request.exists(): raise KeyboardInterrupt('Stop requested at batch boundary')
            summary = summaries[rid]; authority = mapping['runs'][rid]; symbol = authority['symbol']
            if any(r['symbol'] != symbol for r in batch): raise ValueError('Batch ticker differs from authority')
            state.update(active=1, queued=len(tasks) - len(state['batches']) - 1, current=key)
            save(manifest, state)
            if current_run != rid:
                if pool is not None: pool.close(); pool = None
                if connection is not None: connection.close(); connection = None
                pool = PreparedV7Cursors(uuid4().hex, dict(fingerprint=summary['experimental_structure_fingerprint']), [symbol])
                packet = qmd_history_post_json('/level-book-v7/stream', dict(operation='prepare', stream_id=pool.stream_id,
                    frame_path=authority['cache'], day=summary['session_date'], catalog_hash=summary['experimental_structure_fingerprint'],
                    required_end=summary['session_end'], tickers=[symbol]), timeout=180)
                if len(packet['rows']) != 1 or packet['rows'][0]['ticker'] != symbol: raise ValueError('Unexpected preparation receipt')
                receipt = dict(packet['rows'][0]); seed = pool.cursors[symbol].decoder.decode(receipt.pop('seed_packet'))
                expected = summary['data_authority']['sources'][f"v7:{symbol}:{summary['session_date']}:prepared-source"]['source_revision']
                if (receipt['input_hash'] != expected['input_hash'] or seed['source_audit']['source_revision'] != expected
                        or seed['as_of'] != epoch(summary['session_start']) or seed['bars_processed'] != 0):
                    raise ValueError('Prepared source differs from replay')
                connection = closed_connection(Path(authority['summary']).parent / 'journal.sqlite3'); current_run = rid
            pool.fetch([(symbol, datetime.fromtimestamp(r['at'], timezone.utc)) for r in batch])
            decisions = decision_rows(connection, rid, [r['sequence'] for r in batch])
            snapshots = {}; additions = []
            for row in batch:
                snapshot = pool.cursors[symbol].snapshot(datetime.fromtimestamp(row['at'], timezone.utc))
                snapshots[str(int(row['at']))] = {k: v for k, v in snapshot.items() if k != 'qmd_structure_unified_levels'}
                additions.append(attach(row, decisions[row['sequence']], snapshot, summary, configurations[rid]))
            target = output / (key + '.json.gz')
            save_batch(target, dict(receipt=receipt, snapshots=snapshots, rows=additions))
            state['batches'][key] = dict(file=target.name, sha256=digest(target), count=len(batch), run_id=rid)
            state.update(active=0, completed=len(state['batches']))
            save(manifest, state)
            print(f"Completed={state['completed']}/{len(tasks)} batches active=0 queued={len(tasks)-state['completed']} skipped={state['skipped']} failed=0; {symbol}", flush=True)
        if pool is not None: pool.close(); pool = None
        extra_by_id = {}
        for item in state['batches'].values():
            data = json.loads(gzip.decompress((output / item['file']).read_bytes()))
            for extra in data['rows']:
                if extra['id'] in extra_by_id: raise ValueError('Duplicate enriched decision')
                extra_by_id[extra['id']] = extra
        if set(extra_by_id) != set(ids): raise ValueError('Incomplete enriched coverage')
        for row in rows:
            extra = extra_by_id[row['id']]
            row['features'].update(extra['features']); row['level_authority'] = extra['evidence']
        save(output / 'features.json', rows)
        (output / 'labels.json').write_bytes((source / 'labels.json').read_bytes())
        state.update(status='completed', active=0, queued=0, stream_released=True, rows=len(rows), runs=len(grouped),
            label_policy=parent['label_policy'], limitations=parent['limitations'],
            outputs={name: digest(output / name) for name in ('features.json', 'labels.json')})
        save(manifest, state)
        print(f'Complete: {len(rows)} decisions, {len(grouped)} runs; labels unchanged; prepared streams released.', flush=True)
    except BaseException as exc:
        state.update(status='interrupted' if isinstance(exc, KeyboardInterrupt) else 'failed', active=0,
            failed=int(not isinstance(exc, KeyboardInterrupt)), error=str(exc))
        save(manifest, state); raise
    finally:
        try:
            if pool is not None: pool.close()
        finally:
            if connection is not None: connection.close()
            lease.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('source', 'cache-map', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--stop-request-file', type=Path)
    args = parser.parse_args()
    run(args.source, args.cache_map, args.output, args.stop_request_file)
