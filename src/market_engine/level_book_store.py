"""Immutable compressed V7 artifacts, independent of prediction models."""
import gzip
import json
import os
from pathlib import Path
from .historical_level_checkpoint import digest

ROOT=Path(r'D:\TradingML\runtimes\level-book-v7')


def read(path):
    path=Path(path)
    with (gzip.open(path,'rt',encoding='utf-8') if path.suffix=='.gz' else path.open(encoding='utf-8')) as f:
        return json.load(f)


def write(path,value,*,immutable=True):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    if immutable and path.exists():
        if read(path)!=value:raise ValueError(f'Immutable artifact differs: {path}')
        return
    raw=json.dumps(value,separators=(',',':'),allow_nan=False).encode()
    if path.suffix=='.gz':raw=gzip.compress(raw,compresslevel=1,mtime=0)
    temp=path.with_suffix(path.suffix+'.tmp')
    with temp.open('wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
    temp.replace(path)


def verified_book(path):
    value=read(path)
    if value.get('checkpoint_hash')!=digest({k:v for k,v in value.items() if k!='checkpoint_hash'}):
        raise ValueError('Level book checkpoint hash mismatch')
    return value
