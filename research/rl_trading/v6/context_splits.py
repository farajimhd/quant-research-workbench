"""Immutable, opening-time split receipts and non-mutating feature views."""
from datetime import date, datetime
import json
import math
import re
from pathlib import Path
import numpy as np

from research.rl_trading.v1.common import digest, exclusive
from research.rl_trading.v1.reference_features import opening
from research.rl_trading.v6.features import SCALAR_NAMES

VERSION = 'v6-prior-context-opening-splits-v1'


def factors(rows, identities, previous_day, day):
    """Return new shares per old share; duplicate revisions never compound."""
    result = dict.fromkeys(identities, 1.)
    seen = {}
    for row in rows:
        identity = row['listing_id']
        effective = date.fromisoformat(str(row['execution_date']))
        if identity not in result or not previous_day < effective <= day:
            raise ValueError('Split outside bound identity/session interval')
        if 'inserted_at' in row:
            stamp = re.fullmatch(r'(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?', str(row['inserted_at']))
            if stamp is None:
                raise ValueError('Invalid canonical split availability timestamp')
            seconds = datetime.fromisoformat(stamp[1])
            cutoff = datetime.fromisoformat(opening(day))
            if seconds > cutoff or (seconds == cutoff and int(stamp[2] or '0') > 0):
                raise ValueError('Split metadata was unavailable at session opening')
        before, after = float(row['split_from']), float(row['split_to'])
        if not all(math.isfinite(x) and x > 0 for x in (before, after)):
            raise ValueError('Invalid split quantities')
        ratio = after / before
        if not math.isfinite(ratio) or ratio <= 0:
            raise ValueError('Invalid split factor')
        key = identity, effective
        if key in seen:
            if not math.isclose(seen[key], ratio, rel_tol=1e-12):
                raise ValueError('Conflicting split revisions')
            continue
        seen[key] = ratio
        result[identity] *= ratio
        if not math.isfinite(result[identity]) or result[identity] <= 0:
            raise ValueError('Invalid compounded split factor')
    return result


def receipt(runtime, plan):
    """Freeze canonical metadata, including negative evidence, once per plan.

    Late metadata never silently changes a previously admitted session.
    A replacement receipt requires an explicit new version/admission.
    """
    from research.rl_trading.v1 import arte_source
    from research.rl_trading.v1.arte_sql import query, literal
    from research.mlops.clickhouse import discover_clickhouse_env_files
    from research.mlops.env import load_env_files
    identities = sorted(plan['census'])
    day, prior = date.fromisoformat(plan['day']), date.fromisoformat(plan['previous_day'])
    binding = dict(version=VERSION, plan_hash=plan['hash'], day=str(day),
                   previous_day=str(prior), cutoff=opening(day), identities=identities)
    root = Path(runtime) / 'rl-v6-context-splits' / VERSION
    root.mkdir(parents=True, exist_ok=True)
    path = root / (plan['hash'] + '.json')
    with exclusive(path.with_suffix('.lock')):
        if path.exists():
            saved = json.loads(path.read_text())
            if saved.get('binding') != binding or saved.get('hash') != digest(
                    {k: v for k, v in saved.items() if k != 'hash'}):
                raise ValueError('Split context receipt binding/hash changed')
        else:
            load_env_files(discover_clickhouse_env_files(), verbose=False)
            reader = arte_source.reader(threads=1)
            rows = []
            try:
                for start in range(0, len(identities), 256):
                    names = ','.join(literal(x) for x in identities[start:start+256])
                    rows.extend(query(reader,
                        'SELECT listing_id,security_id,symbol_id,execution_date,split_from,split_to,'
                        'inserted_at,source_content_sha256 FROM q_live.market_stock_split_v1 FINAL '
                        f'WHERE listing_id IN ({names}) AND execution_date>toDate({literal(prior)}) '
                        f'AND execution_date<=toDate({literal(day)}) '
                        f'AND inserted_at<=toDateTime64({literal(opening(day))},9,\'UTC\') '
                        'ORDER BY listing_id,execution_date,inserted_at'))
            finally:
                reader.close()
            # Validate before publication, including conflicts and ratios.
            factors(rows, identities, prior, day)
            saved = dict(binding=binding, rows=rows)
            saved['hash'] = digest(saved)
            temporary = path.with_suffix('.tmp')
            temporary.write_text(json.dumps(saved, indent=2), encoding='utf-8')
            temporary.replace(path)
    return factors(saved['rows'], identities, prior, day), saved['hash']


class SplitScalarView:
    """Array-compatible bounded row reads; original memmaps remain untouched."""
    def __init__(self, source, offsets, ratios, *, prior=False):
        self.source, self.shape, self.dtype = source, source.shape, source.dtype
        self.prior = prior
        self.intervals = [(a, b, ratios.get(name, 1.)) for name, (a, b) in offsets.items()
                          if ratios.get(name, 1.) != 1. and b > a]

    def __len__(self):
        return len(self.source)

    def __getitem__(self, key):
        if not self.intervals:
            return self.source[key]
        if isinstance(key, tuple):
            return self[key[0]][(...,) + key[1:]]
        indices = (np.arange(*key.indices(len(self))) if isinstance(key, slice)
                   else np.asarray(key))
        if indices.dtype == bool:
            indices = np.flatnonzero(indices)
        indices = np.where(indices < 0, indices + len(self), indices)
        output = np.array(self.source[key], copy=True)
        single = output.ndim == 1
        values = output[None] if single else output
        indices = np.atleast_1d(indices)
        for left, right, ratio in self.intervals:
            selected = (indices >= left) & (indices < right)
            if not selected.any():
                continue
            if self.prior:
                valid = selected & (values[:, SCALAR_NAMES.index('bar_price_valid')] == 1)
                values[valid, :4] -= math.log(ratio)
                for name in ('log_volume', 'log_volume_60s', 'log_float_shares', 'log_shares_outstanding'):
                    column = SCALAR_NAMES.index(name)
                    values[selected, column] = np.log1p(np.expm1(values[selected, column].astype(np.float64)) * ratio)
                # Relative VWAP/EMA/MACD/ATR and V7 distances are scale invariant.
                # Trades, clocks, masks and as-of reference ages are unchanged.
            else:
                column = SCALAR_NAMES.index('log_rvol_10s_prev_session')
                values[selected, column] = np.log1p(np.expm1(values[selected, column].astype(np.float64)) / ratio)
        if not np.isfinite(values).all():
            raise ValueError('Split adjustment produced nonfinite features')
        output.setflags(write=False)
        return output

    def __array__(self, dtype=None, copy=None):
        return np.asarray(self[:], dtype=dtype)
