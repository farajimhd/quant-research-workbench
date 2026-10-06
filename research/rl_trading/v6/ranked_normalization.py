"""TRAIN-only units fitted from unique, strictly prior target histories."""
import numpy as np
import torch
from research.rl_trading.v1.common import digest
from research.rl_trading.v6.execution_features import bps_input
from research.rl_trading.v6.features import SCALAR_NAMES, LEVEL_NAMES

VERSION = 'rl-v6-ranked-prior-candle-normalization-v1'


def fit_normalization(session, decisions):
    if session.role != 'train' or not decisions:
        raise ValueError('Nonempty TRAIN histories required for normalization')
    clocks = {}
    for d in decisions:
        index = int(d.held_index[0]) if len(d.held_index) else d.soft_tokens[1]-1
        if not 0 <= index < len(session.listings):
            raise ValueError('Target listing escaped certified population')
        clocks.setdefault(index, []).append(d.close_us)
    count = 0; mean = np.zeros(147); m2 = np.zeros(147); receipts = []
    for index, targets in sorted(clocks.items()):
        identity = session.listings[index]; sources = []
        if session.previous and identity in session.previous.manifest['offsets']:
            sources.append(session.previous.listing_tail(identity))
        sources.append(session.bank.listing(identity))
        times = np.concatenate([s.close_us for s in sources])
        if len(times)>1 and (np.diff(times)<=0).any():
            raise ValueError('Normalization history clocks must increase')
        ends = np.searchsorted(times, np.unique(targets), side='left')
        positions = ends[:, None] - np.arange(120, 0, -1)[None]
        used = np.unique(positions[positions>=0])
        if not len(used): continue
        if times[used].max() >= max(targets):
            raise ValueError('Normalization crossed the strict prior fence')
        scalar = np.concatenate([s.scalar for s in sources])[used]
        levels = np.concatenate([s.levels for s in sources])[used]
        values = bps_input(torch.from_numpy(scalar), torch.from_numpy(levels)).numpy().astype(np.float64)
        if not np.isfinite(values).all(): raise ValueError('Nonfinite TRAIN features')
        n = len(values); batch_mean = values.mean(0); batch_m2 = ((values-batch_mean)**2).sum(0)
        delta = batch_mean-mean; total = count+n
        m2 += batch_m2+delta**2*count*n/total; mean += delta*n/total; count = total
        receipts.append(dict(identity=identity, observations=n, clock_hash=digest(times[used].tolist())))
    if not count: raise ValueError('TRAIN prior history is empty')
    std = np.sqrt(m2/count); std = np.where(std>1e-6, std, 1.)
    for i, name in enumerate(SCALAR_NAMES):
        if name.endswith(('valid','present','available')) or name in ('premarket','regular','after_hours'):
            mean[i]=0.; std[i]=1.
    for slot in range(10):
        for name in ('role_support','role_resistance','role_transition','historical_origin','present'):
            i=37+slot*11+LEVEL_NAMES.index(name); mean[i]=0.; std[i]=1.
    return dict(version=VERSION, scope='train_only', units='bps-v1', mean=mean.tolist(), std=std.tolist(),
        observations=count, source_certificate_sha256=session.source_certificate_sha256,
        context_split_receipt_sha256=session.context_split_receipt_sha256,
        history_contract='last120_actual_candles_strictly_prior_no_price_filter', histories=receipts)
