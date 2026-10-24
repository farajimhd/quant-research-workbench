"""Training-case specialists and broad offspring; never trim winning trades."""
from dataclasses import dataclass, asdict
import math
import numpy as np
from research.vectorized_backtest.v42 import objectives as base


@dataclass(frozen=True)
class Objective:
    drawdown_weight: float = .05

    def validate(self):
        if not math.isfinite(self.drawdown_weight) or self.drawdown_weight < 0:
            raise ValueError('Finite nonnegative drawdown weight required')
        return self


def panel_from_cases(pnl, drawdown, config=Objective()):
    config.validate()
    pnl, drawdown = np.asarray(pnl, float), np.asarray(drawdown, float)
    if pnl.ndim != 2 or not pnl.size or pnl.shape != drawdown.shape:
        raise ValueError('Aligned complete case panels required')
    if not np.isfinite(pnl).all() or not np.isfinite(drawdown).all() or (drawdown < 0).any():
        raise ValueError('Finite case profit and nonnegative drawdown required')
    case_scores = pnl - config.drawdown_weight * drawdown
    best = np.argmax(case_scores, axis=0)
    return dict(case_scores=case_scores, specialist=case_scores[best, np.arange(pnl.shape[1])],
                target_index=best, global_profit=pnl.sum(0), case_pnl=pnl, case_drawdown=drawdown)


def local_panel(ordered, config=Objective(), *, case_maps):
    """Economic-security cases; realized drawdown from complete episode exits.

    Full-session mark-to-market drawdown stays available in the financial panel.
    Case drawdown measures realized closed-position equity, not intraposition marks.
    """
    result = base.local_panel(ordered, base.Objective())
    if len(ordered) != len(case_maps):
        raise ValueError('Certified case map required for each training session')
    if len(ordered) != 1:
        raise ValueError('V46 experiment is explicitly single-session')
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
        open_listings=np.asarray(metrics.get('open_episode_listings',[[] for _ in range(n)])[candidate],int)
        open_profits=np.asarray(metrics.get('open_episode_realized',[[] for _ in range(n)])[candidate],float)
        if len(open_listings)!=len(open_profits) or not np.isfinite(open_profits).all():
            raise ValueError('Incomplete-position cashflow alignment changed')
        if len(open_listings):
            if metrics['terminal_valid'][candidate] or (open_listings<0).any() or (open_listings>=len(listing_cases)).any():
                raise ValueError('Unliquidated position identity changed')
            target=np.asarray(listing_cases,int)[open_listings]
            pnl[:,candidate]+=np.bincount(target,weights=open_profits,minlength=len(names))
            # Such candidates are ineligible; cashflows are retained for reconciliation,
            # not promoted to completed-position episodes or invented exit prices.
        expected = np.zeros(len(names))
        for listing, value in metrics['ticker_pnl'][candidate].items():
            expected[by_listing[listing]] += value
        if not np.allclose(expected, pnl[:, candidate], rtol=1e-10, atol=1e-7):
            raise ValueError('Economic security P&L does not reconcile')
    if not np.allclose(pnl.sum(0), result['pnl'][0], rtol=1e-10, atol=1e-7):
        raise ValueError('Case P&L does not reconcile to full session')
    cases = [f'{mapping["day"]}:{name}' for name in names]
    prepared = panel_from_cases(pnl, dd, config)
    # Global selection uses the authoritative full-session net summary exactly.
    prepared['global_profit']=result['pnl'].sum(0)
    result.update(prepared)
    result.update(values=prepared['global_profit'][None], case_ids=cases,
                  specialist_targets=[cases[i] for i in prepared['target_index']],
                  session_penalty_available=False)
    return result
