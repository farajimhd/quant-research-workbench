"""Bounded metric cadence at safe chronological processing boundaries."""
from time import monotonic


class PeriodicProgress:
    def __init__(self, emit, *, seconds=60., clock=monotonic):
        if seconds<=0:
            raise ValueError('Positive progress cadence required')
        self.emit,self.seconds,self.clock=emit,seconds,clock
        self.last=clock()

    def __call__(self, metrics):
        now=self.clock()
        if now-self.last>=self.seconds:
            self.emit(metrics)
            self.last=now
