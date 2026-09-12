"""Atomic immutable campaign artifacts with bounded Windows/SMB retries."""
import gzip,json,os,time,uuid
from pathlib import Path
from src.market_engine.level_book_store import read as _read
from src.market_engine.historical_level_checkpoint import digest

def read(path):
    deadline=time.monotonic()+5
    while True:
        try:return _read(path)
        except PermissionError:
            if time.monotonic()>=deadline:raise
            time.sleep(.05)

def write(path,value,*,immutable=True):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    if immutable and path.exists():
        if read(path)!=value:raise ValueError(f'Immutable campaign artifact differs: {path}')
        return
    raw=json.dumps(value,separators=(',',':'),allow_nan=False).encode()
    if path.suffix=='.gz':raw=gzip.compress(raw,compresslevel=1,mtime=0)
    temp=path.with_name('.'+path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temp.open('xb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
        deadline=time.monotonic()+5
        while True:
            try:temp.replace(path);break
            except PermissionError:
                if time.monotonic()>=deadline:raise
                time.sleep(.05)
    finally:temp.unlink(missing_ok=True)

def verified_book(path):
    b=read(path)
    if b.get('checkpoint_hash')!=digest({k:v for k,v in b.items() if k!='checkpoint_hash'}):raise ValueError('Checkpoint integrity mismatch')
    return b
