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
from datetime import date
from concurrent.futures import ThreadPoolExecutor


WINNER_SHA256 = '57c03ac8aba6e2e09df254d0ac615fce61efa63fe64754351f73c06fafde4d12'
GENERATION = 24


@dataclass(frozen=True)
class OptimizationReference:
    generation: int
    winner_sha256: str
    candidate: int
    program: Mapping
    execution: Mapping
    sessions: Mapping


def read_reference(root: str | Path, *, concurrency: int = 1) -> OptimizationReference:
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
    return OptimizationReference(GENERATION, WINNER_SHA256, candidate,
        json.loads(contents['winner.json']), execution, sessions)


async def load_reference(root, *, concurrency):
    """Read the all-session comparison reference off-loop; never launch research."""
    from src.backend.qmd_columnar_history_transport import _owned_worker
    return await _owned_worker(read_reference, root, concurrency=concurrency)


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
            metrics = actual['metrics']
            if any(type(value) not in (int, float, bool) or not isfinite(value)
                   for value in metrics.values()):
                raise ValueError(f'Non-finite or non-numeric app metrics: {day}')
            if ('terminal_valid' in metrics and metrics['terminal_valid'] is not True):
                raise ValueError(f'App result lacks valid terminal accounting: {day}')
            missing = set(expected) - set(metrics)
            row.update(app=actual, status='incomplete_metrics' if missing else 'compared',
                       missing_metrics=sorted(missing),
                       differences={name: metrics[name] - value for name, value in expected.items()
                                    if name in metrics and name != 'terminal_valid'})
        rows.append(row)
    return rows
