"""Exact fractional empirical tails. No validation-dependent configuration."""
from dataclasses import dataclass, asdict
import math
import numpy as np


@dataclass(frozen=True)
class Objective:
    other_session_profit_weight: float = .25
    top_trim_fraction: float = 0.
    trade_tail_fraction: float = .10
    session_tail_fraction: float = .20
    trade_tail_weight: float = 0.
    session_tail_weight: float = 0.
    drawdown_weight: float = .05
    risk_weight: float = .005
    exposure_weight: float = .0004
    # V15 user-approved complexity price per reachable node.
    complexity_dollars_per_node: float = 0.
    block_sessions: int = 5
    block_tail_fraction: float = .50
    session_profit_target: float = 1000.0
    shortfall_weight: float = 0.
    concentration_weight: float = 0.

    def validate(self):
        if any((self.top_trim_fraction,self.trade_tail_weight,self.session_tail_weight,self.complexity_dollars_per_node,self.shortfall_weight,self.concentration_weight)):
            raise ValueError('V15 selects raw session profit with only drawdown/risk/exposure penalties')
        if any(not math.isfinite(v) or v < 0 for v in asdict(self).values()):
            raise ValueError('Objective parameters must be finite and nonnegative')
        if not 0 <= self.top_trim_fraction < 1 or not 0 < self.trade_tail_fraction <= 1 or not 0 < self.session_tail_fraction <= 1:
            raise ValueError('Invalid empirical tail fraction')
        if isinstance(self.block_sessions,bool) or not isinstance(self.block_sessions,int) or self.block_sessions<1:
            raise ValueError('block_sessions must be a positive integer')
        if not 0 < self.block_tail_fraction <= 1:
            raise ValueError('Invalid block tail fraction')
        if self.session_profit_target <= 0:
            raise ValueError('Positive session profit target required')
        return self


def period_statistics(pnl, config):
    """Chronological blocks, with a short final block weighted by session count."""
    days,count=pnl.shape
    starts=np.arange(0,days,config.block_sessions)
    lengths=np.minimum(config.block_sessions,days-starts)
    means=np.add.reduceat(pnl,starts,axis=0)/lengths[:,None]
    order=np.argsort(means,axis=0,kind='stable')
    ranked=np.take_along_axis(means,order,axis=0)
    mass=lengths[order]
    tail_mass=config.block_tail_fraction*days
    weights=np.clip(tail_mass-(np.cumsum(mass,axis=0)-mass),0,mass)
    tail=(ranked*weights).sum(0)/tail_mass
    positive=np.maximum(pnl,0)
    total=positive.sum(0)
    shares=np.divide(positive,total,out=np.zeros_like(positive),where=total>0)
    # Dollars: gross positive session P&L times its Herfindahl concentration.
    concentration=(positive*shares).sum(0)
    return dict(block_tail_daily_profit=tail,session_concentration_dollars=concentration,
                profitable_block_fraction=(means>0).mean(0),
                block_count=np.full(count,len(starts),dtype=np.int64))


def fractional_tail(values, fraction, largest=False):
    """Exact tail sum/mean over the last axis, including fractional boundary."""
    x = np.sort(np.asarray(values, dtype=np.float64), axis=-1)
    if x.shape[-1] == 0 or not 0 < fraction <= 1 or not np.isfinite(x).all():
        raise ValueError('Finite nonempty tail required')
    if largest:
        x = x[..., ::-1]
    mass = fraction * x.shape[-1]
    weights = np.clip(mass - np.arange(x.shape[-1]), 0, 1)
    total = (x * weights).sum(-1)
    return total, total / mass


def episode_statistics(profits, config):
    """Segmented NumPy sorting/reduction: no Python loop over trades."""
    sizes = np.fromiter(map(len, profits), dtype=np.int64, count=len(profits))
    count = len(sizes)
    values = np.concatenate(profits).astype(np.float64) if sizes.sum() else np.empty(0)
    if not np.isfinite(values).all():
        raise ValueError('Nonfinite complete-position profit')
    owner = np.repeat(np.arange(count), sizes)
    order = np.lexsort((values, owner))
    values, owner = values[order], owner[order]
    starts = np.cumsum(sizes) - sizes
    local = np.arange(len(values)) - starts[owner]
    bottom_mass = config.trade_tail_fraction * sizes
    top_mass = config.top_trim_fraction * sizes
    bottom = np.clip(bottom_mass[owner] - local, 0, 1)
    top = np.clip(top_mass[owner] - (sizes[owner] - 1 - local), 0, 1)
    sums = lambda weights: np.bincount(owner, weights=values * weights, minlength=count)
    total = sums(np.ones(len(values)))
    mean = np.divide(sums(bottom), bottom_mass, out=np.zeros(count), where=bottom_mass > 0)
    return dict(closed_positions=sizes, total_pnl=total, trimmed_profit=total-sums(top),
                trade_tail_loss=np.maximum(0, -mean), trade_tail_mean=mean)


def score(ordered, complexity, config=Objective()):
    config.validate()
    pnl = np.asarray([m['net_pnl'] for m in ordered], dtype=np.float64)
    if pnl.ndim != 2 or len(pnl) < 1 or not np.isfinite(pnl).all():
        raise ValueError('Finite complete session panel required')
    days, count = pnl.shape
    complexity = np.asarray(complexity, dtype=np.float64)
    if complexity.shape != (count,) or not np.isfinite(complexity).all() or (complexity < 0).any():
        raise ValueError('Invalid reachable complexity')
    profits = [np.concatenate([np.asarray(m['episode_profits'][i], dtype=np.float64) for m in ordered]) for i in range(count)]
    stats = episode_statistics(profits, config)
    if not np.allclose(stats['total_pnl'], pnl.sum(0), rtol=1e-10, atol=1e-7):
        raise ValueError('Complete-position profits do not reconcile to session P&L')
    closed = np.asarray([m['closed_positions'] for m in ordered])
    if not np.array_equal(stats['closed_positions'], closed.sum(0)):
        raise ValueError('Complete-position count mismatch')
    _, session_tail = fractional_tail(pnl.T, config.session_tail_fraction)
    periods=period_statistics(pnl,config)
    sums = lambda key: np.asarray([m[key] for m in ordered], dtype=np.float64).sum(0)
    components = dict(trimmed_profit=stats['trimmed_profit'],
        trade_tail_penalty=config.trade_tail_weight*stats['closed_positions']*stats['trade_tail_loss'],
        session_tail_reward=config.session_tail_weight*days*session_tail,
        drawdown_penalty=config.drawdown_weight*sums('drawdown'),
        stop_risk_penalty=config.risk_weight*sums('stop_risk_dollar_seconds')/3600,
        capital_time_penalty=config.exposure_weight*sums('capital_dollar_seconds')/3600,
        complexity_penalty=config.complexity_dollars_per_node*complexity,
        shortfall_penalty=config.shortfall_weight*(np.maximum(config.session_profit_target-pnl,0)**2/config.session_profit_target).sum(0),
        concentration_penalty=config.concentration_weight*periods['session_concentration_dollars'])
    if any(not np.isfinite(v).all() for v in components.values()):
        raise ValueError('Nonfinite objective component')
    value = components['trimmed_profit']+components['session_tail_reward']-sum(v for k,v in components.items() if k.endswith('penalty'))
    feasible = np.asarray([m['terminal_valid'] for m in ordered], dtype=bool).all(0)
    # Empty strategies have no empirical distribution and cannot be parents.
    feasible &= stats['closed_positions'] > 0
    active = (closed > 0).sum(0)
    return dict(**stats, **periods, score=value, cost=-value, feasible=feasible, components=components,
        best_day_pnl=pnl.max(0), other_days_pnl=pnl.sum(0)-pnl.max(0),
        session_tail_mean_pnl=session_tail, profitable_day_fraction=(pnl>0).mean(0),
        active_sessions=active, zero_trade_sessions=days-active,
        profitable_active_fraction=np.divide((pnl>0).sum(0),active,out=np.zeros(count),where=active>0),
        session_shortfall=np.maximum(config.session_profit_target-pnl,0).sum(0),
        target_session_fraction=(pnl>=config.session_profit_target).mean(0),
        complexity=complexity)


def session_scores(ordered, config=Objective()):
    """Vectorized session x candidate selection authority; no heldout inputs."""
    config.validate()
    panel=lambda key: np.asarray([m[key] for m in ordered],dtype=np.float64)
    pnl=panel('net_pnl')
    value=pnl-config.drawdown_weight*panel('drawdown')-config.risk_weight*panel('stop_risk_dollar_seconds')/3600-config.exposure_weight*panel('capital_dollar_seconds')/3600
    if value.ndim!=2 or not value.size or not np.isfinite(value).all():raise ValueError('Finite session score panel required')
    # Exclude the current session; a single-session panel has no contribution.
    if len(pnl)>1:value=value+config.other_session_profit_weight*(pnl.sum(0,keepdims=True)-pnl)/(len(pnl)-1)
    feasible=np.asarray([m['terminal_valid'] for m in ordered],dtype=bool).all(0)
    eligible=(panel('closed_positions')>0)&feasible[None,:]
    return value,eligible
