"""One immutable decode/pack transaction per population, overlapped with I/O."""
from concurrent.futures import ThreadPoolExecutor
from time import perf_counter

class PopulationPreparation:
    def __init__(self):
        self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='v42-population-pack')
        self.payload=None;self.token=None;self.future=None;self.hits=0;self.misses=0;self.parent=None;self.subset_hits=0
    def submit(self,payload,token):
        if self.token==token and self.future is not None:
            self.hits+=1;return self.future
        self.misses+=1;self.parent=None;self.payload=payload;self.token=token
        self.future=self.pool.submit(prepare,payload,token);return self.future
    def subset(self,payload,token,parent_token,selection):
        from research.vectorized_backtest.v60.identity import digest
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
    def submit_members(self,members,token):
        """Pack the already validated immutable successor without decoding JSON."""
        payload=[m.payload() for m in members]
        from .identity import digest
        if digest(payload)!=token:raise ValueError('Successor preparation identity changed')
        self.parent=None;self.payload=payload;self.token=token;self.misses+=1
        def pack():
            from .lineage import program_key
            from .network_pack import pack_networks
            from .validation_cache import validation_scope
            started=perf_counter()
            with validation_scope(capacity=min(32768,max(4096,len(members)*15))):
                return members,[program_key(m) for m in members],pack_networks(members),perf_counter()-started
        self.future=self.pool.submit(pack);return self.future
    def submit_snapshot(self,snapshot,keys=None,validated=()):
        """Snapshot checked once; reuse its canonical token and typed members."""
        snapshot.verify(snapshot.members)
        if self.token==snapshot.token and self.future is not None:return self.future
        self.parent=None;self.payload=snapshot.payload;self.token=snapshot.token;self.misses+=1
        def pack():
            from .lineage import program_key
            from .network_pack import pack_networks
            from .validation_cache import validation_scope
            started=perf_counter()
            with validation_scope(capacity=32768,certificate=validated):
                members=list(snapshot.members)
                return members,list(keys) if keys is not None else [program_key(m) for m in members],pack_networks(members),perf_counter()-started
        self.future=self.pool.submit(pack);return self.future
    def append_members(self,parent_token,payload,extra,token,snapshot=None):
        if self.token!=parent_token or self.future is None:return False
        source=self.future;parent_payload=self.payload
        def append():
            from .identity import digest
            from .network_pack import pack_networks,concatenate_packed
            from .lineage import program_key
            if snapshot is not None:
                snapshot.verify(snapshot.members);n=len(snapshot.payload)
                if snapshot.token!=parent_token or len(payload)!=n+len(extra) or any(a is not b for a,b in zip(payload[:n],snapshot.payload)) or payload[n:]!=[m.payload() for m in extra]:
                    raise ValueError('Paired snapshot identity mismatch')
            elif digest(payload)!=token or payload!=parent_payload+[m.payload() for m in extra]:raise ValueError('Paired preparation identity mismatch')
            members,keys,packed,_=source.result();started=perf_counter()
            return members+extra,keys+[program_key(m) for m in extra],concatenate_packed(packed,pack_networks(extra)),perf_counter()-started
        self.payload=payload;self.token=token;self.parent=None;self.future=self.pool.submit(append);return True
    def close(self):self.pool.shutdown(wait=True,cancel_futures=True)

def prepare(payload,token):
    from research.vectorized_backtest.v60.identity import digest
    from .genome import Individual
    from .lineage import program_key
    from .network_pack import pack_networks
    from .validation_cache import validation_scope
    started=perf_counter()
    if digest(payload)!=token:raise ValueError('Immutable population token mismatch')
    # Keep one bounded cohort resident through decode, identity, and packing.
    # 1024 candidates contain 5120 networks; the default 4096 FIFO thrashes.
    from .genome import STAGES
    with validation_scope(capacity=min(32768,max(4096,len(payload)*len(STAGES)))):
        population=[Individual.restore(p) for p in payload]
        keys=[program_key(p) for p in population]
        packed=pack_networks(population)
    return population,keys,packed,perf_counter()-started
