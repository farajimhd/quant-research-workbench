"""Read-only, hash-bound optimization reference; never an app market source.

The app must consume certified ARTE products and use Portfolio/OMS/broker
execution. This module only supplies the frozen program specification and
comparison expectations. It cannot publish a strategy or launch a backtest.
"""
from dataclasses import dataclass
from hashlib import sha256
import json
from math import isfinite
from pathlib import Path
from typing import Mapping
from datetime import date, datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal, localcontext
from types import MappingProxyType
from io import BytesIO


WINNER_SHA256 = '57c03ac8aba6e2e09df254d0ac615fce61efa63fe64754351f73c06fafde4d12'
GENERATION = 24
METRICS = frozenset(('net_pnl', 'drawdown', 'capital_dollar_seconds',
    'stop_risk_dollar_seconds', 'filled_batches', 'terminal_valid',
    'inactivity_fraction', 'add_count', 'reduce_count', 'worst_position_pnl',
    'closed_positions', 'winning_positions'))
_COUNTS = frozenset(('filled_batches', 'add_count', 'reduce_count',
    'closed_positions', 'winning_positions'))
_NONNEGATIVE = _COUNTS | {'drawdown', 'capital_dollar_seconds', 'stop_risk_dollar_seconds'}


def _validate_metrics(metrics, *, day, origin, complete=False):
    """Preserve search metric meanings, including its float-encoded counts."""
    if not isinstance(metrics, Mapping) or set(metrics) - METRICS:
        raise ValueError(f'Unknown {origin} comparison metrics: {day}')
    if complete and set(metrics) != METRICS:
        raise ValueError(f'Incomplete frozen metric contract: {day}')
    for name, value in metrics.items():
        if name == 'terminal_valid':
            if value is not True:
                raise ValueError(f'{origin} result lacks valid terminal accounting: {day}')
            continue
        if type(value) is bool or not _finite_metric(value):
            raise ValueError(f'Non-finite or non-numeric {origin} metrics: {day}')
        if name in _NONNEGATIVE and value < 0:
            raise ValueError(f'Negative {origin} comparison metric {name}: {day}')
        if name in _COUNTS and value != int(value):
            raise ValueError(f'Fractional {origin} comparison count {name}: {day}')
        if name == 'inactivity_fraction' and not 0 <= value <= 1:
            raise ValueError(f'Invalid {origin} inactivity fraction: {day}')
    if ('winning_positions' in metrics and 'closed_positions' in metrics
            and metrics['winning_positions'] > metrics['closed_positions']):
        raise ValueError(f'{origin} winning positions exceed closed positions: {day}')


def _finite_metric(value):
    return (value.is_finite() if type(value) is Decimal else
            type(value) in (int, float, bool) and isfinite(value))


def _metric_difference(actual, expected):
    if type(actual) is Decimal or type(expected) is Decimal:
        left, right = Decimal(str(actual)), Decimal(str(expected))
        with localcontext() as context:
            # Preserve the native decimal, including differences below Float64
            # resolution. Search floats retain their recorded decimal value.
            context.prec = max(80, max(left.adjusted(), right.adjusted())
                - min(left.as_tuple().exponent, right.as_tuple().exponent) + 2)
            return left - right
    return actual - expected


@dataclass(frozen=True)
class ReferenceClockWindow:
    requested_start: datetime
    first_clock: datetime
    last_clock: datetime
    clocks: int
    input_sha256: str
    clocks_sha256: str

    @property
    def metric_intervals(self):
        return self.clocks - 1


def _read_reference_clock_window(input_root, day, input_hash, *, validation_opened=False, role=None):
    """Read sealed clock metadata only; never supply app market observations."""
    import numpy as np
    if type(validation_opened) is not bool or role not in (None, 'training', 'validation'):
        raise ValueError('Explicit clock metadata role and validation flag required')
    target = (input_root / day).resolve()
    if not target.is_relative_to(input_root):
        raise ValueError('Frozen optimizer clock path escaped its declared authority')
    complete = (target / 'complete.json').read_bytes()
    if sha256(complete).hexdigest() != input_hash:
        raise ValueError(f'Frozen optimizer input identity changed: {day}')
    metadata = json.loads(complete)
    if (metadata['status'] != 'complete' or metadata['ready_for_replay'] is not True
            or metadata['validation_opened'] is not validation_opened
            or (role is not None and metadata.get('split', role) != role)
            or metadata['identity']['session']['day'] != day
            or metadata['identity']['top_n'] != 10 or metadata['identity']['window'] != 30):
        raise ValueError(f'Frozen optimizer clock metadata differs: {day}')
    content = (target / 'clocks.npy').read_bytes()
    clock_hash = metadata['files']['clocks.npy']
    if sha256(content).hexdigest() != clock_hash:
        raise ValueError(f'Frozen optimizer clocks changed: {day}')
    clocks = np.load(BytesIO(content), allow_pickle=False)
    if (clocks.dtype != np.dtype('int64') or clocks.ndim != 1 or not 2 <= len(clocks) <= 57601
            or metadata['clocks'] != len(clocks) or not np.all(np.diff(clocks) == 1)):
        raise ValueError(f'Frozen optimizer clocks lack a complete dense second grid: {day}')
    declared = metadata['identity']['session']
    start, end = (datetime.fromisoformat(declared[name]) for name in ('start', 'end'))
    if any(at.tzinfo is None or at.utcoffset() is None for at in (start, end)):
        raise ValueError(f'Frozen optimizer window lacks timezone authority: {day}')
    first, last = (datetime.fromtimestamp(int(clocks[index]), timezone.utc) for index in (0, -1))
    if start.astimezone(timezone.utc) + timedelta(seconds=1) != first or end.astimezone(timezone.utc) != last:
        raise ValueError(f'Frozen optimizer clocks differ from their declared session: {day}')
    return ReferenceClockWindow(start.astimezone(timezone.utc), first, last, len(clocks), input_hash, clock_hash)


@dataclass(frozen=True)
class OptimizationReference:
    generation: int
    winner_sha256: str
    candidate: int
    program: Mapping
    execution: Mapping
    sessions: Mapping
    clock_windows: Mapping | None = None
    candidate_envelope: object | None = None


def read_reference(root: str | Path, *, concurrency: int = 1, input_root=None) -> OptimizationReference:
    """Require every frozen session, its receipt hash and the exact winner.

    The immutable winner hash is the trust anchor. Reference files are not
    permissible substitutes for database bars, scanner rows or feature inputs.
    """
    if type(concurrency) is not int or not 1 <= concurrency <= 32:
        raise ValueError('Reference reads require bounded explicit concurrency')
    root = Path(root).resolve()
    manifest = json.loads((root / 'freeze.json').read_text(encoding='utf-8-sig'))
    if (manifest['generation'] != GENERATION or manifest['training_sessions'] != 30
            or manifest['validation_opened']
            or manifest['winner_sha256'] != WINNER_SHA256):
        raise ValueError('Unexpected optimization reference identity')
    hashes = manifest['files_sha256']
    if (type(hashes) is not dict
            or not {'winner.json', 'complete.json', 'ranking.json'} <= set(hashes)):
        raise ValueError('Missing frozen winner, ranking or generation seal')
    def read(item):
        name, checksum = item
        relative = Path(name)
        target = (root / relative).resolve()
        if relative.is_absolute() or '..' in relative.parts or not target.is_relative_to(root):
            raise ValueError('Unsafe reference path')
        content = target.read_bytes()
        if sha256(content).hexdigest() != checksum:
            raise ValueError(f'Frozen reference changed: {name}')
        return name, content
    if hashes.get('winner.json') != WINNER_SHA256:
        raise ValueError('Missing frozen winner seal')
    _, complete = read(('complete.json', hashes['complete.json']))
    seal = json.loads(complete)
    days = seal['training_days']
    if len(days) != 30 or len(set(days)) != 30 or seal['validation_opened']:
        raise ValueError('Expected all 30 unique training sessions')
    if any(type(day) is not str or date.fromisoformat(day).isoformat() != day for day in days):
        raise ValueError('Frozen session date is not canonical')
    expected_files = {'winner.json', 'complete.json', 'ranking.json'} | {
        f'{day}/receipt.json' for day in days}
    # The actual frozen campaign also seals these diagnostic companions.
    # They are verified below, but never supply strategy or market inputs.
    optional_diagnostics = {'diversity.json', 'timings.json'} | {
        f'{day}/timings.json' for day in days}
    if expected_files - set(hashes):
        raise ValueError('Missing frozen session seal')
    if (seal.get('selection_allowed') is not True
            or seal.get('session_receipts') != {day: hashes[f'{day}/receipt.json'] for day in days}):
        raise ValueError('Generation seal does not authorize the exact session receipts')
    if set(hashes) - expected_files - optional_diagnostics:
        raise ValueError('Unexpected reference file outside declared training comparison')
    items = [(name, checksum) for name, checksum in hashes.items() if name != 'complete.json']
    # Retain the exact verified bytes, rather than reopening mutable files.
    # The worker pool settles every submitted read before it can return.
    if concurrency == 1:
        contents = dict(read(item) for item in items)
    else:
        with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix='frozen-reference') as pool:
            futures = [pool.submit(read, item) for item in items]
            verified, errors = [], []
            for future in futures:
                try:
                    verified.append(future.result())
                except BaseException as failure:
                    errors.append(failure)
            if len(errors) == 1:
                raise errors[0]
            if errors:
                raise BaseExceptionGroup('Frozen reference file reads failed', errors)
            contents = dict(verified)
    ranking = json.loads(contents['ranking.json'])
    candidate = ranking[0]['candidate']
    if type(candidate) is not int or candidate < 0:
        raise ValueError('Invalid winner population index')
    sessions = {}
    execution = None
    input_hashes = {}
    for day in days:
        name = f'{day}/receipt.json'
        if name not in hashes:
            raise ValueError(f'Missing frozen session seal: {day}')
        receipt = json.loads(contents[name])
        if (receipt['day'] != day or not receipt['full_session']
                or receipt['validation_opened']
                or receipt['population_sha256'] != seal['population_sha256']):
            raise ValueError(f'Unexpected reference receipt: {day}')
        if execution is None:
            execution = receipt['execution']
        elif execution != receipt['execution']:
            raise ValueError('Reference execution varies across sessions')
        metrics = receipt['metrics']
        if not metrics or any(candidate >= len(values) for values in metrics.values()):
            raise ValueError('Winner index outside session metric population')
        if ('terminal_valid' not in metrics or metrics['terminal_valid'][candidate] is not True):
            raise ValueError(f'Winner lacks valid terminal accounting: {day}')
        sessions[day] = {name: values[candidate] for name, values in metrics.items()}
        _validate_metrics(sessions[day], day=day, origin='reference', complete=True)
        if input_root is not None:
            input_hashes[day] = receipt['identity']['input_sha256']
    windows = None
    if input_root is not None:
        input_root = Path(input_root).resolve()
        # These independent, hash-bound metadata reads use the same explicit
        # bounded pool. No feature, price, SQLite or raw SIP input is opened.
        with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix='reference-clocks') as pool:
            futures = {day: pool.submit(_read_reference_clock_window, input_root, day, checksum)
                       for day, checksum in input_hashes.items()}
            measured, errors = {}, []
            for day, future in futures.items():
                try: measured[day] = future.result()
                except BaseException as error: errors.append(error)
            if errors:
                if len(errors) == 1: raise errors[0]
                raise BaseExceptionGroup('Frozen optimizer clock reads failed', errors)
            windows = MappingProxyType(measured)
    return OptimizationReference(GENERATION, WINNER_SHA256, candidate,
        json.loads(contents['winner.json']), execution, sessions, windows)


def read_candidate_reference(root, serial, *, concurrency=1, input_roots=None):
    """Bridge every frozen candidate session into the native comparison contract.

    Only hash-bound clock metadata and clocks.npy are opened from input roots;
    these reference clocks never provide app market or feature observations.
    Each split declares one physical parent or an exact day-to-parent mapping;
    directory aliases do not authorize reads outside those declared parents.
    """
    from .optimized_candidate_evidence import read_frozen_candidate_evidence
    from .optimized_candidate import restore_candidate
    if type(concurrency) is not int or not 1 <= concurrency <= 8:
        raise ValueError('Candidate reference reads require bounded concurrency')
    if type(serial) is not int or serial < 1:
        raise ValueError('Explicit candidate serial required')
    root = Path(root).resolve(strict=True)
    manifest = read_frozen_candidate_evidence(root, workers=concurrency)
    envelopes = [value for value in manifest['candidate_envelopes'] if value['serial'] == serial]
    if len(envelopes) != 1:
        raise ValueError('Candidate serial absent or duplicated in frozen evidence')
    candidate = restore_candidate(envelopes[0])
    selected = manifest['sessions'][str(serial)]
    sessions, jobs = {}, []
    if input_roots is not None and set(input_roots) != {'training', 'validation'}:
        raise ValueError('Explicit input roots required for both source splits')
    authorities = {}
    if input_roots is not None:
        for role in ('training', 'validation'):
            days = {session['day'] for session in selected[role]}
            declared = input_roots[role]
            if isinstance(declared, Mapping):
                if set(declared) != days:
                    raise ValueError('Per-day input authorities require exact frozen split days')
                authorities[role] = {day: Path(parent).resolve(strict=True)
                                     for day, parent in declared.items()}
            else:
                parent = Path(declared).resolve(strict=True)
                authorities[role] = {day: parent for day in days}
    for role in ('training', 'validation'):
        for session in selected[role]:
            day = session['day']
            if day in sessions:
                raise ValueError('Candidate comparison sessions overlap')
            metrics = {name: value for name, value in session['metrics'].items() if name in METRICS}
            _validate_metrics(metrics, day=day, origin='reference', complete=True)
            sessions[day] = MappingProxyType(metrics)
            if input_roots is not None:
                relative = f'{role}/generation-{candidate.generation:04d}/{day}/receipt.json'
                raw = (root / relative).read_bytes()
                if sha256(raw).hexdigest() != manifest['sources'][relative]:
                    raise ValueError('Frozen candidate receipt changed during clock binding')
                receipt = json.loads(raw)
                checksum = receipt['identity']['input_sha256']
                input_root = authorities[role][day]
                # The source input certificate may retain validation_opened=False
                # even for a display-only optimizer receipt. Bind its exact flag.
                target = (input_root / day).resolve(strict=True)
                if not target.is_relative_to(input_root):
                    raise ValueError('Frozen optimizer clock path escaped its declared authority')
                certificate = (target / 'complete.json').read_bytes()
                if sha256(certificate).hexdigest() != checksum:
                    raise ValueError('Candidate input certificate differs from receipt')
                flag = json.loads(certificate)['validation_opened']
                jobs.append((day, input_root, checksum, flag, role))
    windows = None
    if jobs:
        def read(job):
            day, input_root, checksum, flag, role = job
            return day, _read_reference_clock_window(input_root, day, checksum,
                validation_opened=flag, role=role)
        with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix='candidate-clocks') as pool:
            windows = MappingProxyType(dict(pool.map(read, jobs)))
    return OptimizationReference(candidate.generation, candidate.program_hash, candidate.candidate,
        candidate.program, candidate.execution, MappingProxyType(sessions), windows, candidate)


async def load_reference(root, *, concurrency, input_root=None):
    """Read the all-session comparison reference off-loop; never launch research."""
    from src.backend.qmd_columnar_history_transport import _owned_worker
    return await _owned_worker(read_reference, root, concurrency=concurrency, input_root=input_root)


def _verified_comparison_clock(value):
    if type(value) is str:
        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('App comparison clock lacks explicit timezone authority')
    return value.astimezone(timezone.utc)


def verify_comparison_windows(window, actual):
    """Require the frozen execution bounds and search's first-clock exclusion."""
    if type(window) is not ReferenceClockWindow:
        raise ValueError('App comparison lacks a hash-bound optimizer clock window')
    execution, measurement = actual.get('execution_window'), actual.get('measurement_window')
    if (not isinstance(execution, Mapping) or not isinstance(measurement, Mapping)
            or _verified_comparison_clock(execution.get('start_at')) != window.requested_start
            or _verified_comparison_clock(execution.get('end_at')) != window.last_clock
            or _verified_comparison_clock(measurement.get('origin_at')) != window.first_clock
            or _verified_comparison_clock(measurement.get('end_at')) != window.last_clock
            or type(measurement.get('samples')) is not int or measurement['samples'] != window.metric_intervals
            or type(measurement.get('interval_ms')) is not int or measurement['interval_ms'] != 1000):
        raise ValueError('App comparison differs from its frozen execution or measurement window')


def compare_sessions(reference: OptimizationReference, app_results: Mapping) -> list[dict]:
    """Compare full-session, net-of-fee metrics without fabricating missing runs.

    Callers project verified app lifecycle/financial facts into these named
    metrics. An app result must identify its sealed strategy, run and verified
    journal sequence. Differences are diagnostic, not an equality verdict:
    actual portfolio and broker execution intentionally differ from search.
    """
    if set(app_results) - set(reference.sessions):
        raise ValueError('App comparison contains non-reference sessions')
    rows = []
    for day, expected in reference.sessions.items():
        _validate_metrics(expected, day=day, origin='reference')
        actual = app_results.get(day)
        row = dict(day=day, optimization=expected, app=None, differences=None,
                   status='not_run')
        if actual is not None:
            if (actual.get('winner_sha256') != reference.winner_sha256
                    or not actual.get('run_id') or not actual.get('strategy_digest')
                    or actual.get('full_session') is not True
                    or actual.get('market_cursor_verified') is not True
                    or type(actual.get('verified_sequence')) is not int
                    or actual['verified_sequence'] < 1):
                raise ValueError(f'Unverified or mismatched app comparison: {day}')
            if reference.clock_windows is not None:
                if day not in reference.clock_windows:
                    raise ValueError(f'Missing frozen optimizer clock window: {day}')
                verify_comparison_windows(reference.clock_windows[day], actual)
            metrics = actual['metrics']
            _validate_metrics(metrics, day=day, origin='app')
            missing = set(expected) - set(metrics)
            row.update(app=actual, status='incomplete_metrics' if missing else 'compared',
                       missing_metrics=sorted(missing),
                       differences={name: _metric_difference(metrics[name], value) for name, value in expected.items()
                                    if name in metrics and name != 'terminal_valid'})
        rows.append(row)
    return rows
