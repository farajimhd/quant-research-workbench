"""Bounded process-owned publication, ordered seals, no CUDA or selection access."""
import json,time,multiprocessing
import threading
from pathlib import Path
from hashlib import sha256
from concurrent.futures import ProcessPoolExecutor
from .population_snapshot import envelope
from research.vectorized_backtest.v42.publication import write_json,_write_text
from research.vectorized_backtest.v42.distributed_assets import sha


class Buffer:
    """Capture frozen result references for the bounded generation transaction."""
    def __init__(self):self.writes=[]
    def write(self,path,value):self.writes.append((str(path),value))
    def __enter__(self):return self
    def __exit__(self,*args):return False


def start_generation(folder,raw,token,ids,records):
    folder=Path(folder)
    _write_text(folder/'population.json',envelope(dict(ids=ids,population_sha256=token),population=raw))
    write_json(folder/'evaluated-pedigree.json',records)


def resolve(value,hashes):
    if isinstance(value,dict):
        if value.get('receipt') in hashes:
            key='receipt_sha256' if 'receipt_sha256' in value else 'sha256'
            value[key]=hashes[value['receipt']]
        for v in value.values():resolve(v,hashes)
    elif isinstance(value,list):
        for v in value:resolve(v,hashes)


def publish_replays(writes):
    hashes={}
    # Results are passed directly from replay memory. The writer never replays.
    for path,value in writes:
        if 'results' in value:
            raw=__import__('research.vectorized_backtest.v57.population_snapshot',fromlist=['dumps']).dumps(value['results'])
            value=dict(value,results_sha256=sha256(raw.encode()).hexdigest())
            _write_text(path,envelope({k:v for k,v in value.items() if k!='results'},results=raw))
        else:
            resolve(value,hashes);write_json(path,value)
        hashes[str(path)]=sha(path)
    return hashes


def finish_generation(packet):
    p=packet;root=Path(p['root']);folder=Path(p['folder'])
    hashes=publish_replays(p['replay_writes'])
    # Multi-session replay receipts were streamed through this same ordered lane.
    for path in p.get('published_receipts',[]):hashes[path]=sha(path)
    for path,value in p['artifacts']:
        resolve(value,hashes);write_json(path,value)
    frozen=p['frozen_raw'];frozen_hash=sha256(frozen.encode()).hexdigest()
    if frozen_hash!=p['frozen_hash']:raise ValueError('Frozen successor publication changed')
    # File key ordering is canonical as before, not insertion dependent.
    _write_text(folder/'successor.json',envelope(dict(ids=p['state']['ids'],rng=p['state']['rng'],sha256=frozen_hash,training_only=True),population=p['successor_raw']))
    from .evolution_report import publish,expose
    publish(root,folder,*p['report'])
    resolve(p['validation'],hashes);write_json(folder/'validation.json',p['validation'])
    mapping={'training.json':'training_sha256','validation.json':'validation_sha256','successor.json':'successor_sha256',
             'population.json':'population_sha256','reproduction.json':'reproduction_sha256','births.json':'births_sha256',
             'evaluated-pedigree.json':'evaluated_pedigree_sha256','offspring-audit.json':'offspring_audit_sha256'}
    seal={key:sha(folder/name) for name,key in mapping.items()}
    seal.update(observational_reports={name:sha(folder/name) for name in ('evolution-report.json','founder-results.json','session-metrics.json','transition-timings.json') if (folder/name).exists()},
                mutation_probe_sha256=sha(folder/'mutation-probe.json') if (folder/'mutation-probe.json').exists() else None,
                source_sha256=p['source'],validation_independent=True,wall_seconds=time.perf_counter()-p['started'])
    write_json(folder/'complete.json',seal);seal_hash=sha(folder/'complete.json')
    cp=root/'checkpoint.json'
    previous=json.loads(cp.read_bytes())['state'] if cp.exists() else {'completed':0,'seals':{}}
    if previous['completed']!=p['generation']-1:raise ValueError('Publication generation order changed')
    state=dict(p['state'],completed=p['generation'],seals=dict(previous['seals'],**{str(p['generation']):seal_hash}))
    raw=envelope(state,population=p['successor_raw'])
    _write_text(cp,envelope(dict(contract=p['contract'],state_sha256=sha256(raw.encode()).hexdigest()),state=raw))
    expose(root,folder)
    elapsed=time.perf_counter()-p['started']
    write_json(folder/'generation-timings.json',dict(generation=p['generation'],seal_sha256=seal_hash,full_generation_seconds=elapsed,
               includes_checkpoint_and_observer_publication=True,publication_overlapped_next_replay=True,critical_path_seconds=p['critical_path_seconds']))
    return dict(generation=p['generation'],seal_sha256=seal_hash,full_generation_seconds=elapsed)


class Lane:
    def __init__(self):
        self.pool=ProcessPoolExecutor(max_workers=1,mp_context=multiprocessing.get_context('spawn'))
        self.pending=[]
        self.lock=threading.RLock()

    def submit(self,fn,*args):
        with self.lock:return self._submit(fn,*args)

    def _submit(self,fn,*args):
        # At most one generation finishing plus one next-generation start.
        while len(self.pending)>=2:self.pending.pop(0).result()
        self.check();future=self.pool.submit(fn,*args);self.pending.append(future);return future

    def check(self):
        with self.lock:
            for future in self.pending:
                if future.done():future.result()

    def close(self):
        try:
            for future in self.pending:future.result()
        finally:self.pool.shutdown(wait=True,cancel_futures=True)
