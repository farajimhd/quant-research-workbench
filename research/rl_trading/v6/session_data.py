"""Fail-closed loader and sparse event view of certified actual-candle days.

The bank is identity-packed, not a dense [listing, clock-second, feature]
array. Only transient integer row indices are sorted by candle close time;
feature values are never copied into overlapping 120-candle windows.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from hashlib import sha256
import json
from pathlib import Path
from typing import Iterator

import numpy as np

from research.rl_trading.v1.common import digest
from research.rl_trading.v6.bank import SessionBank, open_bank
from research.rl_trading.v6.features import VERSION as FEATURE_VERSION
from research.rl_trading.v6.split import role


def _hash(path: Path) -> str:
    digest = sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class CandleEvent:
    close_us: int
    listing_index: np.ndarray  # [K], indices into identity-ordered listings.
    bank_row: np.ndarray  # [K], absolute row offsets into the packed bank.


@dataclass(frozen=True)
class PackedSession:
    day: date
    role: str
    root: Path
    source_certificate_sha256: str
    bank: SessionBank
    previous: SessionBank | None
    listings: tuple[str, ...]

    def candle_events(self) -> Iterator[CandleEvent]:
        """Yield one causal event per observed close clock, never padded gaps.

        Stable sort stores only transient row indices. Grouping keys use the
        certified int64 close coordinate; no close time is rounded or inferred.
        """
        offsets = self.bank.manifest['offsets']
        lengths = np.asarray([offsets[name][1] - offsets[name][0]
                              for name in self.listings], dtype=np.int64)
        listing_by_row = np.repeat(np.arange(len(self.listings),
            dtype=np.int32), lengths)
        if len(listing_by_row) != len(self.bank.close_us):
            raise ValueError('Packed row count disagrees with listing offsets')
        order = np.argsort(self.bank.close_us, kind='stable')
        sorted_clocks = self.bank.close_us[order]
        if not len(order):
            return
        boundaries = np.r_[0, np.flatnonzero(np.diff(sorted_clocks)) + 1,
                           len(order)]
        for left, right in zip(boundaries[:-1], boundaries[1:]):
            rows = order[left:right]
            listing = listing_by_row[rows]
            if len(np.unique(listing)) != len(listing):
                raise ValueError('Listing has duplicate candles at one close clock')
            yield CandleEvent(int(sorted_clocks[left]), listing, rows)


def open_session(root: Path, *, runtime_root: Path,
                 previous_root: Path | None = None, split_manifest: Path | None = None) -> PackedSession:
    """Bind the day plan, top-level certificate, feature bank and prior tail.

    Hashes are checked once per loaded day, not repeatedly at every epoch or
    candle event. There is no unchecked training/replay mode.
    """
    runtime = Path(runtime_root).resolve()
    root = Path(root).resolve()
    previous_root = (Path(previous_root).resolve()
                     if previous_root is not None else None)
    if (not runtime.is_dir() or not root.is_relative_to(runtime) or
            (previous_root is not None and
             (not previous_root.is_relative_to(runtime) or
              previous_root == root))):
        raise ValueError('V6 session roots must be distinct runtime directories')
    plan_path, certificate_path = root / 'plan.json', root / 'complete.json'
    plan = json.loads(plan_path.read_text(encoding='utf-8'))
    certificate = json.loads(certificate_path.read_text(encoding='utf-8'))
    day = date.fromisoformat(plan['day'])
    from research.rl_trading.v6.validation_split import read_split, generation_role, dataset_split
    extension=read_split(split_manifest) if split_manifest else None
    if plan.get('validation_split') is not None and extension is None:
        raise ValueError('Extension bank requires explicit generation-only split authorization')
    if extension is not None:
        bound=dataset_split(plan,runtime)
        if bound is None or bound['hash']!=extension['hash']:raise ValueError('Bank extension split binding differs')
    split_role=generation_role(day,extension)
    if (certificate.get('status') != 'complete' or
            certificate.get('version') != FEATURE_VERSION or
            certificate.get('plan_hash') != plan.get('hash') or
            digest({key: value for key, value in plan.items()
                    if key != 'hash'}) != plan.get('hash') or
            plan.get('split_role') != split_role):
        raise ValueError('V6 day certificate, plan, or forward split mismatch')
    bank = open_bank(root / 'bank', verify_hashes=True)
    if (bank.manifest['source_hash'] != plan['hash'] or
            bank.manifest['files_sha256'] !=
            certificate['bank_file_hashes'] or
            bank.manifest['offsets'] != _offsets_from_census(plan['census'])):
        raise ValueError('Packed bank differs from certified day census')
    previous = None
    previous_day = plan.get('previous_day')
    if previous_day is None:
        if previous_root is not None or split_role != 'context_only':
            raise ValueError('Only context-only day may omit previous context')
    else:
        if previous_root is None:
            raise ValueError('Prior certified day is required for 120 candles')
        prior_plan = json.loads((previous_root / 'plan.json').read_text())
        prior_certificate = json.loads((previous_root / 'complete.json').read_text())
        if (prior_plan['day'] != previous_day or
                digest({key: value for key, value in prior_plan.items()
                        if key != 'hash'}) != prior_plan.get('hash') or
                prior_certificate.get('status') != 'complete' or
                prior_certificate.get('plan_hash') != prior_plan.get('hash') or
                prior_plan.get('source_build_id') != plan.get('previous_build_id')):
            raise ValueError('Previous-day context or source authority changed')
        previous = open_bank(previous_root / 'bank', verify_hashes=True)
        if (previous.manifest['source_hash'] != prior_plan['hash'] or
                previous.manifest['files_sha256'] !=
                prior_certificate['bank_file_hashes']):
            raise ValueError('Prior bank differs from its certificate')
        for identity in set(bank.manifest['offsets']) & set(previous.manifest['offsets']):
            current_start, current_end = bank.manifest['offsets'][identity]
            prior_start, prior_end = previous.manifest['offsets'][identity]
            if (current_end > current_start and prior_end > prior_start and
                    previous.close_us[prior_end - 1] >=
                    bank.close_us[current_start]):
                raise ValueError('Prior candle context reaches current decision day')
    return PackedSession(day, split_role, root, _hash(certificate_path), bank,
                         previous, tuple(sorted(bank.manifest['offsets'])))


def _offsets_from_census(census: dict[str, int]) -> dict[str, list[int]]:
    offsets = {}
    cursor = 0
    for identity in sorted(census):
        size = census[identity]
        if type(size) is not int or size < 0:
            raise ValueError('Invalid certified candle census')
        offsets[identity] = [cursor, cursor + size]
        cursor += size
    return offsets
