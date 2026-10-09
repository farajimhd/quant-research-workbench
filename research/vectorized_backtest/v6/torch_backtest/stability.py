"""Fixed stability objective; all daily accounts start independently."""
from dataclasses import dataclass,asdict
import math
import torch

@dataclass(frozen=True)
class Objective:
    median_weight:float=.5
    ex_best_weight:float=.5
    cvar_weight:float=.25
    tail_fraction:float=.2
    drawdown_weight:float=.25
    stop_risk_weight:float=.10
    capital_time_weight:float=.002
    complexity_weight:float=.001
    maximum_nodes:int=32
    minimum_batches:int=0
    maximum_batches:int=2147483647
    require_positive_ex_best:bool=False
    def validate(self):
        if any(not math.isfinite(v) or v<0 for k,v in asdict(self).items() if isinstance(v,float)) or not 0<self.tail_fraction<=1 or self.maximum_nodes<1 or self.minimum_batches<0 or self.maximum_batches<self.minimum_batches:
            raise ValueError('Invalid immutable stability objective')
        return self

@dataclass(frozen=True)
class DollarObjective(Objective):
    median_weight:float=0.
    ex_best_weight:float=0.
    inactivity_weight:float=.001
    inactive_removal_fraction:float=.5
    def validate(self):
        super().validate()
        if not 0<=self.inactive_removal_fraction<1: raise ValueError('Invalid inactive removal fraction')
        return self


@dataclass(frozen=True)
class LowerTailDollarObjective(DollarObjective):
    """Frozen lower-tail profit reward, replacing the loss-only tail cost."""
    tail_profit_weight:float=.25


def score(pnl,drawdown,risk_dollar_seconds,capital_dollar_seconds,batches,terminal_valid,complexity,*,initial_cash=10000.,config=Objective(),inactivity=None):
    config.validate()
    arrays=(pnl,drawdown,risk_dollar_seconds,capital_dollar_seconds,batches)
    if pnl.ndim!=2 or len(pnl)<2 or any(v.shape!=pnl.shape for v in arrays) or terminal_valid.shape!=pnl.shape or complexity.shape!=(pnl.shape[1],) or initial_cash<=0:
        raise ValueError('Require finite all-session metrics, [session,candidate]')
    finite=torch.stack([torch.isfinite(v).all(0) for v in arrays]).all(0)&torch.isfinite(complexity)
    pnl,drawdown,risk_dollar_seconds,capital_dollar_seconds,batches=[torch.where(finite[None],v,0.) for v in arrays]
    complexity=torch.where(finite,complexity,0.)
    r=pnl/initial_cash; ordered=r.sort(0).values
    middle=len(r)//2
    median=ordered[middle] if len(r)%2 else (ordered[middle-1]+ordered[middle])/2
    ex_best=(r.sum(0)-r.amax(0))/(len(r)-1)
    k=max(1,math.ceil(len(r)*config.tail_fraction))
    tail=(-ordered[:k]).clamp_min(0).mean(0)
    components=dict(median_reward=config.median_weight*median,ex_best_reward=config.ex_best_weight*ex_best,
        tail_penalty=config.cvar_weight*tail,drawdown_penalty=config.drawdown_weight*(drawdown/initial_cash).mean(0),
        stop_risk_penalty=config.stop_risk_weight*(risk_dollar_seconds/(initial_cash*3600)).mean(0),
        capital_time_penalty=config.capital_time_weight*(capital_dollar_seconds/(initial_cash*3600)).mean(0),
        complexity_penalty=config.complexity_weight*complexity/config.maximum_nodes)
    if isinstance(config,DollarObjective):
        if inactivity is None or inactivity.shape!=complexity.shape or not torch.isfinite(inactivity).all() or ((inactivity<0)|(inactivity>1)).any():
            raise ValueError('Bound elapsed inactivity required')
        scale=len(r)*initial_cash
        components=dict(total_profit=pnl.sum(0),
            tail_penalty=config.cvar_weight*tail*scale,
            drawdown_penalty=config.drawdown_weight*drawdown.sum(0),
            stop_risk_penalty=config.stop_risk_weight*risk_dollar_seconds.sum(0)/3600,
            capital_time_penalty=config.capital_time_weight*capital_dollar_seconds.sum(0)/3600,
            complexity_penalty=config.complexity_weight*complexity/config.maximum_nodes*scale,
            inactivity_penalty=config.inactivity_weight*inactivity*scale)
        if isinstance(config,LowerTailDollarObjective):
            components.pop('tail_penalty')
            components['lower_tail_profit_reward']=config.tail_profit_weight*ordered[:k].mean(0)*scale
    objective=(components['total_profit'] if isinstance(config,DollarObjective) else components['median_reward']+components['ex_best_reward'])-sum(v for k,v in components.items() if k.endswith('penalty'))
    if isinstance(config,LowerTailDollarObjective):objective+=components['lower_tail_profit_reward']
    missing_activity=(batches<config.minimum_batches).sum(0)
    excess_activity=(batches>config.maximum_batches).sum(0)
    nonflat=(~terminal_valid.bool()).sum(0)
    feasible=finite&(nonflat==0)&(missing_activity==0)&(excess_activity==0)
    if config.require_positive_ex_best:feasible&=ex_best>0
    concentration_violation=torch.where(ex_best<=0,1+(-ex_best).clamp_min(0),0.) if config.require_positive_ex_best else torch.zeros_like(ex_best)
    violation=missing_activity+excess_activity+nonflat+concentration_violation
    # Finite worst-score sentinel is JSON-safe; invalid lanes are never parents.
    objective=torch.where(feasible,objective,torch.finfo(objective.dtype).min)
    return dict(score=objective,feasible=feasible,components=components,median_return=median,ex_best_return=ex_best,
        profitable_day_fraction=(pnl>0).to(r.dtype).mean(0),tail_loss=tail,total_pnl=pnl.sum(0),best_day_pnl=pnl.amax(0),
        other_days_pnl=pnl.sum(0)-pnl.amax(0),violation=violation,
        session_tail_mean_pnl=ordered[:k].mean(0)*initial_cash,session_tail_count=torch.full_like(median,k),
        violations=dict(missing_activity_days=missing_activity,excess_activity_days=excess_activity,nonflat_days=nonflat,concentration=concentration_violation))
