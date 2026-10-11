"""Hash-bound benchmark snapshots from approved split-adjusted V6 banks."""
from pathlib import Path
import hashlib
import numpy as np
import torch
from research.rl_trading.v1.common import file_hash
from research.rl_trading.v6.run_staged_underfit import read_authenticated
from research.rl_trading.v6.ranked_teacher_data import load_prepared
from research.rl_trading.v6.run_channel_underfit import selected_targets, verify_coverage

FIELDS = ('raw', 'present', 'held', 'action', 'quality', 'ratio', 'future', 'future_soft')


def windows(session, targets):
    """Exactly the last 120 observed bank rows strictly before each target.

    SessionBank supplies the already-adjusted previous scalar view; structural
    levels are consumed unchanged. Missing seconds are never manufactured.
    """
    if session.role not in ('train', 'development') or not targets:
        raise ValueError('Nonempty public session required; sealed access denied')
    n = len(targets)
    raw = np.zeros((n, 120, 147), np.float32); present = np.zeros((n, 120), bool)
    held = np.zeros((n, 11), np.float32); action = np.zeros(n, np.int64)
    quality = np.zeros(n, np.float32); ratio = np.full(n, np.nan, np.float32)
    future = np.full((n, 5), -1, np.int64); soft = np.zeros((n, 5, 4), np.float32); soft[..., 1] = 1
    maximum = np.full(n, -1, np.int64); keys = []
    groups = {}
    for row, target in enumerate(targets):
        index = int(target.held_index[0]) if len(target.held_index) else target.soft_tokens[1] - 1
        groups.setdefault(index, []).append(row)
        holding = bool(len(target.held_index))
        action[row] = (3 if target.token == 1 + len(session.listings) else 2) if holding else (0 if target.token else 1)
        if holding:
            held[row] = target.held_features[0]
        quality[row] = target.soft_probabilities[0 if holding else 1]
        if target.allocation_ratio_target is not None:
            ratio[row] = target.allocation_ratio_target
        if target.forecast_actions is not None:
            length = len(target.forecast_actions)
            if not 1 <= length <= 5 or target.forecast_probabilities is None:
                raise ValueError('Saved forecast contract changed')
            future[row, :length] = target.forecast_actions
            soft[row, :length] = target.forecast_probabilities
        keys.append((session.day.isoformat(), int(target.close_us), target.episode_uid, index, holding))
    if len(set(keys)) != n:
        raise ValueError('Duplicate target identity')
    for index, rows in groups.items():
        identity = session.listings[index]
        sources = []
        if session.previous and identity in session.previous.manifest['offsets']:
            sources.append(session.previous.listing_tail(identity))
        sources.append(session.bank.listing(identity))
        clock = np.concatenate([item.close_us for item in sources])
        if len(clock) > 1 and (np.diff(clock) <= 0).any():
            raise ValueError('Unordered input history')
        values = np.concatenate([np.concatenate((item.scalar, item.levels.reshape(-1, 110)), 1) for item in sources])
        for row in rows:
            end = int(np.searchsorted(clock, targets[row].close_us, side='left'))
            begin = max(0, end - 120); size = end - begin
            if size:
                raw[row, -size:] = values[begin:end]
                present[row, -size:] = True
                maximum[row] = clock[end - 1]
    if not np.isfinite(raw).all() or not np.isfinite(held).all() or any(maximum[i] >= targets[i].close_us for i in range(n)):
        raise ValueError('Nonfinite or noncausal input')
    if np.isinf(ratio).any() or ((action != 0) & np.isfinite(ratio)).any():
        raise ValueError('Allocation authority requires selected ENTRY')
    return dict(raw=torch.from_numpy(raw), present=torch.from_numpy(present), held=torch.from_numpy(held),
        action=torch.from_numpy(action), quality=torch.from_numpy(quality), ratio=torch.from_numpy(ratio),
        future=torch.from_numpy(future), future_soft=torch.from_numpy(soft), keys=keys,
        max_input_close_us=maximum.tolist(), role=session.role,
        bank_certificate_sha256=session.source_certificate_sha256,
        context_split_receipt_sha256=session.context_split_receipt_sha256)


def combine(items):
    result = {name: torch.cat([item[name] for item in items]) for name in FIELDS}
    result['keys'] = sum([item['keys'] for item in items], [])
    result['max_input_close_us'] = sum([item['max_input_close_us'] for item in items], [])
    return result


def training(parent_root):
    parent = read_authenticated(parent_root / 'manifest.json')
    if parent['version'] != 'rl-v6-own-channel-balanced-underfit-v1' or parent['sealed_labels_read'] is not False or parent['development_labels_read'] is not False:
        raise ValueError('Authenticated TRAIN-only own-channel parent required')
    selection_path = Path(parent['arguments']['selection'])
    if file_hash(selection_path) != parent['selection_sha256']:
        raise ValueError('TRAIN selection changed')
    selection = read_authenticated(selection_path)
    if selection.get('sealed_targets_read') is not False or selection.get('development_targets_read') is not False:
        raise ValueError('Public TRAIN-only target selection required')
    for name, key in (('normalization.json', 'normalization_sha256'), ('channel-normalization.json', 'channel_normalization_sha256')):
        if file_hash(parent_root / name) != parent[key]:
            raise ValueError('TRAIN normalization changed')
    items = []; witnesses = []; bindings = []
    for index, binding in enumerate(parent['sessions']):
        root = Path(binding['cache'])
        source = Path(parent['arguments']['initial_source']) if index == 0 else root / 'source.json'
        if file_hash(root / 'prepared-train.pt') != binding['cache_sha256'] or file_hash(source) != binding['source_sha256']:
            raise ValueError('Source/cache binding changed')
        session, candidates, _ = load_prepared(root, read_authenticated(source))
        if session.day.isoformat() != binding['day'] or session.role != 'train' or list(session.listings) != binding['input_listings']:
            raise ValueError('TRAIN session identity changed')
        targets = selected_targets(selection, binding['day'], binding['cache_sha256'], candidates)
        witnesses.append((session, targets)); items.append(windows(session, targets)); bindings.append(binding)
    verify_coverage(selection, witnesses)
    return parent, combine(items), bindings


def label_blind_sample(targets, day, maximum):
    """Uniform deterministic hash sample of rows, independent of label values."""
    if type(maximum) is not int or maximum < 1:
        raise ValueError('Positive sample bound required')
    def identity(target):
        return f'{day}|{target.close_us}|{target.episode_uid}|{list(target.held_index)}|{target.soft_tokens[1]}'
    chosen = sorted(targets, key=lambda target: hashlib.sha256(identity(target).encode()).digest())[:maximum]
    return sorted(chosen, key=lambda target: (target.close_us, target.episode_uid, len(target.held_index)))


def validation(source_runtime, parent, days, maximum=2048):
    """Caller must admit a fixed overfit checkpoint BEFORE invoking this."""
    import os
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from research.rl_trading.v6 import saved_label_audit as source, published_market_audit as market
    from research.rl_trading.v6.session_data import open_session
    from research.rl_trading.v6.opportunity_dataset import load_teacher
    from research.rl_trading.v6.probe_teacher_sequence import subset
    os.environ['RL_V6_LABEL_AUDIT_RUNTIME'] = str(source_runtime)
    items = []; scope = []
    for day in days:
        if day not in ('2026-08-24', '2026-08-25') or day <= max(binding['day'] for binding in parent['sessions']):
            raise ValueError('Frozen later public validation dates required')
        active, entry, _, teacher, bankroot, _ = source.session(day)
        if entry['role'] != 'development':
            raise ValueError('Only public development sessions; sealed access denied')
        ma, _, me, mroot, _ = market.session(day)
        # Public registries may have been extended since the historical parent.
        # Source certificates are authenticated above; do not silently rebind a
        # changed training source or assume old and current registry hashes equal.
        names = source.saved_symbols(str(bankroot), entry['bank_certificate_sha256'])
        ids = sorted(identity for identity, name in names.items() if name in parent['tickers'])
        if len(ids) != len(parent['tickers']) or {names[i] for i in ids} != set(parent['tickers']):
            raise ValueError('Validation ticker identity coverage changed')
        full = open_session(bankroot, runtime_root=source.runtime(), previous_root=source.mapped(entry['previous_root']))
        labels, _ = load_teacher(teacher, full, runtime_root=source.runtime(), audit_development=True, audit_listing_ids=ids, market_root=mroot)
        begin = int(datetime.fromisoformat(day + 'T04:00:00').replace(tzinfo=ZoneInfo('America/New_York')).timestamp() * 1_000_000)
        session, targets = subset(full, labels, full.listings, begin, begin + 600_000_000 - 1)
        selected = label_blind_sample(targets, day, maximum)
        items.append(windows(session, selected))
        scope.append(dict(day=day, role=entry['role'], available_rows=len(targets), sampled_rows=len(selected),
            dataset_sha256=active['sha256'], market_dataset_sha256=ma['sha256'], bank_certificate_sha256=entry['bank_certificate_sha256'],
            market_certificate_sha256=me['sha256'], context_split_receipt_sha256=session.context_split_receipt_sha256))
    return combine(items), scope
