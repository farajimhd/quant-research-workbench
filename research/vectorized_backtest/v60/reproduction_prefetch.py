"""Prepare deterministic fresh proposals while the current population replays."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import numpy as np
from research.vectorized_backtest.v60.schedule import generation_root,Proposal


class FreshPrefetch:
    def __init__(self,rng,generation,count,probe):
        clone=np.random.default_rng();clone.bit_generator.state=deepcopy(rng.bit_generator.state)
        self.root=generation_root(clone);self.generation=generation;self.count=count
        self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='v57-fresh-prefetch')
        def prepare():
            from .initial_population import immigrant
            from .validation_cache import validation_scope
            with validation_scope(capacity=32768):
                return [immigrant(Proposal(self.root,(generation,i,'immigrant',0)),probe) for i in range(count)]
        self.future=self.pool.submit(prepare)

    def get(self,root,generation,slot,attempt):
        if root!=self.root or generation!=self.generation:raise ValueError('Prefetched reproduction RNG identity changed')
        if attempt or slot>=self.count:return None
        return self.future.result()[slot]

    def close(self):self.pool.shutdown(wait=True,cancel_futures=True)
