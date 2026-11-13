"""Explicit overlapping-origin plans over immutable, verified session pools.

Bar values are never recomputed or split-adjusted here. Absolute session ranks
prove consecutive origins even when older context pools have been trimmed.
"""
from pathlib import Path
from functools import lru_cache
import os
import time
import numpy as np

from .contract import BAR, ORIGIN, BLOCK
from .storage import ShardReader
from .continuation import trim_pools
from ..shards.contract import digest, seed_for

POLICY = 'overlapping-origins-unique-blocks-v1'


@lru_cache(maxsize=32)
def session_paths(root, ticker):
    """Immutable parent directory metadata reused across chronological months."""
    base=Path(root)/'shards'/digest(ticker)[:24]
    try:
        with os.scandir(base) as entries:
            return tuple(sorted((p.name,Path(p.path)) for p in entries
                                if len(p.name)==10 and p.is_dir(follow_symlinks=False)))
    except FileNotFoundError:
        return ()


def ranks_for(receipt, blocks, pending):
    count = receipt['sampling']['session_origins']
    coverage = receipt.get('origin_coverage')
    if coverage and coverage['version'] == POLICY:
        starts = np.asarray(coverage['block_starts'], dtype=np.int64)
    elif coverage:
        starts = np.concatenate([np.arange(lo, hi, BLOCK) for lo, hi in coverage['ranges']]) if coverage['ranges'] else np.empty(0, dtype=np.int64)
    else:
        start = receipt['sampling']['unselected_prefix_origins'] + receipt['stats']['warmup_deferred']
        starts = start + np.arange(len(blocks)) * BLOCK
    if len(starts) != len(blocks) or np.any(starts < 0) or np.any(starts + BLOCK > count):
        raise ValueError('Parent block rank evidence differs')
    ranks = (starts[:, None] + np.arange(BLOCK)).reshape(-1)
    if len(pending):
        end = count
        if coverage and coverage['version'] == 'disjoint-session-origin-ranks-v1':
            # uncovered() retains the tail of the LAST REMAINING run, which
            # need not reach session end when an older block occupies the end.
            excluded = coverage['excluded_ranges']
            previous = 0
            for lo, hi in excluded:
                if not previous <= lo < hi <= count:
                    raise ValueError('Malformed excluded session ranges')
                previous = hi
            for lo, hi in reversed(excluded):
                if hi == end:
                    end = lo
                else:
                    break
            if any(lo < end and hi > end-len(pending) for lo, hi in excluded):
                raise ValueError('Pending origins intersect excluded coverage')
        if end < len(pending):
            raise ValueError('Pending origins exceed remaining session run')
        ranks = np.r_[ranks, np.arange(end-len(pending), end)]
    return ranks, starts


def rebuild(readers, geometry, stride, seed):
    if not readers or not 1 <= stride < BLOCK:
        raise ValueError('Require verified parents and an explicit overlap stride')
    receipts = [r.receipt for r in readers]
    first = receipts[0]
    for r in receipts:
        if any(r[k] != first[k] for k in ('ticker','session_date','liquid','price_basis','geometry')) or r['sampling']['session_origins'] != first['sampling']['session_origins']:
            raise ValueError('Parent session authority or split basis differs')
        if not r['price_basis'] or not r['independent_unit']:
            raise ValueError('Require independent session-basis parent')
    views, mappings = [], [[] for _ in readers]
    for frame in range(5):
        pieces = [r.view(frame) for r in readers]
        merged = np.concatenate(pieces)
        order = np.argsort(merged['end_us'], kind='stable'); merged = merged[order]
        duplicate = np.r_[False, np.diff(merged['end_us']) == 0]
        raw = merged.view(np.dtype((np.void, BAR.itemsize)))
        where = np.flatnonzero(duplicate)
        if np.any(raw[where] != raw[where-1]):
            raise ValueError('Parent bar bytes disagree on a shared clock')
        merged = merged[~duplicate].copy(); views.append(merged)
        for i, piece in enumerate(pieces):
            mappings[i].append(np.searchsorted(merged['end_us'], piece['end_us']))
    origins, ranks, old_starts = [], [], set()
    for i, reader in enumerate(readers):
        blocks = reader.array('origins.npy'); pending = reader.array('pending.npy').copy()
        if blocks.dtype != ORIGIN or blocks.shape != (reader.receipt['blocks'], BLOCK) or pending.dtype != ORIGIN:
            raise ValueError('Parent origin layout differs')
        pending['latest'] -= np.asarray(reader.receipt['pool_starts'])
        source = np.concatenate((blocks.reshape(-1), pending))
        rr, starts = ranks_for(reader.receipt, blocks, pending)
        old_starts.update(map(int, starts)); ranks.append(rr)
        for frame, history in enumerate(geometry.history):
            indexes = source['latest'][:, frame].copy(); mapping = mappings[i][frame]
            if np.any(indexes < history-1) or np.any(indexes >= len(mapping)):
                raise ValueError('Parent origin lacks complete context')
            # Every old context remains exactly contiguous after union. No
            # interpolation, missing-history fill or silently inserted bar.
            if np.any(mapping[indexes] - mapping[indexes-history+1] != history-1):
                raise ValueError('Merged context changes a parent history window')
            source['latest'][:, frame] = mapping[indexes]
        origins.append(source)
    origins = np.concatenate(origins); ranks = np.concatenate(ranks)
    order = np.argsort(ranks, kind='stable'); ranks, origins = ranks[order], origins[order]
    duplicate = np.r_[False, np.diff(ranks) == 0]; where = np.flatnonzero(duplicate)
    raw = origins.view(np.dtype((np.void, ORIGIN.itemsize)))
    if np.any(raw[where] != raw[where-1]):
        raise ValueError('Parent origins disagree at a shared session rank')
    origins, ranks = origins[~duplicate], ranks[~duplicate]
    if len(origins) and np.any(np.diff(origins['end_us']) <= 0):
        raise ValueError('Session rank clock ordering differs')
    offset = seed_for(seed, first['ticker'], first['session_date'], POLICY) % stride
    boundaries = np.r_[0, np.flatnonzero(np.diff(ranks) != 1)+1, len(ranks)]
    positions = []
    for lo, hi in zip(boundaries[:-1], boundaries[1:]):
        if hi-lo < BLOCK: continue
        start = lo + (offset-int(ranks[lo])) % stride
        positions.extend(p for p in range(start, hi-BLOCK+1, stride) if int(ranks[p]) not in old_starts)
    starts = [int(ranks[p]) for p in positions]
    blocks = np.stack([origins[p:p+BLOCK] for p in positions]) if positions else np.empty((0,BLOCK), dtype=ORIGIN)
    compiled = dict(views=views, blocks=blocks, pending=np.empty(0,dtype=ORIGIN),
        splits=[int(first['session_date'][:4]=='2026')]*len(blocks), pool_starts=[0]*5,
        cache_slices=[0]*5, pending_key=None, independent_unit=True, price_basis=first['price_basis'],
        stats=dict(source_origins=len(blocks)*BLOCK, warmup_deferred=0, boundary_tail_deferred=0,
                   pending_origins=0, source_count_semantics='overlapping stored occurrences; not unique events'),
        sampling=dict(session_origins=first['sampling']['session_origins'], reason='complete_blocks' if len(blocks) else 'no_new_unique_block',
                      origin_distribution=POLICY, stride=stride, offset=offset, verified_parent_receipts=[r['hash'] for r in receipts]),
        origin_coverage=dict(version=POLICY, session_origins=first['sampling']['session_origins'], block_starts=starts))
    trim_pools(compiled, geometry.history)
    return compiled


def compile_cached(unit, geometry, parents, stride, seed, gate, reporter=None):
    directories = {}
    for root, binding in parents:
        for day,path in session_paths(str(root),unit['ticker']):
            if day.startswith(unit['month']+'-'):
                directories.setdefault(day, []).append((path, binding))
    for day, sources in sorted(directories.items()):
        began = time.monotonic()
        if reporter: reporter.emit(state='Read',detail='Verify and reblock '+day)
        with gate(query=False):
            readers = [ShardReader(path,binding) for path,binding in sources]
            compiled = rebuild(readers, geometry, stride, seed)
        session = dict(unit, month=day, session=day, parent_month=unit['month'], split=int(day[:4]=='2026'))
        yield session, compiled, dict(queries=0, query_seconds=0., fetch_seconds=time.monotonic()-began,
                                     verified_parent_sessions=len(readers), cache_reblock_seconds=time.monotonic()-began)
