"""Label-free contiguous prefix inputs from authenticated session banks.

Caller owns bank/source authentication. No label, suppression, episode score or
future action is accepted here. Reference position descriptors are hypothetical
teacher state, distinct from model intent and actual broker execution.
"""
import numpy as np
import torch

from research.rl_trading.v6.causal_feedback import WARMUP


def clock_grid(targets, session_start):
    targets = np.asarray(targets)
    if targets.dtype != np.int64 or targets.ndim != 1:
        raise ValueError('Integer target clocks required')
    clocks = targets[:, None] + np.arange(-WARMUP, 1, dtype=np.int64)[None] * 1_000_000
    available = clocks >= session_start
    reset = np.zeros_like(available)
    reset[:, 0] = True
    reset[:, 1:] = available[:, 1:] & ~available[:, :-1]
    return clocks, available, reset


def prefix_windows(session, identity, targets, reference_held, session_start):
    if session.role not in ('train', 'development'):
        raise ValueError('Sealed prefix access denied')
    clocks, available, reset = clock_grid(targets, session_start)
    reference_held = np.asarray(reference_held, dtype=np.float32)
    if reference_held.shape != (len(clocks), 11) or not np.isfinite(reference_held).all():
        raise ValueError('Finite reference position descriptors required')
    if np.any(reference_held[:, 4:] != 0):
        raise ValueError('Unsupported nonzero reference position slots')
    sources = []
    if session.previous and identity in session.previous.manifest['offsets']:
        sources.append(session.previous.listing_tail(identity))
    sources.append(session.bank.listing(identity))
    source_clock = np.concatenate([x.close_us for x in sources])
    values = np.concatenate([np.concatenate((x.scalar, x.levels.reshape(-1, 110)), 1) for x in sources])
    if len(source_clock) > 1 and np.any(np.diff(source_clock) <= 0):
        raise ValueError('Source clocks must be strictly ordered')
    valid_marks = np.flatnonzero(values[:, 35] == 1)
    n, steps = clocks.shape
    raw = np.zeros((n, steps, 120, 147), np.float32)
    present = np.zeros((n, steps, 120), bool)
    maximum = np.full((n, steps), -1, np.int64)
    held = np.zeros((n, steps, 11), np.float32)
    for row in range(n):
        units, entry_price, age = reference_held[row, :3]
        if units < 0 or age < 0 or (units > 0 and entry_price <= 0):
            raise ValueError('Invalid reference position')
        entry_clock = int(targets[row]) - int(round(float(age) * 1_000_000))
        for step, clock in enumerate(clocks[row]):
            if not available[row, step]:
                continue
            end = int(np.searchsorted(source_clock, clock, side='left'))
            begin = max(0, end - 120); count = end - begin
            if count:
                raw[row, step, -count:] = values[begin:end]
                present[row, step, -count:] = True
                maximum[row, step] = source_clock[end - 1]
            if units > 0 and clock >= entry_clock:
                mark_end = int(np.searchsorted(valid_marks, end, side='left'))
                if not mark_end:
                    raise ValueError('Held reference requires a causal valid mark')
                mark = np.exp(float(values[valid_marks[mark_end - 1], 3]))
                held[row, step, :4] = units, entry_price, (clock - entry_clock) / 1_000_000, mark / entry_price - 1
        # Preserve the approved final reference descriptor exactly; the prefix
        # uses earlier marks, never the final unrealized return.
        held[row, -1] = reference_held[row]
    if not np.isfinite(raw).all() or np.any(maximum >= clocks):
        raise ValueError('Invalid or noncausal prefix features')
    return {name: torch.from_numpy(value) for name, value in dict(
        raw=raw, present=present, held=held, clocks_us=clocks,
        available=available, reset=reset, max_input_close_us=maximum).items()}
