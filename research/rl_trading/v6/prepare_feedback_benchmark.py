"""Prepare authenticated, sharded earlier-clock features without target changes."""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
os.environ.setdefault('POLARS_MAX_THREADS', '2')
os.environ.setdefault('OMP_NUM_THREADS', '2')
import argparse
from datetime import date
import gc
import json
from pathlib import Path
import time
import traceback

import numpy as np
import polars as pl
import torch

from research.rl_trading.v1.common import file_hash
from research.rl_trading.v6 import saved_label_audit as authority
from research.rl_trading.v6.session_data import open_session
from research.rl_trading.v6.prepare_enriched_benchmark import origin, prepare_day
from research.rl_trading.v6.feedback_inputs import prefix_windows, clock_grid
from research.rl_trading.v6.causal_feedback import VERSION, WARMUP
from research.rl_trading.v6.run_architecture_benchmark import write, save


def quote_prefix(reader, repair, session, keys, names):
    from research.rl_trading.v1.arte_source import load_build
    from research.rl_trading.v1.arte_sql import literal
    from research.rl_trading.v6.environment_source import ArteExecutionSource, EXECUTION_SCHEMA
    from research.rl_trading.v6.sparse_coverage import certify_source
    day = session.day.isoformat(); ledger = repair / 'build-ledger-v2.sqlite3'
    tickers = sorted({names[k[2][len(day)+1:].split(':pair:')[0]] for k in keys})
    build = load_build(repair/'bars/latest.json', ledger, [date.fromisoformat(day)], tickers)
    plan = json.loads((session.root/'plan.json').read_text())
    if build['build_id'] != plan['source_build_id'] or build['definition_hash'] != plan['source_definition_hash']:
        raise ValueError('Fixed candle and quote source differ')
    end = max(k[1] for k in keys)
    execution = ArteExecutionSource(reader, build, ledger, date.fromisoformat(day), end_us=end)
    _, certificate = certify_source(execution, tickers)
    scopes = ','.join(f'({literal(t)},toUUID({literal(execution.attempts[t])}))' for t in tickers)
    first = max(0, (min(k[1] for k in keys)-execution.origin-1_000_000)//100_000)
    last = (end-execution.origin)//100_000-1
    schema = {k:v for k,v in EXECUTION_SCHEMA.items() if k not in ('quote_count','extrema_count')}
    frame = execution._frame('SELECT '+','.join(schema)+
        f' FROM arte.liquidity_100ms_v1 WHERE build_id={literal(build["build_id"])} AND session_date=toDate({literal(day)}) AND resolution_ms=100 AND bucket_index BETWEEN {first} AND {last} AND (ticker,attempt_id) IN ({scopes}) ORDER BY ticker,bucket_index', schema)
    return frame.with_columns((pl.lit(execution.origin)+(pl.col('bucket_index')+1)*100_000).alias('bucket_end_us')), certificate


def prepare_prefix_day(session, keys, reference, names, quotes):
    targets = np.array([k[1] for k in keys], np.int64)
    clocks, available, reset = clock_grid(targets, origin(session.day))
    n, steps = clocks.shape
    groups = {}
    for row, k in enumerate(keys):
        groups.setdefault(k[2][len(k[0])+1:].split(':pair:')[0], []).append(row)
    sequence = {}
    for identity, rows in groups.items():
        part = prefix_windows(session, identity, targets[rows], reference[rows].numpy(), origin(session.day))
        for name, value in part.items():
            if name not in sequence: sequence[name] = torch.empty((n, *value.shape[1:]), dtype=value.dtype)
            sequence[name][rows] = value
    # Prefix rows contain no labels; the original identity only selects the
    # certified listing. Duplicate decision clocks are intentionally shared.
    expanded = [tuple([k[0], int(clock), *k[2:]]) for k, row in zip(keys, clocks) for clock in row]
    valid_indices = np.flatnonzero(available.reshape(-1))
    features = prepare_day(session, [expanded[i] for i in valid_indices], names, quotes)
    for name, values in features.items():
        flat = torch.zeros((n*steps, *values.shape[1:]), dtype=values.dtype)
        flat[valid_indices] = values
        sequence[name] = flat.reshape(n, steps, *values.shape[1:])
    return sequence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--panel', type=Path, default=Path('D:/TradingML/runtimes/v6-enriched-feature-panel-v2-20261012'))
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(); out = args.output.resolve(); runtime = Path('D:/TradingML/runtimes').resolve()
    if not runtime.is_dir() or not out.is_relative_to(runtime) or out.exists():
        raise ValueError('Fresh explicit runtime output required')
    out.mkdir(); started = time.perf_counter()
    import psutil
    psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS); torch.set_num_threads(2)
    proof = json.loads((args.panel/'complete.json').read_text())
    audit = json.loads((args.panel/'panel-audit.json').read_text())
    if (proof['status'] != 'prepared' or proof['sealed_labels_read'] or audit.get('sealed_labels_read') is not False
            or audit.get('source_days') != 8 or any(audit.get(role, {}).get('original_inputs_targets_exact') is not True
            for role in ('train', 'validation'))):
        raise ValueError('Audited enriched panel required')
    benchmark = Path(proof['source_benchmark']); manifest = json.loads((benchmark/'manifest.json').read_text())
    if file_hash(benchmark/'manifest.json') != proof['source_manifest_sha256']:
        raise ValueError('Original benchmark manifest changed')
    source = Path(manifest['source_runtime']); os.environ['RL_V6_LABEL_AUDIT_RUNTIME'] = str(source)
    from research.mlops.env import discover_env_files, load_env_files
    from research.rl_trading.v1.arte_sql import ArteReader
    load_env_files(discover_env_files(Path.cwd()), verbose=False)
    if file_hash(args.panel/'normalization.json') != proof['normalization_sha256']:
        raise ValueError('Original enrichment normalizers changed')
    write(out/'manifest.json', dict(version=VERSION, panel=str(args.panel.resolve()),
        panel_sha256=file_hash(args.panel/'complete.json'), warmup=WARMUP,
        normalization_sha256=proof['normalization_sha256'], sealed_labels_read=False))
    reader = ArteReader(threads=2); shards = []
    try:
        for role in ('train','validation'):
            path = args.panel/(role+'.pt')
            if file_hash(path) != proof[role+'_sha256']: raise ValueError('Enriched panel changed')
            data = torch.load(path, weights_only=False)
            for day in sorted({k[0] for k in data['keys']}):
                if role == 'validation' and day not in ('2026-08-24','2026-08-25'):
                    raise ValueError('Frozen public validation dates only')
                if psutil.virtual_memory().available < 8*1024**3: raise RuntimeError('Host reserve below8GiB')
                write(out/'progress.json', dict(phase='preparing', role=role, day=day, completed=len(shards), active=1))
                rows = [i for i,k in enumerate(data['keys']) if k[0] == day]; keys = [data['keys'][i] for i in rows]
                _, entry, _, _, bankroot, _ = authority.session(day)
                binding = next(b for b in proof['bindings'] if b['day']==day and b['role']==role)
                if entry['bank_certificate_sha256'] != binding['bank']: raise ValueError('Frozen bank source changed')
                session = open_session(bankroot, runtime_root=source, previous_root=authority.mapped(entry['previous_root']))
                if session.role != ('train' if role=='train' else 'development') or session.context_split_receipt_sha256 != binding['split']:
                    raise ValueError('Role or split basis changed')
                names = authority.saved_symbols(str(session.root), session.source_certificate_sha256)
                expanded = [tuple([k[0], max(origin(day), k[1]-WARMUP*1_000_000), *k[2:]]) for k in keys] + keys
                quotes, certificate = quote_prefix(reader, source/'rl-v6-reporting-repair-20261002', session, expanded, names)
                sequence = prepare_prefix_day(session, keys, data['held'][rows], names, quotes)
                for name in ('raw','present','held','multi','multi_present','liquidity','market'):
                    if not torch.equal(sequence[name][:,-1], data[name][rows]):
                        raise ValueError('Final target input differs from Group A: '+name)
                shard = out/(role+'-'+day+'.pt')
                save(shard, dict(sequence=sequence, original_rows=rows, keys=keys))
                receipt = dict(role=role, day=day, path=str(shard), sha256=file_hash(shard), rows=len(rows),
                    bank=binding['bank'], split=binding['split'], quote_certificate=certificate,
                    query_rows=quotes.height, final_inputs_exact=True)
                write(shard.with_suffix('.json'), receipt); shards.append(receipt)
                del session, sequence, quotes; gc.collect()
            del data; gc.collect()
        write(out/'complete.json', dict(version=VERSION, status='prepared', manifest_sha256=file_hash(out/'manifest.json'),
            shards=shards, seconds=time.perf_counter()-started, sealed_labels_read=False, final_inputs_exact=True))
        write(out/'progress.json', dict(phase='prepared', active=0, completed=len(shards)))
    except BaseException as error:
        write(out/'failure.json', dict(error=str(error), traceback=traceback.format_exc()))
        raise
    finally: reader.close()


if __name__ == '__main__': main()
