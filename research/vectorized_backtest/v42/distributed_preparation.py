"""One immutable decode/pack transaction per population, overlapped with I/O."""
from concurrent.futures import ThreadPoolExecutor
from time import perf_counter

class PopulationPreparation:
    def __init__(self):
        self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='v42-population-pack')
        self.payload=None;self.token=None;self.future=None;self.hits=0;self.misses=0;self.parent=None;self.subset_hits=0
    def submit(self,payload,token):
        if self.payload is payload and self.token==token:
            self.hits+=1;return self.future
        self.misses+=1;self.parent=None;self.payload=payload;self.token=token
        self.future=self.pool.submit(prepare,payload,token);return self.future
    def subset(self,payload,token,parent_token,selection):
        from .run_search import digest
        if digest(payload)!=token:raise ValueError('Subset population token mismatch')
        source=self.future if self.token==parent_token else self.parent[1] if self.parent and self.parent[0]==parent_token else None
        if source is None:return False
        self.parent=(parent_token,source)
        self.payload=payload;self.token=token;self.subset_hits+=1
        def gather():
            from .network_pack import select_packed
            population,keys,packed,_=source.result()
            members=[population[i] for i in selection]
            if digest([m.payload() for m in members])!=token:raise ValueError('Training/validation genome alignment changed')
            return members,[keys[i] for i in selection],select_packed(packed,selection),0.
        self.future=self.pool.submit(gather);return True
    def close(self):self.pool.shutdown(wait=True,cancel_futures=True)

def prepare(payload,token):
    from .run_search import digest
    from .genome import Individual
    from .lineage import program_key
    from .network_pack import pack_networks
    from .validation_cache import validation_scope
    started=perf_counter()
    if digest(payload)!=token:raise ValueError('Immutable population token mismatch')
    with validation_scope():
        population=[Individual.restore(p) for p in payload]
        keys=[program_key(p) for p in population]
        packed=pack_networks(population)
    return population,keys,packed,perf_counter()-started
