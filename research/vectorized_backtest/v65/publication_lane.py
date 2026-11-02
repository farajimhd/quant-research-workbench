"""Bounded process-owned publication, ordered seals, no CUDA or selection access."""
import json,time,multiprocessing
import threading
from pathlib import Path
from hashlib import sha256
from concurrent.futures import ProcessPoolExecutor,ThreadPoolExecutor
from .population_snapshot import envelope
from research.vectorized_backtest.v65.publication import write_json,_write_text
from research.vectorized_backtest.v65.distributed_assets import sha


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


def publish_replays(writes,indexed=False):
    hashes={}
    indices=[]
    if indexed:
        receipts=[(path,value) for path,value in writes if isinstance(value,dict) and value.get('program_keys') and 'metrics' in value]
        keep=[]
        for path,value in writes:
            if 'results' not in value:keep.append((path,value));continue
            matches=[(p,r) for p,r in receipts if r.get('result_identity')==value['identity'] and set(r['program_keys'])==set(value['results'])]
            if not matches:raise ValueError('No complete bound receipt for result index')
            receipt_path,receipt=matches[0]
            indices.append((path,dict(format='v57-receipt-index-v1',identity=value['identity'],receipt=receipt_path)))
        writes=keep
    # Results are passed directly from replay memory. The writer never replays.
    for path,value in writes:
        if 'results' in value:
            raw=__import__('research.vectorized_backtest.v65.population_snapshot',fromlist=['dumps']).dumps(value['results'])
            value=dict(value,results_sha256=sha256(raw.encode()).hexdigest())
            _write_text(path,envelope({k:v for k,v in value.items() if k!='results'},results=raw))
        else:
            resolve(value,hashes);write_json(path,value)
        hashes[str(path)]=sha(path)
    for path,value in indices:
        write_json(path,dict(value,receipt_sha256=hashes[value['receipt']]))
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
    def __init__(self,replay_workers=1):
        if replay_workers not in (1,2,4):raise ValueError('Bounded receipt writers required')
        self.pool=ProcessPoolExecutor(max_workers=1,mp_context=multiprocessing.get_context('spawn'))
        self.replay_pools=[ProcessPoolExecutor(max_workers=1,mp_context=multiprocessing.get_context('spawn')) for _ in range(replay_workers)]
        self.seal_dispatch=ThreadPoolExecutor(max_workers=1,thread_name_prefix='v65-seal-dependencies')
        self.replay_serial=0
        self.main_pending=[];self.replay_pending=[];self.replay_capacity=3*replay_workers
        self.lock=threading.RLock()

    @property
    def pending(self):
        with self.lock:return self.main_pending+self.replay_pending

    def submit(self,fn,*args):
        with self.lock:return self._submit(fn,*args)

    def _submit(self,fn,*args):
        self.check()
        if fn is publish_replays:
            # Each complete session owns distinct receipt paths. Bound retained
            # IPC payloads; generation seals still use the single ordered lane.
            while len(self.replay_pending)>=self.replay_capacity:self.replay_pending.pop(0).result()
            # A session always uses the same FIFO writer, including its mutable
            # result-cache index. Later generations cannot be overwritten by an
            # earlier session write that happened to complete more slowly.
            key=next((str(Path(path).parent) for path,value in args[0] if Path(path).name=='receipt.json'),None) if args and isinstance(args[0],list) else None
            if key is not None:
                path=Path(key);key=str(path.parent.parent/path.name)
            index=int(sha256(key.encode()).hexdigest(),16) if key else self.replay_serial
            self.replay_serial+=1
            future=self.replay_pools[index%len(self.replay_pools)].submit(fn,*args);self.replay_pending.append(future);return future
        if fn is finish_generation:
            # Never seal or advance a checkpoint before every session receipt
            # exists and every writer failure has reached the controller.
            dependencies=tuple(self.replay_pending)
            self.replay_pending.clear()
            while len(self.main_pending)>=2:self.main_pending.pop(0).result()
            future=self.seal_dispatch.submit(self._finish,dependencies,fn,args)
            self.main_pending.append(future);return future
        # At most one generation finishing plus one next-generation start.
        while len(self.main_pending)>=2:self.main_pending.pop(0).result()
        future=self.pool.submit(fn,*args);self.main_pending.append(future);return future

    def _finish(self,dependencies,fn,args):
        for future in dependencies:future.result()
        return self.pool.submit(fn,*args).result()

    def check(self):
        with self.lock:
            for future in self.pending:
                if future.done():future.result()

    def close(self):
        try:
            for future in self.pending:future.result()
        finally:
            try:
                self.seal_dispatch.shutdown(wait=True,cancel_futures=False)
                for pool in self.replay_pools:pool.shutdown(wait=True,cancel_futures=True)
            finally:self.pool.shutdown(wait=True,cancel_futures=True)
