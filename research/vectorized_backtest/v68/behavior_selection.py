"""Training-record equivalence, independent of genotype and validation.

These fingerprints describe retained episode/exposure/request evidence, not a
claim that unrecorded individual fills are identical. No rounded comparisons.
"""
import hashlib
import struct
import time
import numpy as np

VERSION = 'training-record-1b-equivalence-v1'
LEDGERS = ('episode_listings', 'episode_entry_clocks', 'episode_exit_clocks',
           'episode_profits', 'open_episode_listings', 'open_episode_realized')
SCALARS = ('net_pnl', 'drawdown', 'closed_positions', 'winning_positions',
           'capital_dollar_seconds', 'turnover', 'terminal_valid')


def session_keys(metrics):
    n = len(metrics['net_pnl'])
    fields = LEDGERS + SCALARS + tuple(sorted(k for k in metrics if k.startswith(('admission_','label_'))))
    if any(len(metrics[k]) != n for k in fields):
        raise ValueError('Behavior evidence alignment changed')
    result = []
    for i in range(n):
        h = hashlib.sha256(VERSION.encode())
        for key in fields:
            integer = key in LEDGERS and key not in ('episode_profits', 'open_episode_realized')
            value = np.asarray(metrics[key][i], dtype='<i8' if integer else '<f8').reshape(-1)
            if not np.isfinite(value).all():
                raise ValueError('Nonfinite behavior evidence')
            h.update(key.encode()); h.update(struct.pack('<Q', value.size)); h.update(value.tobytes())
        result.append(h.digest())
    return result


def population_groups(ordered, n):
    started = time.perf_counter()
    keys = [m.get('selection_behavior_keys') for m in ordered]
    keys = [session_keys(m) if k is None else k for m, k in zip(ordered, keys)]
    if not keys or any(len(k) != n for k in keys):
        raise ValueError('Complete ordered training behavior required')
    groups = {}; labels = []
    for i in range(n):
        signature = tuple(k[i] for k in keys)
        labels.append(groups.setdefault(signature, len(groups)))
    counts = np.bincount(labels)
    return labels, dict(version=VERSION, groups=len(groups), largest_group=int(counts.max()),
                       equivalent_candidates=n-len(groups), seconds=time.perf_counter()-started)


def representatives(pool, labels):
    """One score-ranked representative per group; bounded linear work."""
    seen = set(); result = []
    for i in pool:
        if labels[i] not in seen:
            seen.add(labels[i]); result.append(i)
    return result
