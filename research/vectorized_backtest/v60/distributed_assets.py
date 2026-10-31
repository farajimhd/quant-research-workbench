"""Verified local disk staging of immutable certified session artifacts."""
import hashlib,json,os,shutil,time,threading
from pathlib import Path
from .publication import write_json

CHUNK=16*1024**2
_locks={}
_guard=threading.Lock()

def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for b in iter(lambda:stream.read(CHUNK),b''):h.update(b)
    return h.hexdigest()

def remote(path,share):
    value=str(path).replace('\\','/')
    if not value.startswith('D:/TradingML/runtimes/'):raise ValueError('Only runtime artifacts may cross machines')
    return Path(share)/value[3:]

def copy_verified(source,target,expected):
    source,target=Path(source),Path(target)
    if target.exists():
        if sha(target)!=expected:raise ValueError('Existing artifact differs: '+str(target))
        return
    target.parent.mkdir(parents=True,exist_ok=True);temporary=target.with_name(target.name+'.'+str(os.getpid())+'.tmp')
    h=hashlib.sha256()
    try:
        with source.open('rb') as src,temporary.open('xb') as dst:
            for b in iter(lambda:src.read(CHUNK),b''):dst.write(b);h.update(b)
        if h.hexdigest()!=expected:raise ValueError('Transferred artifact differs: '+str(source))
        os.replace(temporary,target)
    finally:
        if temporary.exists():temporary.unlink()

def _ensure_day(job,node,role,day):
    start=time.perf_counter();items=job['assets'][role][day];marker=Path(job['campaign'])/'data-ready'/node/role/(day+'.json')
    if marker.exists():
        saved=json.loads(marker.read_bytes())
        if saved['asset_sha256']!=job['asset_sha256']:raise ValueError('Asset inventory changed')
        if all(Path(x['path']).exists() and Path(x['path']).stat().st_size==x['size'] and Path(x['path']).stat().st_mtime_ns==x['mtime_ns'] for x in saved['files']):return 0.
    records=[]
    missing=sum(x['size'] for x in items if not Path(x['path']).exists())
    if shutil.disk_usage(Path(job['campaign']).anchor).free<missing+64*2**30:raise MemoryError('64 GiB campaign disk reserve required before staging')
    for item in items:
        bus=Path(job['transport']) if node=='workstation' else remote(job['transport'],job['workstation_share'])
        if (bus/'STOP').exists():raise InterruptedError('Stopped during disk staging')
        target=Path(item['path'])
        if node=='workstation':
            if sha(target)!=item['sha256']:raise ValueError('Certified workstation artifact changed')
        else:copy_verified(remote(target,job['workstation_share']),target,item['sha256'])
        stat=target.stat();records.append(dict(path=str(target),size=stat.st_size,mtime_ns=stat.st_mtime_ns))
    marker.parent.mkdir(parents=True,exist_ok=True);write_json(marker,dict(asset_sha256=job['asset_sha256'],files=records))
    return time.perf_counter()-start

def ensure_day(job,node,role,day):
    with _guard:lock=_locks.setdefault((node,role,day),threading.Lock())
    with lock:return _ensure_day(job,node,role,day)

def receipt_inventory(job,node):
    """Bind every split certificate before admission without copying all arrays."""
    for days in job['assets'].values():
        for items in days.values():
            for item in items:
                if Path(item['path']).name=='complete.json' and node!='workstation':copy_verified(remote(item['path'],job['workstation_share']),item['path'],item['sha256'])
