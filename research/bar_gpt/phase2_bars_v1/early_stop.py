"""Validation patience gated by frozen session-publication traversal coverage."""
import math
import numpy as np


def contract(args):
    return dict(version='session-publication-patience-v1',patience=args.patience,
        minimum_pass_fraction=getattr(args,'stop_min_session_fraction',.10),
        stale_pass_fraction=getattr(args,'stop_stale_session_fraction',.02),
        unit='completed training publication slots, including repeated ticker-days across plans',
        stop_rule='ALL gates required; strict validation composite improvement resets patience')


class SessionGuard:
    def __init__(self,units,args):
        self.policy=contract(args)
        if (self.policy['patience']<1 or any(not math.isfinite(self.policy[k]) or not 0<self.policy[k]<=1
                for k in ('minimum_pass_fraction','stale_pass_fraction'))):
            raise ValueError('Invalid session stopping policy')
        if hasattr(units,'groups'):
            counts=np.concatenate([(t['unit_split'].to_numpy()==0).astype(np.int64)
                                    for _,_,t in units.groups])
        else:
            counts=np.array([int(any(b[1]==0 for b in u.blocks)) for u in units])
        self.prefix=np.concatenate(([0],np.cumsum(counts)))
        self.total=int(self.prefix[-1])
        if self.total<1:raise ValueError('No training session publications')

    def review(self,*,epoch,cursor,exhausted,history,stale):
        if not 0<=cursor[0]<len(self.prefix)-1:raise ValueError('Invalid session cursor')
        seen=epoch*self.total+(self.total if exhausted else int(self.prefix[cursor[0]]))
        best_index=min(range(len(history)),key=lambda i:history[i]['validation']['mse'])
        if best_index==len(history)-1:best_seen=seen
        else:
            previous=history[best_index].get('learning_guard')
            if previous is None or any(previous.get(k)!=v for k,v in self.policy.items()):
                raise ValueError('Session guard requires explicit policy-bound history; no implicit migration')
            best_seen=previous['session_publications_seen']
        if seen<best_seen:raise ValueError('Session coverage moved backwards')
        minimum=math.ceil(self.total*self.policy['minimum_pass_fraction'])
        stale_minimum=math.ceil(self.total*self.policy['stale_pass_fraction'])
        stop=(stale>=self.policy['patience'] and seen>=minimum
              and seen-best_seen>=stale_minimum)
        return dict(**self.policy,stop=stop,session_publications_seen=seen,
            planned_session_publications=self.total,session_publications_since_best=seen-best_seen,
            minimum_session_publications=minimum,minimum_stale_session_publications=stale_minimum,
            stale_validations=stale)
