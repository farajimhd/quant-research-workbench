"""Causal, read-only load-time consolidation of the experimental level book."""
from hashlib import sha256
from math import fsum, isfinite

CONTRACT = 'merged-point-minmax-v1'
DEFAULT_THRESHOLD = 0.80


def merge_levels(levels):
    output = []
    for side in (1, -1):
        ordered = sorted((r for r in levels if r['side'] == side),
                         key=lambda r: (r['lower'], r['upper'], r['unified_level_id']))
        groups = []
        for row in ordered:
            if not groups or row['lower'] > groups[-1][1]:
                groups.append(([row], row['upper']))
            else:
                members, upper = groups[-1]
                members.append(row)
                groups[-1] = (members, max(upper, row['upper']))
        for members, upper in groups:
            ids = sorted(r['unified_level_id'] for r in members)
            identity = sha256(('|'.join([CONTRACT, str(side), *ids])).encode()).hexdigest()[:24]
            output.append(dict(members[0], unified_level_id='merged:'+identity,
                lower=min(r['lower'] for r in members), upper=upper,
                price=fsum(r['price'] for r in members)/len(members),
                prominence=fsum(r['prominence'] for r in members)/len(members),
                created_at_ms=max(r['created_at_ms'] for r in members),
                confirmed_at_ms=max(r['confirmed_at_ms'] for r in members),
                lifecycle='active' if all(r['lifecycle']=='active' for r in members) else 'mixed',
                member_count=len(members), load_contract=CONTRACT,
                timeframes=sorted({t for r in members for t in r.get('timeframes', [])})))
    return sorted(output, key=lambda r: (r['price'], r['side'], r['unified_level_id']))


def calibration(seed, close, ratio=1.0):
    if not isfinite(close) or close <= 0 or not isfinite(ratio) or ratio < 0:
        raise ValueError('Normalization requires a positive prior close and nonnegative range ratio')
    lower, upper = max(0, close*(1-ratio)), close*(1+ratio)
    scores = [r['prominence'] for r in merge_levels(seed) if lower <= r['price'] <= upper]
    return dict(prior_close=close, range_ratio=ratio, lower=lower, upper=upper,
                p_min=min(scores) if scores else None, p_max=max(scores) if scores else None,
                normalization_status='ready' if scores else 'empty_seed')


def transform(levels, basis):
    output = []
    for row in merge_levels(levels):
        if not basis['lower'] <= row['price'] <= basis['upper']:
            continue
        lo, hi = basis['p_min'], basis['p_max']
        normalized = None if lo is None else .5 if hi == lo else min(1., max(0., (row['prominence']-lo)/(hi-lo)))
        output.append(dict(row, p_norm=normalized, normalization_status=basis['normalization_status']))
    return {'unified_levels': output, 'normalization': basis, 'load_contract': CONTRACT}
