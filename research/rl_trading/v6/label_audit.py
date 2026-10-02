"""Read-only teacher-label inspection. No training, label rewriting or holdout access.

The UI selects certified train/development dates, never arbitrary filesystem paths.
Only this deployment's explicit workstation runtime mapping is permitted.
"""
from functools import lru_cache
import json
import os
from pathlib import Path
from threading import RLock

import numpy as np
import polars as pl

from research.rl_trading.v1.common import digest, file_hash
from research.rl_trading.v6.episode_windows import VERSION
from research.rl_trading.v6.features import SCALAR_NAMES
from research.rl_trading.v6.split import TRAIN, DEVELOPMENT

LOCK = RLock()
DEFAULT_ROOT = r"\\DESKTOP-SAAI85T\Workstation-D\TradingML\runtimes"


def runtime():
    root = Path(os.environ.get('RL_V6_AUDIT_RUNTIME_ROOT', DEFAULT_ROOT)).resolve()
    if not root.is_dir():
        raise ValueError('Configured V6 research runtime is unavailable')
    return root


def relocate(value, root):
    """Map the certified workstation D: root to its explicitly configured mount."""
    relative = Path(value).relative_to(Path('D:/TradingML/runtimes'))
    result = (root / relative).resolve()
    if not result.is_relative_to(root):
        raise ValueError('Certified input escapes the research runtime')
    return result


def sources(day):
    allowed = {str(d): 'train' for d in TRAIN} | {str(d): 'development' for d in DEVELOPMENT}
    if day not in allowed:
        raise ValueError('Choose a certified train/development date; heldout is sealed')
    root = runtime()
    path = root / 'rl-v6-training-ready-c824232b4/audit-cert-repair-df08998e3/complete.json'
    dataset = json.loads(path.read_text())
    if (dataset.get('sealed_test_accessed') is not False or
            dataset.get('status') != 'audited_ready_for_training' or
            digest({k: v for k, v in dataset.items() if k != 'hash'}) != dataset.get('hash')):
        raise ValueError('V6 dataset certificate is invalid')
    entries = [d for d in dataset['days'] if d['day'] == day]
    if len(entries) != 1 or entries[0]['role'] != allowed[day]:
        raise ValueError('Session role differs from the forward split')
    entry = entries[0]
    bank = relocate(entry['bank_root'], root)
    labels = root / 'rl-v6-episode-windows-aa9021599' / day
    return entry, bank, labels


def catalog():
    days = []
    for day in TRAIN + DEVELOPMENT:
        role = 'train' if day in TRAIN else 'development'
        days.append(dict(day=str(day), role=role))
    return dict(models=[dict(id='v6', name='RL trading V6', stage='Teacher training',
                             label_source='Hindsight MACD 1s episodes', days=days)],
                heldout='2026-08-26 sealed')


def fingerprint(paths):
    return tuple((str(p), p.stat().st_size, p.stat().st_mtime_ns) for p in paths)


@lru_cache(maxsize=2)
def _verified_labels(day, stamps):
    entry, bank, root = sources(day)
    cert_path = root / 'complete.json'
    cert = json.loads(cert_path.read_text())
    if (cert.get('version') != VERSION or cert.get('status') != 'audited_independent_episode_windows' or
            cert.get('day') != day or cert.get('role') != entry['role'] or
            cert.get('sealed_test_accessed') is not False or
            cert.get('bank_certificate_sha256') != entry['bank_certificate_sha256'] or
            file_hash(bank / 'complete.json') != entry['bank_certificate_sha256']):
        raise ValueError('Label/session certificate binding failed')
    frames = {}
    for branch, probability in [('flat', 'enter_probability'), ('held', 'exit_probability')]:
        path = root / (branch + '.parquet')
        if file_hash(path) != cert['files'][branch]['sha256']:
            raise ValueError(f'{branch} label hash mismatch')
        frame = pl.read_parquet(path)
        if frame.height != cert['files'][branch]['rows']:
            raise ValueError('Certified label count mismatch')
        if frame.select('episode_uid', 'time_us').n_unique() != frame.height:
            raise ValueError('Duplicate label within an episode; overlapping episodes remain separate')
        if frame.filter(pl.col(probability).is_null() | ~pl.col(probability).is_finite() |
                        ~pl.col(probability).is_between(0, 1) | pl.col('sample_weight').is_null() |
                        ~pl.col('sample_weight').is_finite() | (pl.col('sample_weight') <= 0) |
                        (pl.col('sample_weight') > 1)).height:
            raise ValueError('Invalid label probability or weight')
        frames[branch] = frame
    bank_cert = json.loads((bank / 'complete.json').read_text())
    episode_path = bank / 'episodes.parquet'
    if (cert['episodes_sha256'] != bank_cert['outputs']['episodes']['sha256'] or
            file_hash(episode_path) != cert['episodes_sha256']):
        raise ValueError('Episode identity source hash mismatch')
    identities = pl.read_parquet(episode_path, columns=['listing_id', 'ticker']).unique()
    if identities['listing_id'].n_unique() != identities.height:
        raise ValueError('Ambiguous ticker identity')
    for branch in frames:
        frames[branch] = frames[branch].join(identities, on='listing_id', how='left', validate='m:1')
        if frames[branch]['ticker'].null_count():
            raise ValueError('Label identity is missing from certified episodes')
    return frames, cert


def labels(day):
    entry, bank, root = sources(day)
    paths = [root / 'complete.json', root / 'flat.parquet', root / 'held.parquet',
             bank / 'complete.json', bank / 'episodes.parquet']
    with LOCK:
        # Include the expected binding as well as file identity in cache keys.
        return _verified_labels(day, fingerprint(paths) + ((entry['bank_certificate_sha256'], 0, 0),))


def statistics(frames):
    """Raw rows and original episode mass, not training class-balanced weights."""
    classes, distributions, branches = [], [], []
    for branch, positive, negative, column in [('flat', 'ENTRY', 'WAIT', 'enter_probability'),
                                               ('held', 'EXIT', 'HOLD', 'exit_probability')]:
        frame = frames[branch]
        p, w = pl.col(column), pl.col('sample_weight')
        for action, condition, mass in [(positive, p >= .5, p), (negative, p < .5, 1-p)]:
            classes.append(dict(action=action, rows=frame.filter(condition).height,
                                weighted_soft_mass=frame.select((mass*w).sum()).item()))
        for lo, hi in [(0, .1), (.1, .5), (.5, .9), (.9, 1.000001)]:
            distributions.append(dict(branch=branch, range=f'{lo:g}–{min(hi, 1):g}',
                rows=frame.filter((p >= lo) & (p < hi)).height))
        repeated = frame.group_by('listing_id', 'time_us').len().filter(pl.col('len') > 1)
        episodes = frame.group_by('episode_uid').agg(pl.len().alias('rows'),
            ((pl.col('time_us').max()-pl.col('time_us').min())/1e6).alias('span_seconds'),
            w.sum().alias('weight'))
        branches.append(dict(branch=branch, rows=frame.height, episodes=episodes.height,
            overlapping_clocks=repeated.height, extra_overlap_rows=int(repeated['len'].sum() or 0)-repeated.height,
            median_episode_rows=episodes['rows'].median(), median_span_seconds=episodes['span_seconds'].median(),
            mean_probability=frame[column].mean(), original_weight_mass=frame['sample_weight'].sum()))
    tickers = (pl.concat([f.select('listing_id', 'ticker', 'episode_uid') for f in frames.values()])
               .group_by('listing_id', 'ticker').agg(pl.len().alias('rows'), pl.col('episode_uid').n_unique().alias('episodes'))
               .sort(['rows', 'ticker'], descending=[True, False]).to_dicts())
    return dict(classes=classes, distributions=distributions, branches=branches, tickers=tickers,
                scope='Selected full session; saved original teacher labels',
                weight_scope='Original episode weights × soft probability; class balancing excluded',
                coverage='WAIT coverage is inside supervised episodes only; outside-episode candles are unlabeled')


def preflight(day):
    frames, cert = labels(day)
    entry, _, _ = sources(day)
    return dict(model='v6', day=day, role=entry['role'], status='ready',
                checks=['Train/development role verified', 'Label SHA-256 and row counts verified',
                        'Bank certificate binding verified', 'Episode identities verified',
                        'Soft probabilities and weights valid', 'Overlapping episode labels preserved'],
                candle_check='Full candle-bank bytes verified when a chart is loaded',
                certificate_sha256=file_hash(sources(day)[2] / 'complete.json'),
                version=cert['version'], analytics=statistics(frames))


@lru_cache(maxsize=1)
def _verified_bank(day, stamps):
    from research.rl_trading.v6.bank import open_bank
    _, bank, _ = sources(day)
    outer = json.loads((bank / 'complete.json').read_text())
    result = open_bank(bank / 'bank', verify_hashes=True)
    if result.manifest['files_sha256'] != outer['bank_file_hashes']:
        raise ValueError('Candle bank certificate binding failed')
    return result


def chart(day, listing_id, episode_uid, branch, start_us, seconds):
    if branch not in ('flat', 'held') or not 60 <= seconds <= 3600:
        raise ValueError('Invalid chart branch/window')
    frames, _ = labels(day)
    frame = frames[branch].filter(pl.col('listing_id') == listing_id)
    episode_options = frame.select('episode_uid').unique().sort('episode_uid')['episode_uid'].to_list()
    if episode_uid:
        frame = frame.filter(pl.col('episode_uid') == episode_uid)
    if frame.is_empty():
        raise ValueError('No saved labels match the selected listing/episode')
    _, root, _ = sources(day)
    paths = [root / 'bank' / p for p in ['complete.json', 'close_us.npy', 'scalar.npy', 'levels.npy']]
    with LOCK:
        bank = _verified_bank(day, fingerprint(paths))
    item = bank.listing(listing_id)
    start = start_us if start_us is not None else max(int(frame['time_us'].min())-60_000_000, int(item.close_us[0]))
    end = start + seconds*1_000_000
    selected = (item.close_us >= start) & (item.close_us < end)
    # raw[K,37], clocks[K]: sparse actual activity, never fill missing seconds.
    raw, clocks = item.scalar[selected], item.close_us[selected]
    valid = (raw[:, SCALAR_NAMES.index('bar_price_valid')] == 1) & (raw[:, SCALAR_NAMES.index('bar_extremes_valid')] == 1)
    candles = []
    for row, clock in zip(raw[valid], clocks[valid]):
        candle = {name: float(np.exp(float(row[SCALAR_NAMES.index('log_'+name)]))) for name in ['open', 'high', 'low', 'close']}
        # Chart bars start one second before the completed close/label clock.
        candles.append(dict(time=int(clock)//1_000_000-1, endTime=int(clock)//1_000_000, isClosed=True, **candle))
    targets = frame.filter((pl.col('time_us') >= start) & (pl.col('time_us') < end))
    column = 'enter_probability' if branch == 'flat' else 'exit_probability'
    target_rows = targets.select('episode_uid', 'time_us', pl.col(column).alias('probability'), 'sample_weight').sort('time_us', 'episode_uid').to_dicts()
    return dict(ticker=frame['ticker'][0], candles=candles, labels=target_rows, branch=branch,
                start_us=start, end_us=end, seconds=seconds, omitted_invalid_price_rows=int((~valid).sum()),
                source='SHA-verified V6 packed 1s candles (decoded float32 log prices)',
                episodes=episode_options,
                previous_available=start > int(item.close_us[0]), next_available=end <= int(item.close_us[-1]))
