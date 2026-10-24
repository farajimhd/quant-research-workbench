"""Training-case specialists and broad offspring; never trim winning trades."""
from dataclasses import dataclass, asdict
import math
import numpy as np
from research.vectorized_backtest.v42 import objectives as base


@dataclass(frozen=True)
class Objective:
    budget: float = 1000.
    drawdown_weight: float = .05
    outside_loss_weight: float = 1.
    broad_loss_weight: float = 1.
    tail_weight: float = .25
    positive_cap: float = 1.
    tail_fraction: float = .10

    def validate(self):
        if any(not math.isfinite(x) or x < 0 for x in asdict(self).values()):
            raise ValueError('Finite nonnegative objective required')
        if self.budget <= 0 or self.positive_cap <= 0 or not 0 < self.tail_fraction <= 1:
            raise ValueError('Invalid fixed case budget, cap or tail')
        return self


def panel_from_cases(pnl, drawdown, config=Objective()):
    """Fixed case universe x candidates; inactive cases remain exactly zero."""
    config.validate()
    pnl, drawdown = np.asarray(pnl, float), np.asarray(drawdown, float)
    if pnl.ndim != 2 or not pnl.size or drawdown.shape != pnl.shape:
        raise ValueError('Aligned complete case panels required')
    if not np.isfinite(pnl).all() or not np.isfinite(drawdown).all() or (drawdown < 0).any():
        raise ValueError('Invalid case financial summaries')
    utility = (pnl - config.drawdown_weight * drawdown) / config.budget
    loss = np.maximum(-utility, 0)
    count = len(utility)
    outside = (loss.sum(0)[None] - loss) / max(1, count - 1)
    specialist = utility - config.outside_loss_weight * outside
    k = max(1, math.ceil(config.tail_fraction * count))
    tail = np.sort(loss, axis=0)[-k:].mean(0)
    broad = (np.minimum(np.maximum(utility, 0), config.positive_cap).mean(0)
             - config.broad_loss_weight * loss.mean(0) - config.tail_weight * tail)
    best = np.argmax(specialist, axis=0)
    return dict(utility=utility, case_specialist=specialist,
                specialist=specialist[best, np.arange(pnl.shape[1])],
                target_index=best, broad=broad, case_pnl=pnl, case_drawdown=drawdown,
                tail_loss=tail)


def local_panel(ordered, config=Objective(), *, case_maps):
    """Economic-security cases; realized drawdown from complete episode exits.

    Full-session mark-to-market drawdown stays available in the financial panel.
    Case drawdown measures realized closed-position equity, not intraposition marks.
    """
    result = base.local_panel(ordered, base.Objective())
    if len(ordered) != len(case_maps):
        raise ValueError('Certified case map required for each training session')
    if len(ordered) != 1:
        raise ValueError('V44 experiment is explicitly single-session')
    metrics, mapping = ordered[0], case_maps[0]
    names = sorted(set(mapping['security_ids']))
    lookup = {name: i for i, name in enumerate(names)}
    n = result['pnl'].shape[1]
    pnl = np.zeros((len(names), n)); dd = np.zeros_like(pnl)
    listing_cases = [lookup[x] for x in mapping['security_ids']]
    listing_ids = mapping['listing_ids']
    if len(listing_ids) != len(listing_cases) or len(set(listing_ids)) != len(listing_ids):
        raise ValueError('Invalid certified listing case map')
    by_listing = dict(zip(listing_ids, listing_cases))
    for candidate in range(n):
        values = np.asarray(metrics['episode_profits'][candidate], float)
        listings = np.asarray(metrics['episode_listings'][candidate], int)
        clocks = np.asarray(metrics['episode_exit_clocks'][candidate], int)
        if not (len(values) == len(listings) == len(clocks) == metrics['closed_positions'][candidate]):
            raise ValueError('Complete episode alignment changed')
        if not np.isfinite(values).all() or (listings < 0).any() or (listings >= len(listing_cases)).any():
            raise ValueError('Invalid complete episode')
        targets = np.asarray(listing_cases, int)[listings]
        pnl[:, candidate] = np.bincount(targets, weights=values, minlength=len(names))
        # Simultaneous closes aggregate first; arbitrary ledger order cannot affect DD.
        order = np.lexsort((clocks, targets))
        t, c, v = targets[order], clocks[order], values[order]
        if len(v):
            starts = np.r_[0, np.flatnonzero((t[1:] != t[:-1]) | (c[1:] != c[:-1])) + 1]
            t, v = t[starts], np.add.reduceat(v, starts)
            boundaries = np.r_[0, np.flatnonzero(t[1:] != t[:-1]) + 1, len(v)]
            for start, end in zip(boundaries[:-1], boundaries[1:]):
                equity = np.r_[0., np.cumsum(v[start:end])]
                dd[t[start], candidate] = np.max(np.maximum.accumulate(equity) - equity)
        expected = np.zeros(len(names))
        for listing, value in metrics['ticker_pnl'][candidate].items():
            expected[by_listing[listing]] += value
        if not np.allclose(expected, pnl[:, candidate], rtol=1e-10, atol=1e-7):
            raise ValueError('Economic security P&L does not reconcile')
    if not np.allclose(pnl.sum(0), result['pnl'][0], rtol=1e-10, atol=1e-7):
        raise ValueError('Case P&L does not reconcile to full session')
    cases = [f'{mapping["day"]}:{name}' for name in names]
    prepared = panel_from_cases(pnl, dd, config)
    result.update(prepared)
    result.update(values=prepared['broad'][None], case_ids=cases,
                  specialist_targets=[cases[i] for i in prepared['target_index']],
                  session_penalty_available=False)
    return result
