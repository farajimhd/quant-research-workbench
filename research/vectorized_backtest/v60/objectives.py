"""One session-wide cost; equity drawdown includes open positions."""
from dataclasses import dataclass
import math
import numpy as np
from . import base_objectives as base

@dataclass(frozen=True)
class Objective:
    drawdown_weight:float=1.
    loss_weight:float=0.
    def validate(self):
        if any(not math.isfinite(v) or v<0 for v in (self.drawdown_weight,self.loss_weight)):raise ValueError('Finite nonnegative cost weights required')
        return self

def local_panel(ordered,config=Objective(),*,case_maps=None):
    config.validate()
    if len(ordered)!=1:raise ValueError('Local fitness requires exactly one complete session')
    result=base.local_panel(ordered,base.Objective(ticker_trim_fraction=0,session_trim_fraction=0,concentration_weight=0))
    metrics=ordered[0];n=len(metrics['net_pnl']);loss=np.zeros(n)
    if any((np.asarray(metrics[k],float)<0).any() for k in ('maximum_position_risk','maximum_stop_distance')):raise ValueError('Invalid risk diagnostics')
    result['eligible']=np.asarray([metrics['terminal_valid']],dtype=bool)
    for i in range(n):
        v=np.asarray(metrics['episode_profits'][i],float)
        if len(v)!=metrics['closed_positions'][i] or not np.isfinite(v).all():raise ValueError('Complete closed-position ledger required')
        loss[i]=np.maximum(-v,0).sum()
    dd=np.asarray(metrics['drawdown'],float)
    from .full_common import aggregate
    panel=aggregate(ordered,config);score=panel['score']
    result.update(selection_score=score,values=score[None],gross_losses=loss,session_equity_drawdown=dd,
        case_ids=['SESSION'],case_scores=score[None],specialist=score,specialist_targets=['SESSION']*n)
    return result

def isolate_panel(*args,**kwargs):raise ValueError('V60 has no phase0 or ticker-isolated objective')
