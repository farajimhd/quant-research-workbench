"""Packed, non-overlapping per-session feature storage and candle context."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path

import numpy as np
from numpy.lib.format import open_memmap

from research.rl_trading.v6.features import (CONTEXT_CANDLES, LEVEL_NAMES,
                                             SCALAR_NAMES, VERSION,
                                             CandleFeatures)


FILES = ('close_us.npy', 'scalar.npy', 'levels.npy')


def _hash(path: Path) -> str:
    digest = sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _listing_hash(arrays: tuple[np.ndarray, ...], left: int, right: int) -> str:
    digest = sha256()
    for array in arrays:
        digest.update(array[left:right].tobytes())
    return digest.hexdigest()


@dataclass(frozen=True)
class SessionBank:
    root: Path
    manifest: dict
    close_us: np.ndarray
    scalar: np.ndarray
    levels: np.ndarray

    def listing(self, identity: str) -> CandleFeatures:
        try:
            start, stop = self.manifest['offsets'][identity]
        except KeyError as error:
            raise KeyError(f'Listing {identity} is absent from certified bank') from error
        item = CandleFeatures(self.close_us[start:stop], self.scalar[start:stop],
                              self.levels[start:stop])
        item.validate()
        return item

    def listing_tail(self, identity: str, *, length: int = CONTEXT_CANDLES) -> CandleFeatures:
        """Validate and reference only the bounded prior-session context.

        Full bank provenance and byte hashes are verified by open_bank;
        warm-up need not repeatedly scan all earlier candles of each listing.
        """
        if not 1 <= length <= CONTEXT_CANDLES:
            raise ValueError('Invalid actual-candle history length')
        try:
            start, stop = self.manifest['offsets'][identity]
        except KeyError as error:
            raise KeyError(f'Listing {identity} is absent from certified bank') from error
        start = max(start, stop - length)
        item = CandleFeatures(self.close_us[start:stop], self.scalar[start:stop],
                              self.levels[start:stop])
        item.validate()
        return item

    def history(self, identity: str, current_index: int,
                previous: 'SessionBank | None' = None,
                *, length: int = CONTEXT_CANDLES) -> CandleFeatures:
        """Return up to 120 actual completed candles, inclusive of index.

        The prior session is referenced, never copied into this bank. At an
        unseen listing's first session, context is shorter and explicitly so.
        """
        if length < 1 or length > CONTEXT_CANDLES:
            raise ValueError('Invalid actual-candle history length')
        current = self.listing(identity)
        if not 0 <= current_index < len(current.close_us):
            raise IndexError('Decision candle index outside current session')
        count = current_index + 1
        begin = max(0, count - length)
        now = CandleFeatures(current.close_us[begin:count],
                             current.scalar[begin:count],
                             current.levels[begin:count])
        needed = length - len(now.close_us)
        if needed == 0 or previous is None or identity not in previous.manifest['offsets']:
            return now
        prior = previous.listing(identity)
        if len(prior.close_us) and prior.close_us[-1] >= now.close_us[0]:
            raise ValueError('Previous context crosses or follows decision clock')
        return CandleFeatures(
            np.concatenate((prior.close_us[-needed:], now.close_us)),
            np.concatenate((prior.scalar[-needed:], now.scalar)),
            np.concatenate((prior.levels[-needed:], now.levels)),
        )


def write_bank(root: Path, lengths: dict[str, int],
               rows: object, *, source_hash: str) -> dict:
    """Materialize one exact-size packed bank from a per-listing iterator.

    `rows` yields `(identity, CandleFeatures)` once. A census supplies exact
    row counts, so no overlapping windows, dense clock padding, or second
    copy of feature values is written. Incomplete output is never certified.
    """
    root = Path(root)
    if not root.is_absolute() or not lengths or not source_hash:
        raise ValueError('Use an absolute runtime bank root and source hash')
    if any(not key or type(size) is not int or size < 0
           for key, size in lengths.items()):
        raise ValueError('Invalid listing census')
    root.mkdir(parents=True, exist_ok=True)
    identities = sorted(lengths)
    offsets = {}
    cursor = 0
    for identity in identities:
        offsets[identity] = [cursor, cursor + lengths[identity]]
        cursor += lengths[identity]
    if cursor == 0:
        raise ValueError('No certified actual candles in session bank')
    plan = {'version': VERSION, 'source_hash': source_hash,
            'offsets': offsets, 'candle_count': cursor}
    plan_path = root / 'plan.json'
    complete_path = root / 'complete.json'
    if complete_path.exists():
        previous = open_bank(root)
        if (previous.manifest['source_hash'] != source_hash or
                previous.manifest['offsets'] != offsets):
            raise ValueError('Certified bank belongs to a different plan')
        return previous.manifest
    if plan_path.exists():
        if json.loads(plan_path.read_text(encoding='utf-8')) != plan:
            raise ValueError('Partial bank belongs to a different source or census')
        mode = 'r+'
    else:
        if any((root / name).exists() for name in FILES):
            raise ValueError('Bank arrays exist without a binding plan')
        plan_path.write_text(json.dumps(plan, sort_keys=True), encoding='utf-8')
        mode = 'w+'
    clocks = open_memmap(root / FILES[0], mode=mode, dtype=np.int64,
                         shape=(cursor,))
    scalar = open_memmap(root / FILES[1], mode=mode, dtype=np.float32,
                         shape=(cursor, len(SCALAR_NAMES)))
    levels = open_memmap(root / FILES[2], mode=mode, dtype=np.float32,
                         shape=(cursor, 2, 5, len(LEVEL_NAMES)))
    progress_path = root / 'progress.json'
    progress = (json.loads(progress_path.read_text(encoding='utf-8'))
                if progress_path.exists() else {})
    written = set(progress)
    if not written <= set(identities):
        raise ValueError('Partial bank has unknown listing identities')
    for identity, digest in progress.items():
        left, right = offsets[identity]
        combined = _listing_hash((clocks, scalar, levels), left, right)
        if digest != combined:
            raise ValueError(f'Partial bank listing hash mismatch: {identity}')
    for identity, item in rows:
        if identity not in offsets:
            raise ValueError('Unexpected listing while writing bank')
        if identity in written:
            continue
        item.validate()
        left, right = offsets[identity]
        if len(item.close_us) != right - left:
            raise ValueError(f'Census row count changed for {identity}')
        clocks[left:right] = item.close_us
        scalar[left:right] = item.scalar
        levels[left:right] = item.levels
        for array in (clocks, scalar, levels):
            array.flush()
        progress[identity] = _listing_hash((clocks, scalar, levels), left, right)
        progress_tmp = root / 'progress.json.tmp'
        progress_tmp.write_text(json.dumps(progress, sort_keys=True),
                                encoding='utf-8')
        progress_tmp.replace(progress_path)
        written.add(identity)
    if written != set(identities):
        raise ValueError(f'Bank lacks {len(set(identities)-written)} listings')
    for array in (clocks, scalar, levels):
        array.flush()
    del clocks, scalar, levels
    manifest = {
        'version': VERSION, 'source_hash': source_hash,
        'listing_count': len(identities), 'candle_count': cursor,
        'offsets': offsets, 'scalar_names': SCALAR_NAMES,
        'level_names': LEVEL_NAMES,
        'shapes': {'close_us': [cursor], 'scalar': [cursor, len(SCALAR_NAMES)],
                   'levels': [cursor, 2, 5, len(LEVEL_NAMES)]},
        'files_sha256': {name: _hash(root / name) for name in FILES},
    }
    temporary = root / 'complete.json.tmp'
    temporary.write_text(json.dumps(manifest, sort_keys=True), encoding='utf-8')
    temporary.replace(root / 'complete.json')
    return manifest


def open_bank(root: Path, *, verify_hashes: bool = True) -> SessionBank:
    root = Path(root)
    manifest = json.loads((root / 'complete.json').read_text(encoding='utf-8'))
    if (manifest['version'] != VERSION or
            tuple(manifest['scalar_names']) != SCALAR_NAMES or
            tuple(manifest['level_names']) != LEVEL_NAMES):
        raise ValueError('Incompatible packed candle feature bank')
    if verify_hashes and any(_hash(root / name) != manifest['files_sha256'][name]
                             for name in FILES):
        raise ValueError('Packed candle bank hash mismatch')
    arrays = tuple(np.load(root / name, mmap_mode='r', allow_pickle=False)
                   for name in FILES)
    for key, array in zip(('close_us', 'scalar', 'levels'), arrays):
        if list(array.shape) != manifest['shapes'][key]:
            raise ValueError('Packed candle bank shape mismatch')
    return SessionBank(root, manifest, *arrays)
