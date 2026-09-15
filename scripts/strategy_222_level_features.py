"""Measure nearby V7 bands using the replay's exact prepared source authority.

Hindsight anchor labels remain outside feature extraction. No trading rule or
target/stop is inferred from a band's distance. Missing nearby bands remain
missing rather than implying unlimited room.
"""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import sys
sys.dont_write_bytecode = True
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import math
import sqlite3
from types import SimpleNamespace
from uuid import uuid4

from strategy_222_supervised_research import digest, save
from strategy_222_macd_episodes import closed_connection
from strategy_222_feature_comparison import contrasts, epoch
from src.trading_runtime.structure_level_contract import strategy_snapshot
from src.trading_runtime.historical_hod import selected_levels
from src.runtime_paths import runtime_root


def level_features(snapshot, *, symbol, at, price, atr_pct, summary, settings):
    """Only as-of market inputs enter this function; labels are not arguments."""
    if not math.isfinite(at) or not math.isfinite(price) or price <= 0:
        raise ValueError('Invalid decision price/time')
    second = int(at)
    day = summary['session_date']
    expected = summary['data_authority']['sources']
    prefix = f'v7:{symbol}:{day}'
    if (snapshot['ticker'] != symbol or snapshot['as_of'] != second
            or snapshot['session_date'] != day
            or not math.isfinite(snapshot['max_input_timestamp'])
            or snapshot['max_input_timestamp'] > second
            or snapshot['provenance'] != expected[prefix]['checkpoint']
            or snapshot['provenance']['catalog_hash'] != summary['experimental_structure_fingerprint']):
        raise ValueError('V7 identity or causal cutoff mismatch')
    audit = snapshot['source_audit']
    if (audit['source'] != 'qmd-prepared-causal-seconds'
            or audit['consumed_through'] != second
            or audit['source_revision'] != expected[prefix + ':prepared-source']['source_revision']):
        raise ValueError('V7 intraday source differs from replay authority')
    observed = datetime.fromtimestamp(at, timezone.utc)
    projected = strategy_snapshot(snapshot, observed)['unified_levels']
    levels = selected_levels(SimpleNamespace(
        structural_support_levels=[r for r in projected if r['side'] > 0],
        structural_resistance_levels=[r for r in projected if r['side'] < 0],
        structural_transition_levels=[r for r in projected if r['side'] == 0]), settings, at)
    # A future availability timestamp is not a usable as-of feature, even if a
    # malformed row happened to pass the shared strategy's confirmation filter.
    if any(r.get('available_at_ms', r['confirmed_at_ms']) > at * 1000 for r in levels):
        raise ValueError('Future level availability')
    ids = [r['unified_level_id'] for r in levels]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate eligible level identity')
    features = {}
    references = {}
    atr = price * atr_pct / 100 if isinstance(atr_pct, (int, float)) and math.isfinite(atr_pct) and atr_pct > 0 else None
    for role, side in (('support', 1), ('resistance', -1), ('transition', 0)):
        rows = [r for r in levels if r['side'] == side]
        head = 'levels.' + role
        features[head + '.count'] = len(rows)
        features[head + '.containing_count'] = sum(r['lower'] <= price <= r['upper'] for r in rows)
        for location, candidates, distance in (
                ('above', [r for r in rows if r['lower'] > price], lambda r: r['lower'] - price),
                ('below', [r for r in rows if r['upper'] < price], lambda r: price - r['upper']),
                ('containing', [r for r in rows if r['lower'] <= price <= r['upper']], lambda r: abs(r['price'] - price))):
            key = head + '.' + location
            features[key + '.available'] = float(bool(candidates))
            if atr is not None:
                features[key + '.count_within_1_atr'] = sum(distance(r) <= atr for r in candidates)
            if not candidates:
                continue
            row = min(candidates, key=lambda r: (distance(r), r['unified_level_id']))
            references[key] = row['unified_level_id']
            for field in ('lower', 'price', 'upper'):
                delta = row[field] - price
                features[key + '.' + field + '_distance_pct'] = delta / price * 100
                if atr is not None:
                    features[key + '.' + field + '_distance_atr'] = delta / atr
            width = row['upper'] - row['lower']
            features[key + '.width_pct'] = width / price * 100
            if width > 0:
                features[key + '.price_location_in_band'] = (price - row['lower']) / width
            features[key + '.historical'] = float(row['historical'])
            features[key + '.confirmation_age_s'] = at - row['confirmed_at_ms'] / 1000
            features[key + '.member_age_s'] = at - row['oldest_member_confirmed_at_ms'] / 1000
            features[key + '.observations'] = row['observation_count']
            features[key + '.transition_from_support'] = float(row.get('transition_from') == 'support')
            features[key + '.transition_from_resistance'] = float(row.get('transition_from') == 'resistance')
    if not all(math.isfinite(v) for v in features.values()):
        raise ValueError('Nonfinite level feature')
    return features, dict(raw_count=len(snapshot['unified_levels']), projected_count=len(projected),
        eligible_count=len(levels), selected_ids=references, as_of=second,
        catalog_hash=snapshot['provenance']['catalog_hash'], input_hash=audit['input_hash'])


def run(source, cache, summary_path, output):
    from src.backend.v7_book_cursor import PreparedV7Cursors
    from src.backend.qmd_gateway_client import qmd_history_post_json
    output = output.resolve(); output.relative_to(runtime_root().resolve())
    source_manifest = json.loads((source / 'manifest.json').read_text())
    if source_manifest['status'] != 'completed':
        raise ValueError('Incomplete source study')
    for name, sha in source_manifest['outputs'].items():
        if digest(source / name) != sha:
            raise ValueError('Source study output changed')
    journal = summary_path.parent / 'journal.sqlite3'
    for path in (cache, summary_path, journal):
        if digest(path) != source_manifest['identity'].get(str(path.resolve())):
            raise ValueError('Source replay authority changed')
    summary = json.loads(summary_path.read_text())
    if summary['status'] != 'completed' or summary['experimental_structure_book'] != 'level-book-v7':
        raise ValueError('Require completed V7 replay')
    configuration_path = summary_path.parent / 'approved-configuration.json'
    configuration = json.loads(configuration_path.read_text())
    if (configuration['content_hash'] != summary['configuration_content_hash']
            or configuration['revision_id'] != summary['configuration_revision_id']):
        raise ValueError('Approved configuration differs from replay')
    settings = configuration['payload']['strategy']['parameters']['historical_hod']
    inputs = [source / 'manifest.json', source / 'samples.json', cache, summary_path, journal, configuration_path, Path(__file__)]
    repo = Path(__file__).resolve().parents[1]
    inputs.extend(repo / p for p in ('src/trading_runtime/structure_level_contract.py',
        'src/trading_runtime/historical_hod.py', 'src/backend/v7_book_cursor.py',
        'src/market_engine/v7_snapshot_transport.py', 'scripts/strategy_222_feature_comparison.py'))
    identity = {str(p.resolve()): digest(p) for p in inputs}
    rows = json.loads((source / 'samples.json').read_text())
    by_symbol = defaultdict(list)
    for index, row in enumerate(rows):
        if row['status'] == 'sampled':
            by_symbol[row['symbol']].append((index, row))
    output.mkdir(parents=True, exist_ok=True)
    manifest = output / 'manifest.json'
    state = json.loads(manifest.read_text()) if manifest.exists() else dict(identity=identity, symbols={})
    if state['identity'] != identity:
        raise ValueError('Inputs changed; use a successor directory')
    for symbol, item in state['symbols'].items():
        if digest(output / item['file']) != item['sha256']:
            raise ValueError('Completed symbol output changed')
    if state.get('status') == 'completed':
        for name, sha in state['outputs'].items():
            if digest(output / name) != sha: raise ValueError('Completed aggregate changed')
        print('Completed level study verified; no recomputation.', flush=True)
        return
    pending = sorted(set(by_symbol) - set(state['symbols']))
    state.update(status='running', active=0, queued=len(pending), failed=0,
        completed=len(state['symbols']), skipped=len(state['symbols']), retried=int(manifest.exists()))
    save(manifest, state)
    lease = sqlite3.connect(runtime_root() / 'analysis/strategy-222-refinement/_prepared-run-lease.sqlite3', timeout=.1, isolation_level=None)
    pool = PreparedV7Cursors(uuid4().hex, dict(fingerprint=summary['experimental_structure_fingerprint']), pending)
    connection = None
    released = False
    try:
        lease.execute('BEGIN EXCLUSIVE')
        connection = closed_connection(journal)
        for symbol in pending:
            state.update(active=1, current_symbol=symbol, queued=len(pending) - (len(state['symbols']) - state['skipped']) - 1)
            save(manifest, state)
            print(f"Preparing {symbol}; completed={state['completed']} active=1 queued={state['queued']} skipped={state['skipped']} failed=0", flush=True)
            packet = qmd_history_post_json('/level-book-v7/stream', dict(operation='prepare',
                stream_id=pool.stream_id, frame_path=str(cache.resolve()), day=summary['session_date'],
                catalog_hash=summary['experimental_structure_fingerprint'], required_end=summary['session_end'], tickers=[symbol]), timeout=180)
            if len(packet['rows']) != 1 or packet['rows'][0]['ticker'] != symbol:
                raise ValueError('Unexpected preparation receipt')
            receipt = dict(packet['rows'][0]); seed = pool.cursors[symbol].decoder.decode(receipt.pop('seed_packet'))
            expected = summary['data_authority']['sources'][f"v7:{symbol}:{summary['session_date']}:prepared-source"]['source_revision']
            if (receipt['input_hash'] != expected['input_hash'] or seed['source_audit']['source_revision'] != expected
                    or seed['as_of'] != epoch(summary['session_start']) or seed['bars_processed'] != 0):
                raise ValueError('Prepared input differs from replay')
            snapshots = {}; enriched = []
            times = sorted({int(row['decision_at']) for _, row in by_symbol[symbol]})
            for start in range(0, len(times), 32):
                chunk = times[start:start + 32]
                pool.fetch([(symbol, datetime.fromtimestamp(at, timezone.utc)) for at in chunk])
                for at in chunk:
                    snapshot = pool.cursors[symbol].snapshot(datetime.fromtimestamp(at, timezone.utc))
                    snapshots[str(at)] = {k: v for k, v in snapshot.items() if k != 'qmd_structure_unified_levels'}
                print(f"{symbol}: level snapshots {min(start + 32, len(times))}/{len(times)}", flush=True)
            for index, row in by_symbol[symbol]:
                record = connection.execute("select event_time,payload_json from journal where category='strategy_decision' and sequence=?", (row['decision_sequence'],)).fetchone()
                if record is None or epoch(record[0]) != row['decision_at']:
                    raise ValueError('Decision timestamp/sequence mismatch')
                decision = json.loads(record[1])
                if decision['ticker'] != symbol or not any(str(s).startswith(f'qmd-derived:{symbol}:1s:') for s in decision['source_signal_ids']):
                    raise ValueError('Decision ticker or delivery mismatch')
                extra, evidence = level_features(snapshots[str(int(row['decision_at']))], symbol=symbol,
                    at=row['decision_at'], price=decision['metadata']['reference_price'],
                    atr_pct=row['features'].get('candle_1s.atr_pct'), summary=summary, settings=settings)
                enriched.append(dict(index=index, features=extra, evidence=evidence))
            target = output / f'{symbol}.json'
            save(target, dict(receipt=receipt, snapshots=snapshots, rows=enriched))
            state['symbols'][symbol] = dict(file=target.name, sha256=digest(target), rows=len(enriched), snapshots=len(snapshots))
            state.update(completed=len(state['symbols']), active=0)
            save(manifest, state)
        pool.close(); released = True
        for item in state['symbols'].values():
            for extra in json.loads((output / item['file']).read_text())['rows']:
                row = rows[extra['index']]
                row['features'].update(extra['features']); row['level_authority'] = extra['evidence']
        save(output / 'samples.json', rows)
        save(output / 'comparison.json', contrasts(rows))
        state.update(status='completed', active=0, queued=0, stream_released=True,
            sample_status=dict(Counter(row['status'] for row in rows)),
            outputs={name: digest(output / name) for name in ('samples.json', 'comparison.json')},
            limitations=['Selected development population; no unseen-session validation.',
                'Post-anchor samples cannot justify earlier decisions.',
                'Band distances are market context, not certified executable stops or targets.'])
        save(manifest, state)
        print(f"Completed {len(rows)} anchor samples; {state['sample_status']}; prepared stream released.", flush=True)
    except BaseException as exc:
        state.update(status='interrupted' if isinstance(exc, KeyboardInterrupt) else 'failed', active=0,
            failed=int(not isinstance(exc, KeyboardInterrupt)), error=str(exc))
        save(manifest, state)
        raise
    finally:
        try:
            if not released: pool.close()
        finally:
            if connection is not None: connection.close()
            lease.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('source', 'cache', 'summary', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    run(args.source, args.cache, args.summary, args.output)
