"""Prepared Strategy37 episode veto; no release or runtime admission.

The caller supplies ordered native episode groups and Strategy36's independently
compiled activity evidence. This reducer neither reads market data nor changes
position management, quantities, fills, costs, or original episode anchors.
"""
import numpy as np


def episode_activity_veto_policy_payload() -> dict:
    return {
        'policy_id': 'strategy-thirty-seven-confirmed-episode-activity-veto-v1',
        'parent': 'exact published Strategy36',
        'trigger': 'parent-eligible completed decision with independently confirmed Strategy36 activity fade',
        'scope': 'new entries and reentries in the same original native MACD1s episode',
        'duration': 'through the remainder of that episode; next original episode starts without a veto',
        'missing_activity': 'reject current decision under parent policy; never infer a confirmed fade',
        'early_history': 'inherit Strategy36 first20s behavior; inactive history never triggers a veto',
        'anchors': 'retain original episode and first-setup boundaries; no reset after rejection',
        'held_positions': 'inherit all parent exits, protection, sizing and costs unchanged',
        'ordering': 'causal prefix within each ticker and original episode; never use future failure',
        'runtime': 'prepared Backtest research only; not installed or approved for live',
    }


def episode_activity_veto_mask(group_ids, parent_eligible, activity_eligible, confirmed_fade):
    """Return admission and veto masks (N,) from ordered group/evidence arrays.

    group_ids is Int64 (N,), with monotonically ordered identifiers for disjoint
    ticker/original-episode groups. The three Boolean (N,) arrays come from the
    independently certified parent gate and completed activity source. A missing
    candle must yield activity_eligible=False, confirmed_fade=False. A confirmed
    fade must already be parent-eligible and fail the activity gate.

    Two prefix maxima compare each row's most recent failure index with its
    group's first index. This resets at group boundaries without row loops or
    looking ahead; a future failure cannot change an earlier admission.
    """
    groups = np.asarray(group_ids)
    parent, activity, faded = map(np.asarray, (parent_eligible, activity_eligible, confirmed_fade))
    if (groups.dtype != np.int64 or groups.ndim != 1
            or any(mask.dtype != np.bool_ or mask.shape != groups.shape for mask in (parent, activity, faded))
            or np.any(groups < 0) or np.any(np.diff(groups) < 0)):
        raise ValueError('Episode veto requires ordered Int64 groups and aligned Boolean evidence')
    if np.any(faded & (~parent | activity)):
        raise ValueError('Confirmed episode fade contradicts parent or activity admission')
    if not len(groups):
        return np.empty(0, dtype=np.bool_), np.empty(0, dtype=np.bool_)
    indexes = np.arange(len(groups), dtype=np.int64)
    first = np.concatenate((np.asarray([True]), groups[1:] != groups[:-1]))
    starts = np.maximum.accumulate(np.where(first, indexes, -1))
    failures = np.maximum.accumulate(np.where(faded, indexes, -1))
    vetoed = failures >= starts
    return parent & activity & ~vetoed, vetoed
