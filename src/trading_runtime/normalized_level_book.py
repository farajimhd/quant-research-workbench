"""Causal, read-only load-time consolidation of the experimental level book."""
from hashlib import sha256
from math import fsum, isfinite

CONTRACT = 'merged-point-minmax-v3'
MERGE_GAP_BPS = 20.0
DEFAULT_THRESHOLD = 0.80


def merge_levels(levels, proximity_bps=MERGE_GAP_BPS, contract=CONTRACT):
    if not isfinite(proximity_bps) or proximity_bps < 0:
        raise ValueError('Merge proximity must be finite nonnegative bps')
    output = []
    for side in (1, -1):
        ordered = sorted((r for r in levels if r['side'] == side),
                         key=lambda r: (r['lower'], r['upper'], r['unified_level_id']))
        groups = []
        for row in ordered:
            upper = groups[-1][1] if groups else 0
            allowance = proximity_bps / 10000 * (upper + row['lower']) / 2
            tolerance = 1e-12 if proximity_bps else 0.0
            if not groups or row['lower'] - upper > allowance + tolerance:
                groups.append(([row], row['upper']))
            else:
                members, upper = groups[-1]
                members.append(row)
                groups[-1] = (members, max(upper, row['upper']))
        for members, upper in groups:
            ids = sorted(r['unified_level_id'] for r in members)
            identity = sha256(('|'.join([contract, str(side), *ids])).encode()).hexdigest()[:24]
            output.append(dict(members[0], unified_level_id='merged:'+identity,
                lower=min(r['lower'] for r in members), upper=upper,
                price=fsum(r['price'] for r in members)/len(members),
                prominence=fsum(r['prominence'] for r in members)/len(members),
                created_at_ms=max(r['created_at_ms'] for r in members),
                confirmed_at_ms=max(r['confirmed_at_ms'] for r in members),
                lifecycle='active' if all(r['lifecycle']=='active' for r in members) else 'mixed',
                member_count=len(members), load_contract=contract,
                **({'merge_gap_bps': proximity_bps} if contract != 'merged-point-minmax-v1' else {}),
                timeframes=sorted({t for r in members for t in r.get('timeframes', [])})))
    return sorted(output, key=lambda r: (r['price'], r['side'], r['unified_level_id']))


def calibration(seed, close, ratio=1.0, *, contract=CONTRACT):
    if not isfinite(close) or close <= 0 or not isfinite(ratio) or ratio < 0:
        raise ValueError('Normalization requires a positive prior close and nonnegative range ratio')
    lower, upper = max(0, close*(1-ratio)), close*(1+ratio)
    proximity_bps = {'merged-point-minmax-v1': 0.0, 'merged-point-minmax-v2': 35.0}.get(contract, MERGE_GAP_BPS)
    scores = [r['prominence'] for r in merge_levels(seed, proximity_bps, contract) if lower <= r['price'] <= upper]
    return dict(prior_close=close, range_ratio=ratio, lower=lower, upper=upper,
                p_min=min(scores) if scores else None, p_max=max(scores) if scores else None,
                normalization_status='ready' if scores else 'empty_seed', merge_contract=contract, merge_gap_bps=proximity_bps)


def transform(levels, basis):
    output = []
    contract = basis.get('merge_contract', CONTRACT)
    for row in merge_levels(levels, basis.get('merge_gap_bps', MERGE_GAP_BPS), contract):
        if not basis['lower'] <= row['price'] <= basis['upper']:
            continue
        lo, hi = basis['p_min'], basis['p_max']
        normalized = None if lo is None else .5 if hi == lo else min(1., max(0., (row['prominence']-lo)/(hi-lo)))
        output.append(dict(row, p_norm=normalized, normalization_status=basis['normalization_status']))
    return {'unified_levels': output, 'normalization': basis, 'load_contract': contract}
