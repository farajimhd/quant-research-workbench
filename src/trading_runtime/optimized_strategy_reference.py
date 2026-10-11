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


def read_reference(root: str | Path) -> OptimizationReference:
    """Require every frozen session, its receipt hash and the exact winner.

    The immutable winner hash is the trust anchor. Reference files are not
    permissible substitutes for database bars, scanner rows or feature inputs.
    """
    root = Path(root)
    manifest = json.loads((root / 'freeze.json').read_text(encoding='utf-8-sig'))
    if (manifest['generation'] != GENERATION or manifest['training_sessions'] != 30
            or manifest['validation_opened']
            or manifest['winner_sha256'] != WINNER_SHA256):
        raise ValueError('Unexpected optimization reference identity')
    hashes = manifest['files_sha256']
    for name, checksum in hashes.items():
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('Unsafe reference path')
        if sha256((root / relative).read_bytes()).hexdigest() != checksum:
            raise ValueError(f'Frozen reference changed: {name}')
    if hashes.get('winner.json') != WINNER_SHA256:
        raise ValueError('Missing frozen winner seal')
    seal = json.loads((root / 'complete.json').read_text())
    days = seal['training_days']
    if len(days) != 30 or len(set(days)) != 30 or seal['validation_opened']:
        raise ValueError('Expected all 30 unique training sessions')
    ranking = json.loads((root / 'ranking.json').read_text())
    candidate = ranking[0]['candidate']
    if type(candidate) is not int or candidate < 0:
        raise ValueError('Invalid winner population index')
    sessions = {}
    execution = None
    for day in days:
        name = f'{day}/receipt.json'
        if name not in hashes:
            raise ValueError(f'Missing frozen session seal: {day}')
        receipt = json.loads((root / name).read_text())
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
        sessions[day] = {name: values[candidate] for name, values in metrics.items()}
    return OptimizationReference(GENERATION, WINNER_SHA256, candidate,
        json.loads((root / 'winner.json').read_text()), execution, sessions)


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
                    or not actual.get('full_session')
                    or not actual.get('market_cursor_verified')
                    or type(actual.get('verified_sequence')) is not int
                    or actual['verified_sequence'] < 1):
                raise ValueError(f'Unverified or mismatched app comparison: {day}')
            metrics = actual['metrics']
            if any(type(value) not in (int, float, bool) or not isfinite(value)
                   for value in metrics.values()):
                raise ValueError(f'Non-finite or non-numeric app metrics: {day}')
            missing = set(expected) - set(metrics)
            row.update(app=actual, status='incomplete_metrics' if missing else 'compared',
                       missing_metrics=sorted(missing),
                       differences={name: metrics[name] - value for name, value in expected.items()
                                    if name in metrics and name != 'terminal_valid'})
        rows.append(row)
    return rows
