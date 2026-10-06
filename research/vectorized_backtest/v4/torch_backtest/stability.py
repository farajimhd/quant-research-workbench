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
    minimum_batches:int=1
    maximum_batches:int=20
    require_positive_ex_best:bool=True
    def validate(self):
        if any(not math.isfinite(v) or v<0 for k,v in asdict(self).items() if isinstance(v,float)) or not 0<self.tail_fraction<=1 or self.maximum_nodes<1 or self.minimum_batches<0 or self.maximum_batches<self.minimum_batches:
            raise ValueError('Invalid immutable stability objective')
        return self

def score(pnl,drawdown,risk_dollar_seconds,capital_dollar_seconds,batches,terminal_valid,complexity,*,initial_cash=10000.,config=Objective()):
    config.validate()
    arrays=(pnl,drawdown,risk_dollar_seconds,capital_dollar_seconds,batches)
    if pnl.ndim!=2 or len(pnl)<2 or any(v.shape!=pnl.shape or not torch.isfinite(v).all() for v in arrays) or terminal_valid.shape!=pnl.shape or complexity.shape!=(pnl.shape[1],) or initial_cash<=0:
        raise ValueError('Require finite all-session metrics, [session,candidate]')
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
    objective=components['median_reward']+components['ex_best_reward']-sum(v for k,v in components.items() if k.endswith('penalty'))
    missing_activity=(batches<config.minimum_batches).sum(0)
    excess_activity=(batches>config.maximum_batches).sum(0)
    nonflat=(~terminal_valid.bool()).sum(0)
    feasible=(nonflat==0)&(missing_activity==0)&(excess_activity==0)
    if config.require_positive_ex_best:feasible&=ex_best>0
    concentration_violation=torch.where(ex_best<=0,1+(-ex_best).clamp_min(0),0.) if config.require_positive_ex_best else torch.zeros_like(ex_best)
    violation=missing_activity+excess_activity+nonflat+concentration_violation
    return dict(score=objective,feasible=feasible,components=components,median_return=median,ex_best_return=ex_best,
        profitable_day_fraction=(pnl>0).to(r.dtype).mean(0),tail_loss=tail,total_pnl=pnl.sum(0),best_day_pnl=pnl.amax(0),
        other_days_pnl=pnl.sum(0)-pnl.amax(0),violation=violation,
        violations=dict(missing_activity_days=missing_activity,excess_activity_days=excess_activity,nonflat_days=nonflat,concentration=concentration_violation))
