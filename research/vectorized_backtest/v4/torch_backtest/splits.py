"""Causal share-basis conversion for immutable V6 feature views.

Only opening-known actions with previous_day < execution_date <= day apply.
Raw market/broker prices are never changed. Indicators and V7 distances are
ratios and remain invariant under a uniform change of share basis. Historical
fundamental observations remain their original point-in-time observations.
"""
from datetime import date
import math
import numpy as np
import json
from pathlib import Path

VERSION = 'v4-opening-known-share-basis-v1'


def load_basis(path, bank, previous, mapping, *, rvol_only=False):
    from .runtime import file_hash
    from research.rl_trading.v1.common import digest
    value = json.loads(Path(path).read_text(encoding='utf-8'))
    payload = {k: v for k, v in value.items() if k != 'hash'}
    if value.get('hash') != digest(payload) or value.get('version') != VERSION or value.get('status') != 'complete':
        raise ValueError('Invalid split evidence certificate')
    if value['day'] != bank.day['day'] or value['bank_certificate_sha256'] != bank.certificate_hash:
        raise ValueError('Split evidence not bound to current bank')
    if not rvol_only and value['previous_bank_certificate_sha256'] != (previous.certificate_hash if previous else None):
        raise ValueError('Split evidence not bound to previous bank')
    if set(value['listings']) != set(mapping):
        raise ValueError('Incomplete split evidence listing coverage')
    result = {}
    for identity, evidence in value['listings'].items():
        if evidence['ticker'] != mapping[identity]:
            raise ValueError('Split evidence listing identity mismatch')
        rows = evidence['splits']
        # Recompute rather than trusting serialized factors.
        result[identity] = dict(rvol_price_factor=price_factor(rows, bank.day['previous_day'], bank.day['day']) if bank.day.get('previous_day') else 1.)
        if not rvol_only:
            result[identity]['history_price_factor']=price_factor(rows, previous.day['day'], bank.day['day']) if previous else 1.
        if result[identity] != {key: evidence[key] for key in result[identity]}:
            raise ValueError('Split factor arithmetic mismatch')
    return result, file_hash(path)


def price_factor(rows, previous_day, day):
    previous = date.fromisoformat(str(previous_day))
    current = date.fromisoformat(str(day))
    if previous >= current:
        raise ValueError('Split context must precede current session')
    factors = {}
    for row in rows:
        effective = date.fromisoformat(str(row['execution_date']))
        if not previous < effective <= current:
            continue
        before, after = float(row['split_from']), float(row['split_to'])
        if not all(math.isfinite(v) and v > 0 for v in (before, after)):
            raise ValueError('Invalid certified split ratio')
        factor = before / after
        if not math.isfinite(factor) or factor <= 0:
            raise ValueError('Invalid certified split factor')
        if effective in factors and not math.isclose(factors[effective], factor, rel_tol=1e-12, abs_tol=0):
            raise ValueError('Conflicting certified split ratios')
        factors[effective] = factor
    result = math.prod(factors.values())
    if not math.isfinite(result) or result <= 0:
        raise ValueError('Invalid accumulated split factor')
    return result


def history_view(features, factor):
    """Restate prior OHLC/volume; do not mutate a bank or availability masks."""
    if not math.isfinite(factor) or factor <= 0:
        raise ValueError('Invalid share basis')
    out = np.array(features, copy=True)
    if factor == 1:
        return out
    price_valid = out[:, 35] == 1
    extremes_valid = out[:, 36] == 1
    for column in (0, 3):
        out[price_valid, column] += math.log(factor)
    for column in (1, 2):
        out[extremes_valid, column] += math.log(factor)
    for column in (8, 10):
        out[:, column] = np.log1p(np.expm1(out[:, column].astype(np.float64)) / factor)
    if not np.isfinite(out).all():
        raise ValueError('Share-basis conversion overflow')
    return out


def rvol_view(features, factor):
    """Same-clock RVOL denominator uses prior volume on current share basis."""
    out = np.array(features, copy=True)
    if not math.isfinite(factor) or factor <= 0:
        raise ValueError('Invalid RVOL share basis')
    known = out[:, 13] == 1
    out[known, 12] = np.log1p(np.expm1(out[known, 12].astype(np.float64)) * factor)
    if not np.isfinite(out).all():
        raise ValueError('RVOL share-basis conversion overflow')
    return out
