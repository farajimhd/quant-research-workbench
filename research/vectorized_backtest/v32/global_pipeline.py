"""Generation-scoped immutable preparation and independent session telemetry."""
from collections import OrderedDict
from math import ceil
from threading import RLock
from time import perf_counter


class GenerationPopulation:
    """Share read-only genotypes and exact selection packing until the barrier.

    Session-specific cache hits still select their original ordered candidates.
    The bounded memo caches recomputable CPU arrays, never financial results.
    """
    def __init__(self,members,maximum_batches=4):
        from .lineage import program_key
        started=perf_counter()
        self.members=tuple(members);self.keys=tuple(program_key(m) for m in members)
        self.maximum_batches=maximum_batches;self.batches=OrderedDict()
        self.hits=0;self.misses=0;self.seconds=perf_counter()-started

    def pack(self,selection,submit):
        from .preparation import _pack
        key=tuple(selection)
        if key in self.batches:
            self.hits+=1;self.batches.move_to_end(key)
            return self.batches[key]
        self.misses+=1
        future=submit(_pack,[self.members[i] for i in key])
        self.batches[key]=future
        # Drop only this memo's reference, never cancel a consumer's future.
        # Queue admission independently bounds pending computations.
        while len(self.batches)>self.maximum_batches:
            self.batches.popitem(last=False)
        return future


class IndependentProgress:
    """Keep the existing two-active-session panel without cohort barriers."""
    def __init__(self,panel,days,population,batch):
        self.panel=panel;self.order={day:i for i,day in enumerate(days)}
        self.total=len(days);self.batches=ceil(population/batch)
        self.active={};self.completed=0;self.finished_batches=0
        self.started=perf_counter();self.lock=RLock();self.last={}

    def publish(self):
        if not hasattr(self.panel,'update'):return
        paths=dict(sorted(self.active.items(),key=lambda row:self.order[row[0]]))
        self.panel.update(stage='Concurrent session rules / backtests',
            focus=', '.join(paths),session_paths=paths or self.last,
            completed_sessions=self.completed,
            completed_batches=self.finished_batches+sum(v.get('completed_local_batches',0) for v in paths.values()),
            active_sessions=len(paths),queued_sessions=self.total-self.completed-len(paths))

    def begin(self,day):
        with self.lock:
            self.active[day]=dict(day=day,stage='Queued',completed_local_batches=0,candidate_offset=0,
                rule_tiles=0,total_rule_tiles=0,completed_seconds=0,total_seconds=0)
            self.publish()

    def event(self,event):
        with self.lock:
            lane=self.active.get(event['day'])
            if lane is None:return
            if event.get('candidate_offset',lane.get('candidate_offset',0))<lane.get('candidate_offset',0):return
            lane.update(event);self.publish()

    def finish(self,day,result):
        with self.lock:
            row=self.active.pop(day);row.update(stage='Session complete',
                completed_local_batches=len(result['timings']),completed_seconds=result['clocks'],total_seconds=result['clocks'])
            self.last={day:row};self.completed+=1;self.finished_batches+=self.batches
            self.publish()
            if hasattr(self.panel,'samples'):
                # Aggregate wall throughput includes both lanes and their waits.
                self.panel.samples.append((perf_counter()-self.started)/self.finished_batches)
