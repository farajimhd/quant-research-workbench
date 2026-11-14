"""Read-only selected sessions and vectorized block-local causal eligibility."""
from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
import numpy as np
import polars as pl
from ..v5.phase2_ar_shards.contract import BLOCK, FRAMES
from ..v5.phase2_ar_shards.storage import ShardReader
from ..v5.shards.contract import digest, file_hash
from .config import HISTORY, TOKENS


def sealed(path):
    value = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if digest({k: v for k, v in value.items() if k != 'hash'}) != value.get('hash'):
        raise ValueError(f'Changed seal: {path}')
    return value


def beneath(root, relative):
    # Ledger paths were written on Windows; support both separators on readers.
    p = PurePosixPath(relative.replace('\\', '/'))
    if p.is_absolute() or '..' in p.parts or ':' in relative:
        raise ValueError('Unsafe selection payload path')
    path = root.joinpath(*p.parts).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError('Selection escapes its dataset root')
    return path


@dataclass(frozen=True)
class Unit:
    root: Path
    path: Path
    binding: str
    blocks: tuple  # (ordinal, split, liquid, selection id)


def load_units(roots):
    """Pin explicit immutable selected-block exports, never scan all shard files."""
    units, identities, seen, bindings = [], [], set(), set()
    for raw in roots:
        root = Path(raw).resolve()
        if str(root) in seen:
            raise ValueError('Duplicate dataset root')
        seen.add(str(root))
        terminal = json.loads((root / 'result.json').read_text())
        terminal_states={'complete','inventory_exhausted','capacity_reached'}
        if terminal.get('state') not in terminal_states or terminal.get('failure'):
            raise ValueError('Dataset root must be terminal complete, not a partial campaign')
        if (root/'progress.json').exists():
            live=json.loads((root/'progress.json').read_text())
            if live.get('state') not in terminal_states or live.get('failure'):
                raise ValueError('Dataset progress is still active or failed')
        plan, selection = sealed(root / 'plan.json'), sealed(root / 'selection.json')
        if plan['hash'] in bindings:
            raise ValueError('Duplicate immutable plan through a different dataset path')
        bindings.add(plan['hash'])
        if selection['binding'] != plan['hash']:
            raise ValueError('Selection belongs to another plan')
        if (terminal.get('binding')!=plan['hash'] or terminal.get('selection')!=selection
                or terminal.get('selected_blocks')!=selection['blocks']):
            raise ValueError('Final result does not certify this exact selected-block export')
        if (selection.get('input_context_only') is not True
                or selection.get('future_target_purge_required') is not True):
            raise ValueError('Unknown selected-block input/target contract')
        path = beneath(root, selection['path'])
        if path.name != selection['path'] or file_hash(path) != selection['sha256']:
            raise ValueError('Selected-block export changed')
        table = pl.read_parquet(path)
        if not table.height:
            raise ValueError('Root has no released selected blocks; candidate bytes are not a balanced training selection')
        if (table.columns != ['path', 'ordinal', 'split', 'liquid', 'selection']
                or table.height != selection['blocks']
                or table.select(pl.any_horizontal(pl.all().is_null()).any()).item()
                or table['selection'].n_unique() != table.height
                or table.select(pl.struct(['path', 'ordinal']).n_unique()).item() != table.height
                or not set(table['split'].unique()).issubset({0, 1})
                or not set(table['liquid'].unique()).issubset({0, 1})
                or (table['ordinal'] < 0).any()):
            raise ValueError('Invalid or duplicated selected blocks')
        for split in (0, 1):
            group = table.filter(pl.col('split') == split)
            if group.filter(pl.col('liquid') == 1).height != 3 * group.filter(pl.col('liquid') == 0).height:
                raise ValueError('Selected block export violates frozen 75/25 exposure')
        for key, group in table.sort(['path', 'ordinal']).group_by('path', maintain_order=True):
            relative = key[0]
            units.append(Unit(root, beneath(root, relative), plan['hash'],
                              tuple(group.select('ordinal', 'split', 'liquid', 'selection').iter_rows())))
        identities.append(dict(root=str(root), plan=plan['hash'], selection=selection['hash'],
                               sha256=selection['sha256'], blocks=table.height))
    return units, dict(roots=identities, hash=digest(identities))


@dataclass
class BlockPlan:
    origins: np.ndarray             # eligible block-local origin indices
    endpoints: tuple               # five [eligible, tokens_per_frame] GLOBAL pool indices
    targets: np.ndarray             # future 1s end indices, not input metadata
    origin_us: np.ndarray
    report: dict


def plan_block(reader: ShardReader, index: int):
    block = reader.block(index)  # shared hash, dtype, session and basis validation
    rows = reader.array('origins.npy')[index]
    if len(rows) != BLOCK or np.any(np.diff(rows['end_us']) <= 0):
        raise ValueError('Expected ordered 4096 origins')
    if np.any(np.diff(rows['latest'][:, 0]) != 1):
        raise ValueError('Block origins must be consecutive observed 1s bars')
    warm = np.ones(BLOCK, dtype=bool)
    offsets = []
    for f, needed in enumerate(HISTORY):
        pool, latest = block.views[f], block.latest[:, f]
        expected = np.searchsorted(pool['end_us'], block.ends, side='right') - 1
        if not np.array_equal(expected, latest):
            raise ValueError('Stored index is not the last completed timeframe bar')
        warm &= latest >= needed - 1
        offsets.append(int(rows['latest'][0, f] - latest[0]))
    target_ok = np.arange(BLOCK) + 64 < BLOCK
    eligible = np.flatnonzero(warm & target_ok)
    endpoints = tuple(rows['latest'][eligible, f, None] -
                      64 * np.arange(n - 1, -1, -1) for f, n in enumerate(TOKENS))
    # The reader's trimmed prefix, not unrelated session history, defines warmup.
    for f, ends in enumerate(endpoints):
        if len(ends) and np.any(ends - 63 < offsets[f]):
            raise ValueError('Historical window exceeds declared block prefix')
    targets = rows['latest'][eligible + 64, 0]
    if not np.array_equal(targets, rows['latest'][eligible, 0] + 64):
        raise ValueError('Future target is not exactly the next 64 observed 1s bars')
    return BlockPlan(eligible, endpoints, targets, rows['end_us'][eligible], dict(
        block=index, selected_origins=BLOCK, eligible_origins=len(eligible),
        history_insufficient=int((~warm).sum()), target_outside_block=int((~target_ok).sum()),
        rejected_union=int((~(warm & target_ok)).sum()),
        first=int(eligible[0]) if len(eligible) else None,
        last=int(eligible[-1]) if len(eligible) else None,
        reason=None if len(eligible) else 'no origin has all history and an in-block future target'))


def checked_unit(unit):
    reader = ShardReader(unit.path, unit.binding)
    receipt = reader.receipt
    if not receipt.get('price_basis') or not receipt.get('session_date'):
        raise ValueError('Latent v1 requires the explicit immutable session split basis')
    split = int(receipt['session_date'][:4] == '2026')
    if not 2019 <= int(receipt['session_date'][:4]) <= 2026:
        raise ValueError('Session year outside the agreed train/validation contract')
    for ordinal, s, liquid, _ in unit.blocks:
        if (not 0 <= ordinal < receipt['blocks'] or s != split
                or receipt['splits'][ordinal] != s or receipt['liquid'] != liquid):
            raise ValueError('Selection disagrees with the sealed session receipt')
    return reader
