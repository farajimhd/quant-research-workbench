"""Read-only teacher-label inspection. No training, label rewriting or holdout access.

The UI selects certified train/development dates, never arbitrary filesystem paths.
Only this deployment's explicit workstation runtime mapping is permitted.
"""
from functools import lru_cache
from datetime import datetime
import json
import os
from pathlib import Path
from threading import RLock
from zoneinfo import ZoneInfo

import numpy as np
import polars as pl

from research.rl_trading.v1.common import digest, file_hash
from research.rl_trading.v6.episode_windows import VERSION
from research.rl_trading.v6.features import SCALAR_NAMES

LOCK = RLock()
DEFAULT_ROOT = r"\\DESKTOP-SAAI85T\Workstation-D\TradingML\runtimes"
RUN = 'rl-v6-rth-matched-v6-b31fc0cdc'
RUN_TRAIN = ('2026-07-31', '2026-08-10', '2026-08-21')
RUN_DEVELOPMENT = ('2026-08-24', '2026-08-25')


def run_window(day):
    begin = int(datetime.fromisoformat(day+'T09:30:00').replace(tzinfo=ZoneInfo('America/New_York')).timestamp()*1e6)
    return begin, begin + 1024_000_000


def run_scope():
    """Pin the user-selected completed diagnostic, not an arbitrary latest run."""
    root = runtime() / RUN
    protocol = json.loads((root / 'protocol.json').read_text())
    manifest = json.loads((root / 'manifest.json').read_text())
    if (protocol.get('source') != 'b31fc0cdcdb5ce1f5db1417c69322d2922d26966' or
            protocol.get('sealed_holdout_used') is not False or
            protocol.get('train_days') != list(RUN_TRAIN) or
            protocol.get('development_days') != list(RUN_DEVELOPMENT) or
            protocol.get('clock_window') != 'NY09:30inclusive09:47:04exclusive allprewarmup retained' or
            manifest.get('source_commit') != protocol['source'] or
            manifest.get('prefix_clocks') != 1024 or manifest.get('auxiliary_targets_masked') is not True):
        raise ValueError('Selected training run scope changed')
    return dict(run=RUN, source_commit=protocol['source'],
                protocol_sha256=file_hash(root / 'protocol.json'),
                window='09:30:00 inclusiveâ€“09:47:04 exclusive ET',
                objective='Four-head soft classification only; auxiliary value/bracket targets masked')


def training_frames(day, frames):
    begin, end = run_window(day)
    # Preserve original per-episode weights: the actual trainer filters rows,
    # not the episode normalization denominator or probability values.
    return {**frames, **{b: frames[b].filter((pl.col('time_us') >= begin) & (pl.col('time_us') < end)) for b in ('flat', 'held')}}


def hindsight_regions(episodes, listing_id, start, end):
    """Shade hindsight entry-to-exit, not the enclosing MACD sign interval.

    Hints are completed-candle clocks; chart coordinates are candle starts.
    Keep overlapping original episodes independently, as in the label source.
    """
    original = episodes.filter((pl.col('listing_id') == listing_id) &
        (pl.col('direction') == 1) & (pl.col('exit_hint_us') >= start) &
        (pl.col('entry_hint_us') < end))
    regions = [dict(start=r['entry_hint_us']//1_000_000-1,
                    end=r['exit_hint_us']//1_000_000-1,
                    color='var(--success)', label='') for r in original.iter_rows(named=True)]
    return original, regions


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
    allowed = {d: 'train' for d in RUN_TRAIN} | {d: 'development' for d in RUN_DEVELOPMENT}
    if day not in allowed:
        raise ValueError('Choose a date used by the selected RTH run; heldout is sealed')
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
    for day in RUN_TRAIN + RUN_DEVELOPMENT:
        role = 'train' if day in RUN_TRAIN else 'development'
        days.append(dict(day=str(day), role=role))
    return dict(models=[dict(id='v6', name='RL trading V6', stage='Teacher training',
                             label_source='Hindsight MACD 1s episodes', run=RUN, days=days)],
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
    episodes = pl.read_parquet(episode_path)
    identities = episodes.select('listing_id', 'ticker').unique()
    if identities['listing_id'].n_unique() != identities.height:
        raise ValueError('Ambiguous ticker identity')
    for branch in frames:
        frames[branch] = frames[branch].join(identities, on='listing_id', how='left', validate='m:1')
        if frames[branch]['ticker'].null_count():
            raise ValueError('Label identity is missing from certified episodes')
    frames['episodes'] = episodes
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
            distributions.append(dict(branch=branch, range=f'{lo:g}â€“{min(hi, 1):g}',
                rows=frame.filter((p >= lo) & (p < hi)).height))
        repeated = frame.group_by('listing_id', 'time_us').len().filter(pl.col('len') > 1)
        episodes = frame.group_by('episode_uid').agg(pl.len().alias('rows'),
            ((pl.col('time_us').max()-pl.col('time_us').min())/1e6).alias('span_seconds'),
            w.sum().alias('weight'))
        branches.append(dict(branch=branch, rows=frame.height, episodes=episodes.height,
            overlapping_clocks=repeated.height, extra_overlap_rows=int(repeated['len'].sum() or 0)-repeated.height,
            median_episode_rows=episodes['rows'].median(), median_span_seconds=episodes['span_seconds'].median(),
            mean_probability=frame[column].mean(), original_weight_mass=frame['sample_weight'].sum()))
    tickers = (pl.concat([frames[b].select('listing_id', 'ticker', 'episode_uid') for b in ('flat', 'held')])
               .group_by('listing_id', 'ticker').agg(pl.len().alias('rows'), pl.col('episode_uid').n_unique().alias('episodes'))
               .sort(['rows', 'ticker'], descending=[True, False]).to_dicts())
    return dict(classes=classes, distributions=distributions, branches=branches, tickers=tickers,
                scope='Selected full session; saved original teacher labels',
                weight_scope='Original episode weights Ã— soft probability; class balancing excluded',
                coverage='WAIT coverage is inside supervised episodes only; outside-episode candles are unlabeled')


def preflight(day):
    frames, cert = labels(day)
    scope = run_scope()
    frames = training_frames(day, frames)
    entry, _, _ = sources(day)
    return dict(model='v6', day=day, role=entry['role'], status='ready',
                checks=['Train/development role verified', 'Label SHA-256 and row counts verified',
                        'Bank certificate binding verified', 'Episode identities verified',
                        'Soft probabilities and weights valid', 'Overlapping episode labels preserved'],
                candle_check='Candle and indicator file hashes verified when a chart is loaded',
                certificate_sha256=file_hash(sources(day)[2] / 'complete.json'),
                version=cert['version'], run_scope=scope,
                analytics={**statistics(frames), 'scope': f'{RUN} Â· {scope["window"]} Â· original saved soft targets'})


@lru_cache(maxsize=18)
def _verified_bank(day, stamps):
    _, bank, _ = sources(day)
    outer = json.loads((bank / 'complete.json').read_text())
    root = bank / 'bank'
    manifest = json.loads((root / 'complete.json').read_text())
    if manifest['files_sha256'] != outer['bank_file_hashes'] or manifest['scalar_names'] != list(SCALAR_NAMES):
        raise ValueError('Candle bank certificate binding failed')
    # This chart consumes clocks[N] and scalar[N,37], never level tensors.
    # Verify every byte of both consumed files against the certified bank.
    arrays = []
    for name, shape in [('close_us', (manifest['candle_count'],)),
                        ('scalar', (manifest['candle_count'], len(SCALAR_NAMES)))]:
        path = root / (name + '.npy')
        if file_hash(path) != manifest['files_sha256'][path.name]:
            raise ValueError('Chart input hash mismatch: ' + name)
        array = np.load(path, mmap_mode='r', allow_pickle=False)
        if array.shape != shape or array.dtype != (np.int64 if name == 'close_us' else np.float32):
            raise ValueError('Chart input shape/dtype mismatch')
        arrays.append(array)
    return manifest, *arrays


@lru_cache(maxsize=18)
def _candidates(day, stamps):
    _, bank, _ = sources(day)
    proof = json.loads((bank / 'complete.json').read_text())
    path = bank / 'candidates.parquet'
    if file_hash(path) != proof['outputs']['candidates']['sha256']:
        raise ValueError('Reward candidate source hash mismatch')
    return pl.read_parquet(path)


def chart(day, listing_id, episode_uid, branch, start_us, seconds):
    if branch not in ('flat', 'held') or not 60 <= seconds <= 3600:
        raise ValueError('Invalid chart branch/window')
    frames, cert = labels(day)
    run_scope()
    frames = training_frames(day, frames)
    begin, finish = run_window(day)
    branches = (branch,)
    frame = pl.concat([frames[b].select('listing_id', 'ticker', 'episode_uid', 'time_us') for b in branches]).filter(pl.col('listing_id') == listing_id)
    episode_options = frame.select('episode_uid').unique().sort('episode_uid')['episode_uid'].to_list()
    if episode_uid:
        frame = frame.filter(pl.col('episode_uid') == episode_uid)
    if frame.is_empty():
        raise ValueError('No saved labels match the selected listing/episode')
    _, root, _ = sources(day)
    paths = [root / 'complete.json'] + [root / 'bank' / p for p in ['complete.json', 'close_us.npy', 'scalar.npy']]
    with LOCK:
        manifest, all_clocks, all_scalar = _verified_bank(day, fingerprint(paths))
        candidates = _candidates(day, fingerprint([root / 'complete.json', root / 'candidates.parquet']))
    left, right = manifest['offsets'][listing_id]
    item_clocks, item_scalar = all_clocks[left:right], all_scalar[left:right]
    if not len(item_clocks) or np.any(np.diff(item_clocks) <= 0):
        raise ValueError('Invalid chart candle ordering')
    start = max(begin, min(start_us if start_us is not None else int(frame['time_us'].min())-60_000_000, finish-1))
    end = min(start + seconds*1_000_000, finish)
    selected = (item_clocks >= start) & (item_clocks < end)
    # raw[K,37], clocks[K]: sparse actual activity, never fill missing seconds.
    raw, clocks = item_scalar[selected], item_clocks[selected]
    valid = (raw[:, SCALAR_NAMES.index('bar_price_valid')] == 1) & (raw[:, SCALAR_NAMES.index('bar_extremes_valid')] == 1)
    candles = []
    for row, clock in zip(raw[valid], clocks[valid]):
        candle = {name: float(np.exp(float(row[SCALAR_NAMES.index('log_'+name)]))) for name in ['open', 'high', 'low', 'close']}
        # Chart bars start one second before the completed close/label clock.
        candles.append(dict(time=int(clock)//1_000_000-1, endTime=int(clock)//1_000_000, isClosed=True, **candle))
    target_rows = []
    rewards = candidates.select('episode_uid', 'time_us', 'score')
    for b in branches:
        column = 'enter_probability' if b == 'flat' else 'exit_probability'
        targets = frames[b].filter((pl.col('listing_id') == listing_id) & (pl.col('time_us') >= start) & (pl.col('time_us') < end))
        if episode_uid:
            targets = targets.filter(pl.col('episode_uid') == episode_uid)
        if b == 'flat':
            targets = targets.join(rewards, on=['episode_uid', 'time_us'], how='left', validate='m:1')
        else:
            targets = targets.with_columns((pl.col('close')-pl.col('hypothetical_entry_price')-2*cert['config']['fee_per_share']).alias('exit_net_per_share'), pl.lit(None, dtype=pl.Float64).alias('score'))
        reward = pl.when(pl.col(column) > 0).then(pl.col('score')).otherwise(None) if b == 'flat' else pl.col('exit_net_per_share')
        target_rows.extend(targets.select('episode_uid', 'time_us', pl.col(column).alias('probability'), 'sample_weight', reward.alias('reward'), pl.lit(b).alias('branch')).to_dicts())
    target_rows.sort(key=lambda r: (r['time_us'], r['episode_uid'], r['branch']))
    oscillator = []
    for column, label, color in [('macd_line', 'MACD', 'var(--primary)'), ('macd_signal', 'Signal', 'var(--warning)'), ('macd_histogram', 'Histogram', 'var(--muted-foreground)')]:
        data = []
        for row, clock in zip(raw[valid], clocks[valid]):
            if row[SCALAR_NAMES.index('indicator_available')] != 1:
                continue
            line, signal = [float(row[SCALAR_NAMES.index('macd_'+name+'_rel')]) * float(np.exp(float(row[SCALAR_NAMES.index('log_close')]))) for name in ('line', 'signal')]
            data.append(dict(time=int(clock)//1_000_000-1, value=line if column == 'macd_line' else signal if column == 'macd_signal' else line-signal))
        oscillator.append(dict(column=column, label=label, paneKey='macd', style='histogram' if column == 'macd_histogram' else 'line', color=color, lineWidth=1, data=data))
    original, regions = hindsight_regions(frames['episodes'], listing_id, start, end)
    return dict(ticker=frame['ticker'][0], candles=candles, labels=target_rows, branch=branch,
                start_us=start, end_us=end, seconds=seconds, omitted_invalid_price_rows=int((~valid).sum()),
                source='SHA-verified V6 packed 1s candles (decoded float32 log prices)',
                episodes=episode_options, selected_episode=episode_uid, label_config=cert['config'], oscillator_series=oscillator, regions=regions, original_long_episodes=original.to_dicts(),
                reward_units='ENTRY: original discounted score; HOLD/EXIT: fee-adjusted $/share since hypothetical entry',
                previous_available=start > begin, next_available=end < finish)
