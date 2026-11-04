"""Exact frozen-policy replay acceleration across flat, unscheduled WAIT clocks."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import sys
sys.dont_write_bytecode=True
from pathlib import Path
if __package__ in (None,''):sys.path.insert(0,str(Path(__file__).resolve().parents[4]))
import argparse
import time
import numpy as np
from research.rl_trading.v7.experiments import entry_replay as original

RULE='Skip only flat, nonpending, unscheduled WAIT clocks to next sampled clock or decision_len-2; original step for actions, all held seconds and terminal'


def replay_session(session,scorer,config=original.Config(),notify=None):
    if hasattr(scorer,'for_session'):scorer=scorer.for_session(session)
    env=original.CostEnvironment(session,config)
    scheduled=np.asarray(sorted(set(original.sampled_seconds(session.day)[::scorer.row_step].tolist())),dtype=np.int64)
    schedule=set(scheduled.tolist())
    terminal=min(len(session.selection)-2,len(session.clocks)-2)
    fills,decisions=[],[];last=time.monotonic()
    while not env.done:
        if not env.quantity and not env.pending_exit and env.t not in schedule and env.t<terminal:
            index=int(np.searchsorted(scheduled,env.t,side='left'))
            destination=min(int(scheduled[index]) if index<len(scheduled) else terminal,terminal)
            if destination>env.t:
                # Flat equity is constant; repeated peak/drawdown updates are idempotent.
                env.peak=max(env.peak,env.cash)
                env.drawdown=max(env.drawdown,1-env.cash/env.peak)
                env.t=destination
                env.last_fills=[]
        action=0
        if not env.quantity and env.t in schedule:
            obs=env.observe();scores=scorer.score([obs])[0];allowed=obs['action_mask'][1:6]
            if scores.shape!=(5,) or not np.isfinite(scores[allowed]).all():
                raise ValueError('Invalid eligible entry score')
            ranked=np.where(allowed,scores,-np.inf);slot=int(np.argmax(ranked))
            if ranked[slot]>0:action=slot+1
            decisions.append(dict(second=env.t,action=action,
                scores=[float(v) if np.isfinite(v) else None for v in scores],allowed=allowed.tolist(),cash=env.cash))
        env.step(action)
        fills.extend(env.last_fills)
        if notify and time.monotonic()-last>=20:
            notify(dict(second=env.t,entries=env.entries));last=time.monotonic()
    return dict(env.result(),fill_records=fills,decisions=decisions)


class BoundAuthority:
    def __init__(self,scorer,recipe):
        self.scorer=scorer;self.row_step=scorer.row_step
        self.binding=dict(scorer=scorer.binding,acceleration=dict(source=original.sha(Path(__file__)),rule=RULE,recipe=recipe))

    def for_session(self,session):
        return self.scorer.for_session(session) if hasattr(self.scorer,'for_session') else self.scorer


def loader(recipe):
    if recipe=='weighted':
        from research.rl_trading.v7.experiments.temporal_weighted_policy import load_bundle
    elif recipe=='tree':
        from research.rl_trading.v7.experiments.entry_tree_policy import load_bundle
    elif recipe=='temporal-tree':
        from research.rl_trading.v7.experiments.entry_temporal_tree import load_bundle
    else:raise ValueError('Unknown frozen recipe')
    return load_bundle


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__,add_help=False)
    parser.add_argument('--recipe',choices=('weighted','tree','temporal-tree'),required=True)
    args,remaining=parser.parse_known_args(argv)
    selected=loader(args.recipe)
    def bound_loader(*a,**kw):return BoundAuthority(selected(*a,**kw),args.recipe)
    previous=original.replay_session
    original.replay_session=replay_session
    try:return original.main(remaining,bundle_loader=bound_loader)
    finally:original.replay_session=previous


if __name__=='__main__':raise SystemExit(main())
