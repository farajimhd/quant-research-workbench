"""Replay URG's failed day without writing to the campaign or changing MLE rules.

Exit codes: 0 replay completed, 1 model failure captured, 2 diagnostic/preflight
failure, 130 interrupted. Each invocation writes a separate diagnostic report.
"""
import os
import sys

os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
sys.dont_write_bytecode = True
for _name in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS'):
    os.environ[_name] = '1'

import argparse
from collections import OrderedDict
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
from pathlib import Path
import platform
import time
import traceback
import uuid


def json_safe(value):
    """Keep nonfinite optimizer diagnostics explicit, without invalid JSON."""
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    if hasattr(value, 'tolist'):
        return json_safe(value.tolist())
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    return value


@contextmanager
def capture_fits(streaming, band, center):
    """Observe the existing functions and return their original results unchanged."""
    original_partition, original_fit, original_minimize = streaming.partition, band.fit, center.minimize
    state = dict(last_partition=None, fit_calls=0, optimizer_calls=0, unsuccessful_trials=0)
    evidence = OrderedDict()
    active = []

    def minimize(*args, **kwargs):
        result = original_minimize(*args, **kwargs)
        state['optimizer_calls'] += 1
        state['unsuccessful_trials'] += not bool(result.success)
        if active:
            active[-1]['trials'].append(json_safe(dict(
                initial=args[1] if len(args) > 1 else kwargs.get('x0'),
                bounds=kwargs.get('bounds'), method=kwargs.get('method'),
                success=bool(result.success), message=str(result.message),
                status=getattr(result, 'status', None), objective=result.fun,
                parameters=result.x, gradient=getattr(result, 'jac', None),
                iterations=getattr(result, 'nit', None), evaluations=getattr(result, 'nfev', None))))
        return result

    def fit(prices, resolution):
        record = dict(prices=list(prices), resolution=resolution, trials=[])
        active.append(record)
        try:
            result = original_fit(prices, resolution)
            record['result'] = deepcopy(result)
            return result
        finally:
            active.pop()
            state['fit_calls'] += 1
            key = (tuple(prices), resolution)
            evidence[key] = record
            evidence.move_to_end(key)
            while len(evidence) > 4096:
                evidence.popitem(last=False)

    def partition(observations, coverage):
        state['last_partition'] = dict(observations=deepcopy(observations), coverage=coverage)
        result = original_partition(observations, coverage)
        state['last_partition']['components'] = deepcopy(result)
        state['last_partition']['component_fits'] = [deepcopy(evidence.get(
            (tuple(o['price'] for o in obs), min(o['resolution'] for o in obs)),
            dict(note='Fit came from a cache predating diagnostic capture')))
            for obs, _ in result if obs]
        return result

    streaming.partition, band.fit, center.minimize = partition, fit, minimize
    try:
        yield state
    finally:
        streaming.partition, band.fit, center.minimize = original_partition, original_fit, original_minimize


def replay(c, streaming, band, center, campaign, report):
    print('Preflight: pinned code, numerical runtime, checkpoint and source receipts', flush=True)
    plan = c.checked_plan(campaign)
    root = c.paths(campaign, 'URG')
    paths = [campaign / 'plan.json', root / 'source-plan.json',
             root / 'books/2025-06-06.json.gz', root / 'receipts/2025-06-06.json']
    fingerprints = {str(p): sha256(p.read_bytes()).hexdigest() for p in paths}
    prior = c.verified_book(paths[2])
    receipt, source = c.read(paths[3]), c.read(paths[1])
    if source['plan_hash'] != plan['plan_hash'] or receipt['checkpoint_hash'] != prior['checkpoint_hash'] or receipt['state'] != 'complete':
        raise ValueError('Prior receipt/checkpoint/plan mismatch')
    day = '2025-06-09'
    metadata = next(d for d in source['days'] if d['source_date'] == day)
    prior_metadata = next(d for d in source['days'] if d['source_date'] == prior['session'])
    if receipt['source_hash'] != c.source_hash(prior_metadata, plan['rules']):
        raise ValueError('Prior checkpoint source receipt mismatch')
    c.load_env_files(c.discover_clickhouse_env_files(), verbose=False)
    if c.query(c.RULE_SQL) != plan['rules']:
        raise ValueError('Canonical trade-condition policy changed')
    print('Source: reading only URG 2025-06-09 from canonical ClickHouse', flush=True)
    bars, audit = c.decode(c.query(c.bars_sql('URG', day, plan['rules'], metadata)))
    current = c.query("SELECT * FROM market_sip_compact.events_ordinal_continuity FINAL WHERE ticker='URG' AND source_date='2025-06-09'")
    if current != [metadata]:
        raise ValueError('Canonical day differs from frozen source metadata')
    report.update(plan_hash=plan['plan_hash'], parent_hash=prior['checkpoint_hash'],
                  input_file_hashes=fingerprints, source_hash=c.source_hash(metadata, plan['rules']),
                  bars=len(bars), bar_hash=c.digest(bars), source_audit=audit,
                  reference_laptop_bar_hash='be18fcd5c78c755dea5f24aeed0a12c00282bbb31c918b8936dc9fefa3010951')
    actions = [s for s in source['splits'] if prior['session'] < s['execution_date'] <= day]
    engine = c.fit_day(prior, 'URG', day, bars, actions)
    print(f'Replay: {len(bars):,} bars; prior session {prior["session"]}', flush=True)
    started = time.monotonic()
    with capture_fits(streaming, band, center) as capture:
        try:
            for index, bar in enumerate(bars):
                report.update(bar_index=index, bar=bar)
                engine.update(bar, observed_at=bar['t'])
            report.update(status='completed', bars_processed=len(bars))
        except Exception as exc:
            report.update(status='model_failure', error=str(exc), traceback=traceback.format_exc(),
                          fit_evidence=deepcopy(capture['last_partition']))
        finally:
            report['fit_counts'] = {k: v for k, v in capture.items() if k != 'last_partition'}
            report['replay_seconds'] = time.monotonic() - started
    if any(sha256(Path(p).read_bytes()).hexdigest() != h for p, h in fingerprints.items()):
        raise ValueError('Campaign inputs changed during diagnostic replay')
    report['campaign_inputs_unchanged'] = True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--code', type=Path, default=Path('D:/TradingML/codes/level-book-v7-workstation-ab7cdb24'))
    parser.add_argument('--campaign', type=Path, default=Path('D:/TradingML/runtimes/level-book-v7/all-tradable-20250101-20260912-mle-v1'))
    parser.add_argument('--output', type=Path, default=Path('D:/TradingML/runtimes/level-book-v7/urg-diagnosis-20260913'))
    args = parser.parse_args(argv)
    runtime = Path('D:/TradingML/runtimes').resolve()
    output = args.output.resolve()
    if not runtime.is_dir() or not output.is_relative_to(runtime):
        parser.error('Output must be under the available D:/TradingML/runtimes directory')
    if output.is_relative_to(args.campaign.resolve()):
        parser.error('Diagnostic output must be outside the campaign directory')
    output.mkdir(parents=True, exist_ok=True)
    report = dict(version='v7-urg-diagnostic-1', created_at=datetime.now(timezone.utc).isoformat(),
                  host=platform.node(), python=sys.version, executable=sys.executable,
                  code=str(args.code.resolve()), campaign=str(args.campaign.resolve()),
                  diagnostic_sha256=sha256(Path(__file__).read_bytes()).hexdigest(),
                  thread_environment={k: os.environ[k] for k in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS')})
    c = None
    try:
        if not (args.code / 'research/level_book/v7/campaign.py').is_file():
            raise ValueError('Pinned campaign source directory unavailable; use --code')
        sys.path.insert(0, str(args.code.resolve()))
        from research.level_book.v7 import campaign as c
        from src.market_engine import streaming_level_book, reaction_band, reaction_center
        import numpy as np
        import scipy
        report.update(numpy=np.__version__, scipy=scipy.__version__, numpy_configuration=np.show_config(mode='dicts'),
                      cpu_features=getattr(np._core._multiarray_umath, '__cpu_features__', {}))
        replay(c, streaming_level_book, reaction_band, reaction_center, args.campaign, report)
    except KeyboardInterrupt:
        report.update(status='interrupted')
    except Exception as exc:
        report.update(status='diagnostic_failure', error=str(exc), traceback=traceback.format_exc())
    finally:
        if c is not None:
            for client in c.CLIENTS.values():
                client.close()
    path = output / f'diagnostic-{platform.node()}-{uuid.uuid4().hex}.json'
    with path.open('x', encoding='utf-8') as stream:
        json.dump(json_safe(report), stream, allow_nan=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    print(f'Result: {report["status"]}', flush=True)
    if 'error' in report:
        print('Reason:', report['error'], flush=True)
    print('Report:', path, flush=True)
    return {'completed': 0, 'model_failure': 1, 'diagnostic_failure': 2, 'interrupted': 130}[report['status']]


if __name__ == '__main__':
    raise SystemExit(main())
